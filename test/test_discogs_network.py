# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""Discogs requests go through Picard's WebService; no live API access."""

import json
from unittest.mock import (
    MagicMock,
    patch,
)

from PyQt6 import QtCore
from PyQt6.QtNetwork import (
    QNetworkReply,
    QNetworkRequest,
)

from test.picardtestcase import PicardTestCase

from picard import discogsgenre
from picard.webservice import WebService


TOKEN = 'secret-discogs-token'
SEARCH = '/database/search'


def single_track_release(ident, *, artist='Artist', title='Song', genres=('Funk',), styles=('Disco',)):
    return {
        'id': ident, 'uri': 'https://www.discogs.com/release/%d' % ident,
        'artists': [{'name': artist}], 'tracklist': [{'type_': 'track', 'title': title}],
        'genres': list(genres), 'styles': list(styles),
    }


def search_results(*idents):
    return {'pagination': {'page': 1, 'pages': 1}, 'results': [{'id': i} for i in idents]}


class DiscogsGenreRequestTest(PicardTestCase):
    """DiscogsGenreRequest with a mocked WebService."""

    def setUp(self):
        super().setUp()
        self.parent = QtCore.QObject()
        self.addCleanup(self.parent.deleteLater)
        self.ws = MagicMock()
        self.ws.get_url.side_effect = lambda **kwargs: MagicMock(name='task-%d' % self.ws.get_url.call_count)
        self.results = []

    def make(self, responses=None):
        request = discogsgenre.DiscogsGenreRequest(
            self.parent, 'Artist', 'Song', TOKEN, webservice=self.ws, responses=responses,
        )
        request.finished.connect(lambda values, status: self.results.append((values, status)))
        return request

    def call(self, index=-1):
        return self.ws.get_url.call_args_list[index].kwargs

    def respond(self, document, error=None, index=-1):
        self.call(index)['handler'](document, None, error)

    def test_search_uses_picard_webservice_with_header_token(self):
        self.make().start()
        kwargs = self.call()
        self.assertEqual('https://api.discogs.com/database/search', kwargs['url'].toString())
        self.assertEqual(
            {'type': 'release', 'artist': 'Artist', 'track': 'Song', 'per_page': 5}, kwargs['unencoded_queryargs'],
        )
        self.assertEqual({'Authorization': 'Discogs token=' + TOKEN}, kwargs['headers'])
        self.assertEqual('json', kwargs['parse_response_type'])
        self.assertNotIn(TOKEN, kwargs['url'].toString())
        self.assertNotIn(TOKEN, str(kwargs['unencoded_queryargs']))

    def test_successful_response_verifies_single_track_release(self):
        request = self.make()
        request.start()
        self.respond(search_results(1, 2, 3))
        self.assertEqual('https://api.discogs.com/releases/1', self.call()['url'].toString())
        self.respond(single_track_release(1))
        self.assertEqual([(['Funk'], 'https://www.discogs.com/release/1')], self.results)
        self.assertEqual(['Disco'], request.styles)
        self.assertEqual(2, self.ws.get_url.call_count)

    def test_unverified_release_tries_the_next_candidate_only(self):
        self.make().start()
        self.respond(search_results(1, 2, 3))
        self.respond(single_track_release(1, title='Other'))
        self.assertEqual('https://api.discogs.com/releases/2', self.call()['url'].toString())
        self.respond(single_track_release(2, title='Other'))
        self.assertEqual([([], 'empty')], self.results)
        self.assertEqual(3, self.ws.get_url.call_count)

    def test_network_error_finishes_with_error(self):
        self.make().start()
        self.respond(b'', QNetworkReply.NetworkError.ServiceUnavailableError)
        self.assertEqual([([], 'error')], self.results)

    def test_invalid_document_finishes_with_error(self):
        self.make().start()
        self.respond(b'not json')
        self.assertEqual([([], 'error')], self.results)

    def test_timeout_aborts_pending_task_and_ignores_late_response(self):
        request = self.make()
        request.start()
        request._timer.timeout.emit()
        self.assertEqual([([], 'timeout')], self.results)
        self.ws.abort_task.assert_called_once()
        self.respond(search_results(1))
        self.assertEqual(1, self.ws.get_url.call_count)
        self.assertEqual([([], 'timeout')], self.results)

    def test_cancel_aborts_and_ignores_late_response(self):
        request = self.make()
        request.start()
        self.respond(search_results(1))
        request.cancel()
        self.assertEqual([([], 'cancelled')], self.results)
        self.ws.abort_task.assert_called_once()
        self.respond(single_track_release(1))
        self.assertEqual([([], 'cancelled')], self.results)

    def test_stale_duplicate_callback_is_ignored(self):
        self.make().start()
        self.respond(search_results(1, 2), index=0)
        self.respond(search_results(1, 2), index=0)
        self.assertEqual(2, self.ws.get_url.call_count)
        self.assertEqual([], self.results)

    def test_cancelled_reply_does_not_report_an_error(self):
        request = self.make()
        request.start()
        request.cancel()
        self.respond(b'', QNetworkReply.NetworkError.OperationCanceledError)
        self.assertEqual([([], 'cancelled')], self.results)

    def test_stored_response_answers_without_a_request(self):
        responses = discogsgenre.DiscogsResponses()
        responses.put(SEARCH, {'type': 'release', 'artist': 'Artist', 'track': 'Song', 'per_page': 25, 'page': 1},
                      TOKEN, search_results(*range(1, 26)))
        responses.put('/releases/1', {}, TOKEN, single_track_release(1))
        self.make(responses).start()
        self.ws.get_url.assert_not_called()
        self.assertEqual([(['Funk'], 'https://www.discogs.com/release/1')], self.results)


