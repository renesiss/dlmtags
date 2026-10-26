# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""Smart Genre enrichment uses mocked services and never touches media files."""

from unittest.mock import (
    MagicMock,
    patch,
)

from PyQt6.QtNetwork import QNetworkReply

from test.picardtestcase import PicardTestCase
from test.test_smartscan import (
    BASE_SETTINGS,
    acoustid_recording,
    file_metadata,
    release,
)

from picard.config import Option
from picard.file import File
from picard.metadata import Metadata
from picard.smartscan import Outcome

from picard.ui.dlmtags.workspace import MatchingResults
from picard.ui.mainwindow import MainWindow


class SmartGenreTest(PicardTestCase):
    def setUp(self):
        super().setUp()
        self.set_config_values(
            dict(
                BASE_SETTINGS,
                use_genres=True,
                min_genre_usage=50,
                max_genres=5,
                genres_filter='',
                join_genres='',
                folksonomy_tags=False,
                only_my_genres=False,
                smart_genre_lastfm_enabled=False,
                lastfm_api_key='',
            )
        )
        self.patch_tagger_instance('picard.item')
        self.tagger.window = MagicMock()
        self.tagger.acoustidmanager = MagicMock()
        self.tagger.isrc_submit_manager = MagicMock()
        self.tagger._acoustid = MagicMock()
        self.tagger.mb_api = MagicMock()
        self.tagger.mb_api.get_track_by_id.return_value = object()
        self.tagger.mb_api.get_release_by_id.return_value = object()
        self.tagger.webservice = MagicMock()
        self.tagger.webservice.get_url.return_value = object()
        self.tagger.albums = {}
        self.file = File('/music/Artist - Song.mp3')
        self.file.state = File.State.NORMAL
        self.file.orig_metadata = file_metadata(title='Song', artist='Artist')
        self.file.metadata = Metadata(self.file.orig_metadata)

    def tearDown(self):
        self.file.cancel_smart_genre()
        super().tearDown()

    def match(self):
        candidate = acoustid_recording(
            'rec', 'Song', 'Artist', 232000, 0.95,
            [release('rel', 'Album', '1999', rgid='rg')],
        )
        self.file.start_smart_scan()
        callback = self.tagger._acoustid.analyze.call_args.args[1]
        callback({'recordings': [candidate]}, None, None)
        self.assertEqual(Outcome.MATCHED, self.file.smart_scan_decision.outcome)
        if self.tagger.mb_api.get_track_by_id.called:
            return self.tagger.mb_api.get_track_by_id.call_args.args[1]
        return None

    def respond(self, callback, genres=None, **extra):
        callback(dict(id='rec', genres=genres or [], **extra), None, None)

    def test_missing_genre_recording_and_multivalue(self):
        callback = self.match()
        self.respond(callback, [{'name': 'rock', 'count': 10}, {'name': 'pop', 'count': 6}])
        self.assertEqual(['Pop', 'Rock'], self.file.metadata.getall('genre'))
        self.assertEqual('MusicBrainz recording', self.file.smart_genre_source)
        self.assertFalse(self.file.smart_genre_pending)
        self.assertIsNone(self.file.smart_scan_stage)
        self.assertEqual('', self.file.orig_metadata['genre'])

    def test_existing_genre_survives_normal_album_loading(self):
        self.file.metadata['genre'] = ['Jazz', 'Soul']
        self.match()
        self.tagger.mb_api.get_track_by_id.assert_not_called()
        self.file.copy_metadata(Metadata(title='Song', genre='Rock', musicbrainz_recordingid='rec'))
        self.assertEqual(['Jazz', 'Soul'], self.file.metadata.getall('genre'))

    def test_recording_genre_survives_asynchronous_album_updates(self):
        callback = self.match()
        album = Metadata(title='Song', genre='Album Genre', musicbrainz_recordingid='rec')
        self.file.copy_metadata(album)
        self.assertEqual('', self.file.metadata['genre'])
        self.assertNotIn('genre', self.file.metadata.deleted_tags)
        self.respond(callback, [{'name': 'rock', 'count': 10}])
        self.file.copy_metadata(album)
        self.assertEqual(['Rock'], self.file.metadata.getall('genre'))

    def test_reverting_file_cancels_enrichment(self):
        callback = self.match()
        self.file.copy_metadata(self.file.orig_metadata)
        self.respond(callback, [{'name': 'rock', 'count': 10}])
        self.assertFalse(self.file.smart_genre_pending)
        self.assertEqual('', self.file.metadata['genre'])

    def test_changing_recording_cancels_enrichment(self):
        callback = self.match()
        self.file.copy_metadata(Metadata(musicbrainz_recordingid='different'))
        self.respond(callback, [{'name': 'rock', 'count': 10}])
        self.assertFalse(self.file.smart_genre_pending)
        self.assertEqual('', self.file.metadata['genre'])

    def test_progress_waits_for_genre_then_finishes(self):
        callback = self.match()
        results = MagicMock()
        results._targets = [self.file]
        results._window.tagger.webservice.num_pending_web_requests = 0
        results._has_active_operation.side_effect = lambda: MatchingResults._has_active_operation(results)
        MatchingResults._finish_operation(results)
        results.progress.hide.assert_not_called()
        self.respond(callback, [{'name': 'rock', 'count': 10}])
        # The album suggestion search of the unverified release still runs.
        MatchingResults._finish_operation(results)
        results.progress.hide.assert_not_called()
        self.tagger.mb_api.browse_releases.call_args.args[0]({'releases': []}, None, None)
        MatchingResults._finish_operation(results)
        results.progress.hide.assert_called_once()
        results._clock.stop.assert_called_once()
        results._elapsed.invalidate.assert_called_once()

    def test_network_cancel_button_cancels_genre(self):
        callback = self.match()
        self.tagger.files = {'song': self.file}
        window = MagicMock()
        window.tagger = self.tagger
        MainWindow.stop_network_requests(window)
        self.respond(callback, [{'name': 'rock', 'count': 10}])
        self.assertFalse(self.file.smart_genre_pending)
        self.assertEqual('', self.file.metadata['genre'])

    def test_needs_review_does_not_enrich(self):
        candidate = acoustid_recording('rec', 'Song', 'Different Artist', 232000, 0.95, [])
        self.file.start_smart_scan()
        self.tagger._acoustid.analyze.call_args.args[1]({'recordings': [candidate]}, None, None)
        # The artist conflict is followed by the metadata lookup; still Needs Review.
        self.tagger.mb_api.find_tracks.call_args.args[0]({'recordings': []}, None, None)
        self.assertEqual(Outcome.NEEDS_REVIEW, self.file.smart_scan_decision.outcome)
        self.tagger.mb_api.get_track_by_id.assert_not_called()
        self.assertEqual('', self.file.metadata['genre'])

    def test_personal_genres_respects_auth_and_does_not_use_lastfm(self):
        self.set_config_values({
            'only_my_genres': True, 'smart_genre_lastfm_enabled': True, 'lastfm_api_key': 'test-key',
        })
        callback = self.match()
        request = self.tagger.mb_api.get_track_by_id.call_args.kwargs
        self.assertTrue(request['mblogin'])
        self.assertEqual({'user-tags', 'user-genres'}, request['inc'])
        self.respond(callback)
        self.tagger.mb_api.get_release_by_id.call_args.args[1]({'id': 'rel'}, None, None)
        # Only the album suggestion search may ask Last.fm, never for genres.
        methods = [c.kwargs['unencoded_queryargs']['method'] for c in self.tagger.webservice.get_url.call_args_list]
        self.assertNotIn('track.getTopTags', methods)

    def test_folksonomy_setting_and_malformed_counts(self):
        self.set_config_values({'folksonomy_tags': True})
        callback = self.match()
        self.respond(callback, tags=[
            {'name': 'rock', 'count': '10'},
            {'name': 'jazz', 'count': {}},
            {'name': ['invalid'], 'count': 2},
            None,
        ])
        self.assertEqual('Rock', self.file.metadata['genre'])

    def test_manual_edit_while_request_pending(self):
        callback = self.match()
        self.file.metadata['genre'] = 'My Genre'
        self.respond(callback, [{'name': 'rock', 'count': 10}])
        self.assertEqual('My Genre', self.file.metadata['genre'])
        self.assertIsNone(self.file.smart_genre_source)

    def test_manual_delete_while_request_pending(self):
        callback = self.match()
        del self.file.metadata['genre']
        self.respond(callback, [{'name': 'rock', 'count': 10}])
        self.assertEqual('', self.file.metadata['genre'])

    def test_filter_usage_limit_and_join(self):
        self.set_config_values({'genres_filter': '-pop', 'max_genres': 1, 'join_genres': ' / '})
        callback = self.match()
        self.respond(callback, [
            {'name': 'seen live', 'count': 100}, {'name': 'awesome', 'count': 100},
            {'name': 'favorites', 'count': 100}, {'name': 'pop', 'count': 50},
            {'name': 'rock', 'count': 10}, {'name': 'jazz', 'count': 4},
        ])
        self.assertEqual(['Rock'], self.file.metadata.getall('genre'))

    def test_joined_genres(self):
        self.set_config_values({'join_genres': ' / '})
        callback = self.match()
        self.respond(callback, [{'name': 'pop', 'count': 10}, {'name': 'rock', 'count': 10}])
        self.assertEqual(['Pop / Rock'], self.file.metadata.getall('genre'))

    def test_release_group_fallback_after_source_error(self):
        callback = self.match()
        callback({}, None, 500)
        fallback = self.tagger.mb_api.get_release_by_id.call_args.args[1]
        fallback({'id': 'rel', 'release-group': {
            'id': 'rg', 'genres': [{'name': 'jazz', 'count': 8}],
        }}, None, None)
        self.assertEqual('Jazz', self.file.metadata['genre'])
        self.assertEqual('MusicBrainz release group', self.file.smart_genre_source)

    def test_all_sources_error_leaves_empty_and_completes(self):
        callback = self.match()
        callback({}, None, 500)
        self.tagger.mb_api.get_release_by_id.call_args.args[1]({}, None, 500)
        self.assertEqual('', self.file.metadata['genre'])
        self.assertFalse(self.file.smart_genre_pending)
        self.assertEqual(Outcome.MATCHED, self.file.smart_scan_decision.outcome)

    def test_cancellation_ignores_late_response(self):
        callback = self.match()
        self.file.cancel_smart_genre()
        self.respond(callback, [{'name': 'rock', 'count': 10}])
        self.assertFalse(self.file.smart_genre_pending)
        self.assertEqual('', self.file.metadata['genre'])
        self.tagger.mb_api.get_release_by_id.assert_not_called()

    def test_cancelled_response_does_not_start_fallback(self):
        callback = self.match()
        callback({}, None, QNetworkReply.NetworkError.OperationCanceledError)
        self.assertFalse(self.file.smart_genre_pending)
        self.assertEqual('', self.file.metadata['genre'])
        self.tagger.mb_api.get_release_by_id.assert_not_called()

    def test_lastfm_stays_opt_in_even_with_key(self):
        self.set_config_values({'lastfm_api_key': 'test-key'})
        callback = self.match()
        self.respond(callback)
        self.tagger.mb_api.get_release_by_id.call_args.args[1]({'id': 'rel'}, None, None)
        # Only the album suggestion search may ask Last.fm, never for genres.
        methods = [c.kwargs['unencoded_queryargs']['method'] for c in self.tagger.webservice.get_url.call_args_list]
        self.assertNotIn('track.getTopTags', methods)

    def test_timeout_ignores_late_response(self):
        callback = self.match()
        self.file._smart_genre_request._timeout()
        self.respond(callback, [{'name': 'rock', 'count': 10}])
        self.assertFalse(self.file.smart_genre_pending)
        self.assertEqual('', self.file.metadata['genre'])

    def test_duplicate_callbacks_and_requests(self):
        callback = self.match()
        self.file._start_smart_genre(self.file.smart_scan_decision)
        self.tagger.mb_api.get_track_by_id.assert_called_once()
        self.respond(callback)
        self.respond(callback)
        self.tagger.mb_api.get_release_by_id.assert_called_once()

    def test_disabled_preference_is_respected(self):
        self.set_config_values({'use_genres': False})
        self.match()
        self.tagger.mb_api.get_track_by_id.assert_not_called()
        self.assertFalse(self.file.smart_genre_pending)

    def test_default_is_enabled(self):
        self.assertTrue(Option.get('setting', 'use_genres').default)

    def test_no_match_does_not_enrich(self):
        self.file.start_smart_scan()
        self.tagger._acoustid.analyze.call_args.args[1]({'recordings': []}, None, None)
        self.tagger.mb_api.find_tracks.call_args.args[0]({'recordings': []}, None, None)
        self.assertEqual(Outcome.NO_MATCH, self.file.smart_scan_decision.outcome)
        self.tagger.mb_api.get_track_by_id.assert_not_called()

    def test_lastfm_opt_in_requires_key(self):
        self.set_config_values({'smart_genre_lastfm_enabled': True})
        callback = self.match()
        self.respond(callback)
        self.tagger.mb_api.get_release_by_id.call_args.args[1]({'id': 'rel'}, None, None)
        # Only the album suggestion search may ask Last.fm, never for genres.
        methods = [c.kwargs['unencoded_queryargs']['method'] for c in self.tagger.webservice.get_url.call_args_list]
        self.assertNotIn('track.getTopTags', methods)

    def test_lastfm_uses_confirmed_mbid_and_filters_tags(self):
        self.set_config_values({'smart_genre_lastfm_enabled': True, 'lastfm_api_key': 'test-key'})
        callback = self.match()
        self.respond(callback)
        self.tagger.mb_api.get_release_by_id.call_args.args[1]({'id': 'rel'}, None, None)
        request = self.tagger.webservice.get_url.call_args.kwargs
        self.assertEqual('rec', request['queryargs']['mbid'])
        request['handler']({'toptags': {'tag': [
            {'name': 'seen live', 'count': 100}, {'name': 'rock', 'count': 10},
        ]}}, None, None)
        self.assertEqual('Rock', self.file.metadata['genre'])
        self.assertEqual('Last.fm', self.file.smart_genre_source)

    def test_lastfm_error_leaves_match_unchanged(self):
        self.set_config_values({'smart_genre_lastfm_enabled': True, 'lastfm_api_key': 'test-key'})
        callback = self.match()
        self.respond(callback)
        self.tagger.mb_api.get_release_by_id.call_args.args[1]({'id': 'rel'}, None, None)
        request = self.tagger.webservice.get_url.call_args.kwargs
        request['handler']({'error': 10, 'message': 'Invalid API key'}, None, None)
        self.assertEqual('', self.file.metadata['genre'])
        self.assertFalse(self.file.smart_genre_pending)
        self.assertEqual(Outcome.MATCHED, self.file.smart_scan_decision.outcome)
        result = self.file.smart_genre_diagnostics[-1]
        self.assertEqual('Last.fm', result['source'])
        self.assertEqual('not_configured', result['status'])
        self.assertIn('API error 10', result['reason'])

    def test_removal_cancels_enrichment(self):
        callback = self.match()
        self.file.remove()
        self.respond(callback, [{'name': 'rock', 'count': 10}])
        self.assertEqual(File.State.REMOVED, self.file.state)
        self.assertFalse(self.file.smart_genre_pending)
        self.assertEqual('', self.file.metadata['genre'])

    def test_no_genres_limit_does_not_request(self):
        self.set_config_values({'max_genres': 0})
        self.match()
        self.tagger.mb_api.get_track_by_id.assert_not_called()

    def test_no_automatic_save(self):
        with patch.object(File, 'save') as save:
            callback = self.match()
            self.respond(callback, [{'name': 'rock', 'count': 10}])
        save.assert_not_called()