class DiscogsResponsesTest(PicardTestCase):
    def query(self, per_page, page=None, **extra):
        query = {'type': 'release', 'artist': 'Artist', 'track': 'Song', 'per_page': per_page}
        if page is not None:
            query['page'] = page
        query.update(extra)
        return query

    def test_identical_request_is_reused(self):
        responses = discogsgenre.DiscogsResponses()
        document = search_results(1, 2)
        responses.put(SEARCH, self.query(25, 1), TOKEN, document)
        self.assertIs(document, responses.get(SEARCH, self.query(25, 1), TOKEN))
        responses.put('/releases/7', {}, TOKEN, single_track_release(7))
        self.assertEqual(7, responses.get('/releases/7', {}, TOKEN)['id'])

    def test_larger_first_page_answers_smaller_request(self):
        responses = discogsgenre.DiscogsResponses()
        responses.put(SEARCH, self.query(25, 1), TOKEN, search_results(*range(1, 26)))
        document = responses.get(SEARCH, self.query(5), TOKEN)
        self.assertEqual([1, 2, 3, 4, 5], [r['id'] for r in document['results']])
        self.assertNotIn('pagination', document)

    def test_smaller_page_never_answers_larger_request(self):
        responses = discogsgenre.DiscogsResponses()
        responses.put(SEARCH, self.query(5), TOKEN, search_results(1, 2, 3, 4, 5))
        self.assertIsNone(responses.get(SEARCH, self.query(25, 1), TOKEN))
        responses.put(SEARCH, self.query(25, 1), TOKEN, search_results(*range(1, 26)))
        self.assertEqual(25, len(responses.get(SEARCH, self.query(25, 1), TOKEN)['results']))

    def test_later_pages_are_not_derived(self):
        responses = discogsgenre.DiscogsResponses()
        responses.put(SEARCH, self.query(25, 1), TOKEN, search_results(*range(1, 26)))
        self.assertIsNone(responses.get(SEARCH, self.query(5, 2), TOKEN))
        self.assertIsNone(responses.get(SEARCH, self.query(25, 2), TOKEN))

    def test_different_parameters_or_token_are_not_reused(self):
        responses = discogsgenre.DiscogsResponses()
        responses.put(SEARCH, self.query(25, 1), TOKEN, search_results(1))
        self.assertIsNone(responses.get(SEARCH, self.query(25, 1, artist='Other'), TOKEN))
        self.assertIsNone(responses.get(SEARCH, self.query(25, 1), 'other-token'))
        self.assertIsNone(responses.get('/releases/1', {}, TOKEN))

    def test_failed_or_malformed_responses_are_not_stored(self):
        responses = discogsgenre.DiscogsResponses()
        responses.put(SEARCH, self.query(25, 1), TOKEN, b'')
        responses.put(SEARCH, self.query(5), TOKEN, {'message': 'error'})
        self.assertIsNone(responses.get(SEARCH, self.query(5), TOKEN))

    def test_token_is_not_kept_in_plain_text(self):
        responses = discogsgenre.DiscogsResponses()
        responses.put(SEARCH, self.query(25, 1), TOKEN, search_results(1))
        self.assertNotIn(TOKEN, repr(responses.__dict__))

    def test_store_is_bounded(self):
        responses = discogsgenre.DiscogsResponses()
        for ident in range(discogsgenre.DISCOGS_STORED_RESPONSES + 5):
            responses.put('/releases/%d' % ident, {}, TOKEN, single_track_release(ident))
        self.assertIsNone(responses.get('/releases/0', {}, TOKEN))
        self.assertIsNotNone(responses.get('/releases/%d' % (discogsgenre.DISCOGS_STORED_RESPONSES + 4), {}, TOKEN))


class DiscogsWebServiceTest(PicardTestCase):
    """The real WebService schedules, retries and logs Discogs requests."""

    def setUp(self):
        super().setUp()
        self.tmpdir = self.mktmpdir()
        patcher = patch('picard.webservice.appdirs.cache_folder', return_value=self.tmpdir)
        patcher.start()
        self.addCleanup(patcher.stop)
        rc_patcher = patch('picard.webservice.ratecontrol')
        self.ratecontrol = rc_patcher.start()
        self.addCleanup(rc_patcher.stop)
        self.set_config_values({
            'use_proxy': False,
            'server_host': 'musicbrainz.org',
            'network_transfer_timeout_seconds': 30,
            'network_cache_size_bytes': 100 * 1000 * 1000,
        })
        self.ws = WebService()
        self.ws._timer_run_next_task = MagicMock()
        self.ws._timer_count_pending_requests = MagicMock()
        self.parent = QtCore.QObject()
        self.addCleanup(self.parent.deleteLater)
        self.results = []

    def start(self):
        with patch.object(self.ws, 'add_request') as add:
            request = discogsgenre.DiscogsGenreRequest(self.parent, 'Artist', 'Song', TOKEN, webservice=self.ws)
            request.finished.connect(lambda values, status: self.results.append((values, status)))
            request.start()
        add.assert_called_once()
        return request, add.call_args.args[0]

    def reply(self, status, body=b'', error=QNetworkReply.NetworkError.NoError):
        reply = MagicMock()
        reply.error.return_value = error
        reply.attribute.side_effect = lambda attr: {
            QNetworkRequest.Attribute.HttpStatusCodeAttribute: status,
            QNetworkRequest.Attribute.HttpReasonPhraseAttribute: 'Status',
            QNetworkRequest.Attribute.Http2WasUsedAttribute: False,
            QNetworkRequest.Attribute.RedirectionTargetAttribute: None,
            QNetworkRequest.Attribute.SourceIsFromCacheAttribute: False,
        }.get(attr)
        url = QtCore.QUrl('https://api.discogs.com/database/search')
        reply.request.return_value = MagicMock()
        reply.request.return_value.url.return_value = url
        reply.url.return_value = url
        reply.readAll.return_value = MagicMock(data=MagicMock(return_value=body))
        reply.errorString.return_value = 'error'
        return reply

    def handle(self, wsrequest, reply):
        self.ws._active_requests[reply] = wsrequest
        with patch.object(self.ws, 'add_request') as retry:
            self.ws._handle_reply(reply, wsrequest)
        return retry

    def test_request_is_scheduled_per_host_with_cache_and_header_token(self):
        _request, wsrequest = self.start()
        self.assertEqual(('api.discogs.com', 443), wsrequest.get_host_key())
        self.assertEqual(b'Discogs token=' + TOKEN.encode(), bytes(wsrequest.rawHeader(b'Authorization')))
        self.assertNotIn(TOKEN, wsrequest.url().toString())
        # Not forced to the network: Picard's disk cache applies HTTP caching rules.
        self.assertIsNone(wsrequest.attribute(QNetworkRequest.Attribute.CacheLoadControlAttribute))
        self.assertFalse(wsrequest.mblogin)

    def test_http_429_is_retried_with_backoff_then_succeeds(self):
        _request, wsrequest = self.start()
        retry = self.handle(wsrequest, self.reply(429, error=QNetworkReply.NetworkError.UnknownContentError))
        retry.assert_called_once_with(wsrequest)
        self.assertTrue(self.ratecontrol.adjust.call_args.args[1])
        self.assertEqual([], self.results)
        self.handle(wsrequest, self.reply(200, json.dumps({'results': []}).encode()))
        self.assertEqual([([], 'empty')], self.results)

    def test_exhausted_http_429_finishes_with_error(self):
        _request, wsrequest = self.start()
        while not wsrequest.max_retries_reached():
            wsrequest.mark_for_retry()
        retry = self.handle(wsrequest, self.reply(429, error=QNetworkReply.NetworkError.UnknownContentError))
        retry.assert_not_called()
        self.assertEqual([([], 'error')], self.results)

    def test_token_never_appears_in_webservice_logs(self):
        with patch('picard.webservice.log') as log:
            _request, wsrequest = self.start()
            self.handle(wsrequest, self.reply(429, error=QNetworkReply.NetworkError.UnknownContentError))
            self.handle(wsrequest, self.reply(200, json.dumps({'results': []}).encode()))
        logged = repr(log.mock_calls)
        self.assertIn('api.discogs.com', logged)
        self.assertNotIn(TOKEN, logged)
