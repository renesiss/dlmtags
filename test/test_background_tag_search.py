# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""Background suggestions and explicit field-only selection, with mocked APIs."""

from types import SimpleNamespace
from unittest.mock import (
    MagicMock,
    patch,
)

from PyQt6 import (
    QtCore,
    QtWidgets,
)

from test.picardtestcase import PicardTestCase
from test.test_smartscan import (
    BASE_SETTINGS,
    acoustid_recording,
    credit,
    file_metadata,
    release,
)

from picard.config import (
    Option,
    get_config,
)
from picard.discogsgenre import (
    exact_single_track_genres,
    folded,
    same_text,
)
from picard.file import File
from picard.metadata import Metadata
from picard.smartscan import Outcome
from picard.tagprovenance import (
    set_source,
    source_for,
)
from picard.tagsearch import TagSearchRequest
from picard.webservice import WebService

from picard.ui.dlmtags.tag_editor import PRIMARY_TAGS
from picard.ui.dlmtags.tag_form import TagEditor
from picard.ui.dlmtags.workspace import MatchingResults
from picard.ui.metadatabox import MetadataBox
from picard.ui.options.genres import GenresOptionsPage


class BackgroundTagTest(PicardTestCase):
    def test_source_label_switches_with_selected_file(self):
        self.file.metadata['genre'] = 'Funk'
        set_source(self.file, 'genre', 'Discogs')
        editor, box = self.editor()
        editor.refresh()
        self.assertIn('Discogs', editor._source_labels['genre'].text())
        other = File('/music/Other.mp3')
        other.orig_metadata = Metadata()
        other.metadata = Metadata(genre='Rock')
        set_source(other, 'genre', 'Last.fm')
        box.files = {other}
        editor.refresh()
        self.assertEqual('Last.fm', editor._source_labels['genre'].text())

    def test_stale_find_does_not_overwrite_manual_source(self):
        request = self.search('title')
        self.respond()
        self.file.metadata['title'] = 'Edited'
        set_source(self.file, 'title', 'Manual')
        self.assertFalse(request.apply(request.candidates))
        self.assertEqual('Manual', source_for(self.file, 'title'))

    def test_matching_sources_list_only_contributions(self):
        self.matched()
        self.file.metadata['genre'] = 'Funk'
        set_source(self.file, 'genre', 'Last.fm')
        text = '\n'.join(MatchingResults._describe(MagicMock(), self.file))
        self.assertIn('Recognition: AcoustID + MusicBrainz', text)
        self.assertIn('Genre source: Last.fm', text)
        set_source(self.file, 'genre', 'Manual')
        self.assertNotIn('Genre source:', '\n'.join(MatchingResults._describe(MagicMock(), self.file)))

    def test_api_settings_reuse_options_and_mask_credentials(self):
        self.patch_tagger_instance('picard.ui.options')
        page = GenresOptionsPage()
        self.addCleanup(page.deleteLater)
        page.load()
        page.ui.dlmtags_discogs_token.setText('test-token')
        page.ui.lastfm_api_key.setText('test-key')
        page.ui.dlmtags_discogs_enabled.setChecked(True)
        page.ui.smart_genre_lastfm_enabled.setChecked(True)
        page.save()
        self.assertEqual('test-token', get_config().setting['dlmtags_discogs_token'])
        self.assertTrue(get_config().setting['smart_genre_lastfm_enabled'])
        for name in ('dlmtags_discogs_token', 'lastfm_api_key'):
            self.assertFalse(Option.get('setting', name).shareable)
            self.assertEqual(QtWidgets.QLineEdit.EchoMode.Password, getattr(page.ui, name).echoMode())

    def test_recording_attribution_and_acoustid_identity_only(self):
        self.matched()
        self.assertEqual('MusicBrainz', source_for(self.file, 'title'))
        self.assertEqual('MusicBrainz', source_for(self.file, 'artist'))
        self.assertNotEqual('AcoustID', source_for(self.file, 'title'))
        self.assertEqual('', source_for(self.file, 'acoustid_id'))
        self.assertEqual('', self.file.metadata['acoustid_id'])
        self.file.metadata['title'] = 'My Title'
        set_source(self.file, 'title', 'Manual')
        self.file.copy_metadata(Metadata(title='Other', musicbrainz_recordingid='rec'))
        self.assertEqual('My Title', self.file.metadata['title'])
        self.assertEqual('Manual', source_for(self.file, 'title'))

    def test_lastfm_genre_attribution(self):
        self.set_config_values({'smart_genre_lastfm_enabled': True, 'lastfm_api_key': 'test-key'})
        self.matched()
        self.respond(genres=[])
        self.tagger.mb_api.get_release_by_id.call_args.args[1]({'id': 'rel'}, None, None)
        self.tagger.webservice.get_url.call_args.kwargs['handler']({
            'toptags': {'tag': [{'name': 'funk', 'count': 100}]},
        }, None, None)
        self.assertEqual('Last.fm', source_for(self.file, 'genre'))

    def test_discogs_genre_and_style_separate_attribution(self):
        self.set_config_values({'dlmtags_discogs_enabled': True, 'dlmtags_discogs_token': 'test-token'})
        with patch('picard.smartgenre.DiscogsGenreRequest') as factory:
            factory.return_value.styles = ['Disco']
            self.matched()
            self.respond(genres=[])
            self.tagger.mb_api.get_release_by_id.call_args.args[1]({'id': 'rel'}, None, None)
            factory.return_value.finished.connect.call_args.args[0](
                ['Funk', 'Soul'], 'https://www.discogs.com/release/1',
            )
        self.assertEqual(['Funk', 'Soul'], self.file.metadata.getall('genre'))
        self.assertEqual(['Disco'], self.file.metadata.getall('style'))
        self.assertTrue(source_for(self.file, 'genre').startswith('Discogs'))
        self.assertTrue(source_for(self.file, 'style').startswith('Discogs'))

    def test_smart_genre_discogs_uses_webservice_and_file_responses(self):
        self.set_config_values({'dlmtags_discogs_enabled': True, 'dlmtags_discogs_token': 'test-token'})
        with patch('picard.smartgenre.DiscogsGenreRequest') as factory:
            self.matched()
            self.respond(genres=[])
            self.tagger.mb_api.get_release_by_id.call_args.args[1]({'id': 'rel'}, None, None)
        kwargs = factory.call_args.kwargs
        self.assertIs(self.tagger.webservice, kwargs['webservice'])
        self.assertIs(self.file.dlmtags_discogs_responses, kwargs['responses'])

    def test_style_find_changes_only_selected_field_and_source(self):
        self.set_config_values({'dlmtags_discogs_enabled': True, 'dlmtags_discogs_token': 'test-token'})
        request = self.search('style')
        before = Metadata(self.file.metadata)
        self.tagger.webservice.get_url.call_args.kwargs['handler']({
            'pagination': {'page': 1, 'pages': 1},
            'results': [{'id': 1, 'title': 'Artist - Song', 'style': ['Disco']}],
        }, None, None)
        self.tagger.webservice.get_url.call_args.kwargs['handler']({
            'uri': 'https://www.discogs.com/release/1', 'artists': [{'name': 'Artist'}],
            'tracklist': [{'type_': 'track', 'title': 'Song'}], 'styles': ['Disco'],
        }, None, None)
        self.assertEqual(before, self.file.metadata)
        self.assertTrue(request.apply(request.candidates))
        before['style'] = 'Disco'
        self.assertEqual(before, self.file.metadata)
        self.assertEqual('Discogs', source_for(self.file, 'style'))
        self.assertEqual('https://www.discogs.com/release/1', self.file.dlmtags_tag_urls['style'])

    def test_missing_discogs_credentials_do_not_query_or_change_sources(self):
        self.set_config_values({'dlmtags_discogs_enabled': True, 'dlmtags_discogs_token': ''})
        request = self.search('style')
        self.tagger.webservice.get_url.assert_not_called()
        self.assertEqual('not_configured', request.state)
        self.assertEqual({}, self.file.dlmtags_tag_sources)

    def test_discogs_only_verified_single_track_release_supplies_fields(self):
        payload = {
            'artists': [{'name': 'Artist'}], 'tracklist': [{'type_': 'track', 'title': 'Song'}],
            'genres': ['Funk', 'Soul'], 'styles': ['Disco'],
        }
        self.assertEqual(['Funk', 'Soul'], exact_single_track_genres(payload, 'Artist', 'Song'))
        self.assertEqual(['Disco'], exact_single_track_genres(payload, 'Artist', 'Song', field='style'))
        payload['tracklist'].append({'type_': 'track', 'title': 'Another'})
        self.assertEqual([], exact_single_track_genres(payload, 'Artist', 'Song'))

    def test_discogs_single_track_match_preserves_non_latin_scripts(self):
        payload = {
            'artists': [{'name': 'Σάκης Ρουβάς'}], 'tracklist': [{'type_': 'track', 'title': 'Σ’ έχω ερωτευτεί'}],
            'genres': ['Pop'],
        }
        self.assertEqual(['Pop'], exact_single_track_genres(payload, 'ΣΑΚΗΣ ΡΟΥΒΑΣ', "Σ' Έχω Ερωτευτεί"))
        self.assertEqual([], exact_single_track_genres(payload, 'Άννα Βίσση', "Σ' έχω ερωτευτεί"))
        self.assertEqual([], exact_single_track_genres(payload, 'Σάκης Ρουβάς', 'Άλλο τραγούδι'))
        payload['artists'] = [{'name': '!!!'}]
        payload['tracklist'] = [{'type_': 'track', 'title': '???'}]
        self.assertEqual([], exact_single_track_genres(payload, '***', '...'))
        self.assertEqual(['Pop'], exact_single_track_genres(payload, '!!!', '???'))

    def test_folded_keeps_every_script_and_latin_behavior(self):
        self.assertEqual('σακησ ρουβασ', folded('Σάκης Ρουβάς'))
        self.assertEqual('кино', folded('Кино'))
        self.assertEqual('beyonce', folded('Beyoncé'))
        self.assertEqual('don t stop me now', folded("Don't Stop Me Now!"))
        self.assertEqual('ac dc', folded('AC/DC'))
        self.assertFalse(same_text('', ''))
        self.assertFalse(same_text('Σάκης', 'Άννα'))
        self.assertFalse(same_text('!!!', ''))
        self.assertTrue(same_text('AC/DC', 'ac dc'))

    def test_provider_keys_are_redacted_from_network_log_urls(self):
        url = QtCore.QUrl('https://ws.audioscrobbler.com/2.0/?api_key=secret-key&mbid=rec')
        display = WebService.display_url(url)
        self.assertNotIn('secret-key', display)
        self.assertIn('mbid=rec', display)

    def test_sources_follow_values_and_never_enter_metadata(self):
        self.file.metadata['genre'] = 'Funk'
        set_source(self.file, 'genre', 'Discogs', 'https://www.discogs.com/release/1')
        self.file.metadata['title'] = 'Song'
        set_source(self.file, 'title', 'MusicBrainz')
        self.assertEqual('Discogs', source_for(self.file, 'genre'))
        self.assertEqual('MusicBrainz', source_for(self.file, 'title'))
        self.file.metadata['genre'] = 'Soul'
        self.assertEqual('Unknown', source_for(self.file, 'genre'))
        set_source(self.file, 'genre', 'Manual')
        self.assertEqual('Manual', source_for(self.file, 'genre'))
        self.assertNotIn('genre', self.file.dlmtags_tag_urls)
        self.assertFalse(any('source' in tag or 'provenance' in tag for tag in self.file.metadata))

    def test_existing_and_filename_sources(self):
        self.assertEqual('Existing file', source_for(self.file, 'title'))
        self.file.metadata['artist'] = 'Filename artist'
        set_source(self.file, 'artist', 'Filename')
        self.assertEqual('Filename', source_for(self.file, 'artist'))

    def setUp(self):
        super().setUp()
        self.set_config_values(dict(
            BASE_SETTINGS, use_genres=True, min_genre_usage=50, max_genres=5,
            genres_filter='', join_genres='', folksonomy_tags=False, only_my_genres=False,
            smart_genre_lastfm_enabled=False, lastfm_api_key='',
        ))
        self.patch_tagger_instance('picard.item')
        self.tagger.window = MagicMock()
        self.tagger.acoustidmanager = MagicMock()
        self.tagger.isrc_submit_manager = MagicMock()
        self.tagger._acoustid = MagicMock()
        self.tagger.mb_api = MagicMock()
        self.tagger.webservice = MagicMock()
        self.tagger.albums = {}
        self.file = File('/music/Artist - Song.mp3')
        self.file.state = File.State.NORMAL
        self.file.orig_metadata = file_metadata(title='Artist - Song')
        self.file.metadata = Metadata(self.file.orig_metadata)

    def tearDown(self):
        self.file.cancel_smart_genre()
        for request in list(getattr(self.file, '_tag_search_requests', {}).values()):
            request.cancel()
        super().tearDown()

    def matched(self):
        node = acoustid_recording('rec', 'Song', 'Artist', 232000, 0.95, [
            release('rel', 'Album', '2024', rgid='rg'),
        ])
        self.file.start_smart_scan()
        self.tagger._acoustid.analyze.call_args.args[1]({'recordings': [node]}, None, None)
        self.assertEqual(Outcome.MATCHED, self.file.smart_scan_decision.outcome)

    def test_matched_recording_updates_new_tags_before_album_loads(self):
        self.matched()
        self.assertEqual('Artist', self.file.metadata['artist'])
        self.assertEqual('Song', self.file.metadata['title'])
        self.assertEqual('', self.file.metadata['album'])
        self.assertEqual('Artist - Song', self.file.orig_metadata['title'])
        self.assertEqual('', self.file.orig_metadata['artist'])

    def test_manual_fields_survive_later_metadata_updates(self):
        self.file.metadata['comment'] = 'User note'
        self.file.metadata['bpm'] = '128'
        self.matched()
        self.file.metadata['title'] = 'My display title'
        self.file.copy_metadata(Metadata(
            title='Song', artist='Artist', musicbrainz_recordingid='rec', comment='Provider note',
        ))
        self.assertEqual('My display title', self.file.metadata['title'])
        self.assertEqual('User note', self.file.metadata['comment'])
        self.assertEqual('128', self.file.metadata['bpm'])

    def test_existing_tags_are_preserved_when_provider_has_no_value(self):
        self.file.orig_metadata['comment'] = 'Existing note'
        self.file.metadata['comment'] = 'Existing note'
        self.matched()
        self.file.copy_metadata(Metadata(title='Song', artist='Artist', musicbrainz_recordingid='rec'))
        self.assertEqual('Existing note', self.file.metadata['comment'])

    def test_no_save_or_browser(self):
        with patch.object(File, 'save') as save, patch('picard.util.webbrowser2.open') as browser:
            self.matched()
        save.assert_not_called()
        browser.assert_not_called()

    def search(self, tag):
        self.file.metadata['artist'] = 'Artist'
        self.file.metadata['title'] = 'Song'
        self.file.metadata['musicbrainz_recordingid'] = 'rec'
        request = TagSearchRequest(self.file, tag)
        request.start()
        return request

    def respond(self, **extra):
        node = dict(id='rec', title='Canonical Song', length=232000, **extra)
        node.setdefault('artist-credit', credit('Canonical Artist'))
        self.tagger.mb_api.get_track_by_id.call_args.args[1](node, None, None)

    def browse(self, releases, count=None):
        self.tagger.mb_api.browse_releases.call_args.args[0]({
            'releases': releases, 'release-count': len(releases) if count is None else count,
        }, None, None)

    def test_field_search_does_not_write_until_selected_then_only_one_field(self):
        request = self.search('title')
        before = Metadata(self.file.metadata)
        self.respond()
        self.assertEqual(before, self.file.metadata)
        self.assertEqual(('Canonical Song',), request.candidates[0].values)
        self.assertEqual('MusicBrainz', request.candidates[0].source)
        self.assertEqual('MusicBrainz recording', request.candidates[0].evidence[0].source)
        self.assertIn('Canonical Artist — Canonical Song', request.candidates[0].evidence[0].description)
        self.assertTrue(request.apply([request.candidates[0]]))
        before['title'] = 'Canonical Song'
        self.assertEqual(before, self.file.metadata)
        self.assertEqual('Artist - Song', self.file.orig_metadata['title'])

    def test_field_manual_edit_after_results_prevents_apply(self):
        request = self.search('title')
        self.respond()
        self.file.metadata['title'] = 'Manual edit'
        self.assertFalse(request.apply([request.candidates[0]]))
        self.assertEqual('Manual edit', self.file.metadata['title'])

    def test_field_manual_edit_during_request_discards_late_response(self):
        request = self.search('album')
        self.file.metadata['album'] = 'My Album'
        self.browse([release('rel', 'Provider Album', '2024')])
        self.assertEqual('cancelled', request.state)
        self.assertEqual([], request.candidates)
        self.assertEqual('My Album', self.file.metadata['album'])

    def test_field_context_change_discards_response(self):
        request = self.search('album')
        self.file.metadata['musicbrainz_recordingid'] = 'other'
        self.browse([release('rel', 'Provider Album', '2024')])
        self.assertEqual('cancelled', request.state)
        self.assertEqual('', self.file.metadata['album'])

    def test_field_network_failure_and_timeout_do_not_write(self):
        request = self.search('title')
        before = Metadata(self.file.metadata)
        self.tagger.mb_api.get_track_by_id.call_args.args[1]({}, None, 500)
        self.assertEqual('error', request.state)
        self.assertEqual(before, self.file.metadata)
        request = self.search('title')
        callback = self.tagger.mb_api.get_track_by_id.call_args.args[1]
        request._timeout()
        callback({'id': 'rec', 'title': 'Late'}, None, None)
        self.assertEqual('error', request.state)
        self.assertEqual(before, self.file.metadata)
        self.assertFalse(self.file.tag_search_pending)

    def test_field_cancellation_ignores_late_response(self):
        request = self.search('title')
        request.cancel()
        self.respond()
        self.assertEqual([], request.candidates)
        self.assertEqual('Song', self.file.metadata['title'])
        self.assertFalse(request.active)

    def test_duplicate_field_search_does_not_duplicate_request(self):
        request = self.search('album')
        duplicate = TagSearchRequest(self.file, 'album')
        duplicate.start()
        self.tagger.mb_api.browse_releases.assert_called_once()
        self.assertTrue(request.active)
        self.assertEqual('cancelled', duplicate.state)

    def test_field_release_date_normalization_and_field_only_apply(self):
        request = self.search('date')
        self.respond()
        self.browse([release('rel', 'Album', {'year': 2024, 'month': 8})])
        self.assertEqual(('2024-08',), request.candidates[0].values)
        before = Metadata(self.file.metadata)
        self.assertTrue(request.apply([request.candidates[0]]))
        before['date'] = '2024-08'
        self.assertEqual(before, self.file.metadata)
        self.assertEqual('', self.file.metadata['album'])

    def test_field_version_uses_actual_musical_description_only(self):
        request = self.search('subtitle')
        self.respond(disambiguation='acoustic live performance')
        self.assertEqual(('acoustic live performance',), request.candidates[0].values)
        self.assertTrue(request.apply([request.candidates[0]]))
        self.assertEqual('Song', self.file.metadata['title'])
        self.assertEqual('acoustic live performance', self.file.metadata['subtitle'])

    def test_field_original_date_normalizes_release_group_date(self):
        request = self.search('originaldate')
        node = release('rel', 'Album', '2025', rgid='rg')
        node['release-group']['first-release-date'] = {'year': 2024, 'month': 8}
        self.respond()
        self.browse([node])
        self.assertEqual(('2024-08',), request.candidates[0].values)
        request.cancel()

    def test_genre_empty_and_unconfigured_providers_are_distinct(self):
        request = self.search('genre')
        self.respond(genres=[])
        self.assertEqual('empty', request.state)
        self.assertEqual('empty', request.providers['MusicBrainz'].state)
        self.assertEqual('not_configured', request.providers['Discogs'].state)
        self.assertEqual('not_configured', request.providers['Last.fm'].state)

    def test_genre_filtered_results_are_reported(self):
        request = self.search('genre')
        self.respond(genres=[{'name': 'seen live', 'count': 100}])
        self.assertEqual('filtered', request.state)
        self.assertEqual('filtered', request.providers['MusicBrainz'].state)
        self.assertEqual([], request.candidates)

    def test_genre_network_error_is_not_no_genres(self):
        request = self.search('genre')
        self.tagger.mb_api.get_track_by_id.call_args.args[1]({}, None, 1)
        self.assertEqual('error', request.state)
        self.assertEqual('error', request.providers['MusicBrainz'].state)

    def test_genre_filter_and_configuration_cases(self):
        cases = [
            ({'genres_filter': '-rock'}, [{'name': 'rock', 'count': 100}], 'filtered'),
            ({'max_genres': 0}, [{'name': 'rock', 'count': 100}], 'filtered'),
            ({'min_genre_usage': 101}, [{'name': 'rock', 'count': 100}], 'filtered'),
            ({}, [{'name': 'rock', 'count': 0}], 'filtered'),
            ({}, [{'name': None, 'count': 1}], 'filtered'),
            ({}, [{'name': 'rock', 'count': 100}], 'ready'),
        ]
        for settings, entries, expected in cases:
            with self.subTest(settings=settings, entries=entries):
                self.set_config_values(dict(genres_filter='', max_genres=5, min_genre_usage=50))
                self.set_config_values(settings)
                request = self.search('genre')
                self.respond(genres=entries)
                self.assertEqual(expected, request.providers['MusicBrainz'].state)

    def test_genre_lastfm_states(self):
        self.set_config_values({'smart_genre_lastfm_enabled': True, 'lastfm_api_key': 'test-key'})
        cases = [({'error': 10}, 'not_configured'), ({'error': 11}, 'error'), ({'error': 6}, 'empty'), ({}, 'empty')]
        for document, expected in cases:
            with self.subTest(document=document):
                request = self.search('genre')
                self.respond(genres=[])
                self.tagger.webservice.get_url.call_args.kwargs['handler'](document, None, None)
                self.assertEqual(expected, request.providers['Last.fm'].state)
                self.assertFalse(request.active)
        self.set_config_values({'only_my_genres': True})
        request = self.search('genre')
        self.assertEqual('not_configured', request.providers['Last.fm'].state)

    def test_genre_timeout_ignores_late_response(self):
        request = self.search('genre')
        callback = self.tagger.mb_api.get_track_by_id.call_args.args[1]
        request._timeout()
        callback({'id': 'rec', 'genres': [{'name': 'rock', 'count': 100}]}, None, None)
        self.assertEqual('error', request.state)
        self.assertEqual('error', request.providers['MusicBrainz'].state)
        self.assertEqual([], request.candidates)
        self.assertEqual('', self.file.metadata['genre'])

    def test_genre_recording_release_and_group_are_all_queried(self):
        self.file.smart_scan_decision = SimpleNamespace(
            recording_id='rec', release={'id': 'rel', 'release-group': {'id': 'rg'}},
        )
        self.file.metadata['genre'] = 'My Genre'
        request = self.search('genre')
        before = Metadata(self.file.metadata)
        with patch.object(File, 'save') as save:
            self.respond(genres=[{'name': 'pop', 'count': 100}])
            self.assertTrue(request.active)
            self.tagger.mb_api.get_release_by_id.call_args.args[1]({
                'id': 'rel', 'genres': [],
                'release-group': {'id': 'rg', 'genres': [{'name': 'rock', 'count': 100}]},
            }, None, None)
        self.assertFalse(request.active)
        self.assertEqual(['Pop', 'Rock'], [c.values[0] for c in request.candidates])
        self.assertEqual('MusicBrainz release group', request.candidates[1].evidence[0].source)
        self.assertFalse(request.candidates[1].exact)
        self.assertEqual(before, self.file.metadata)
        save.assert_not_called()

    def test_unsupported_fields_report_limitation_without_requests(self):
        for tag in ('bpm', 'comment', '~length', 'mycustomtag'):
            request = TagSearchRequest(self.file, tag)
            request.start()
            self.assertEqual('unsupported', request.state)
        self.tagger.webservice.get_url.assert_not_called()
        self.tagger.mb_api.get_track_by_id.assert_not_called()
        self.tagger.mb_api.find_tracks.assert_not_called()

    def test_field_genre_query_preserves_existing_until_multiple_selection(self):
        self.file.metadata['genre'] = 'Existing'
        request = self.search('genre')
        before = Metadata(self.file.metadata)
        self.respond(genres=[{'name': 'rock', 'count': 10}, {'name': 'pop', 'count': 7}])
        self.assertEqual('ready', request.state)
        self.assertEqual(before, self.file.metadata)
        self.assertEqual(2, len(request.candidates))
        self.assertTrue(request.apply(request.candidates))
        before['genre'] = ['Pop', 'Rock']
        self.assertEqual(before, self.file.metadata)

    def test_field_genre_cancellation_does_not_touch_existing_genre(self):
        self.file.metadata['genre'] = 'Existing'
        request = self.search('genre')
        request.cancel()
        self.respond(genres=[{'name': 'rock', 'count': 10}])
        self.assertEqual('Existing', self.file.metadata['genre'])
        self.assertEqual([], request.candidates)

    def test_field_context_search_without_recording_id(self):
        self.file.metadata['artist'] = 'Artist'
        self.file.metadata['title'] = 'Song'
        request = TagSearchRequest(self.file, 'artist')
        request.start()
        query = self.tagger.mb_api.find_tracks.call_args.kwargs
        self.assertEqual(('Artist', 'Song'), (query['artist'], query['track']))
        self.tagger.mb_api.find_tracks.call_args.args[0]({'recordings': [
            dict(id='rec', title='Song', **{'artist-credit': credit('Artist')}),
        ]}, None, None)
        self.assertEqual(('Artist',), request.candidates[0].values)
        self.assertEqual('', self.file.metadata['musicbrainz_recordingid'])

    def test_field_selection_never_saves_or_opens_browser(self):
        request = self.search('artist')
        self.respond()
        with patch.object(File, 'save') as save, patch('picard.util.webbrowser2.open') as browser:
            self.assertTrue(request.apply([request.candidates[0]]))
        save.assert_not_called()
        browser.assert_not_called()

    def editor(self):
        box = MagicMock()
        box.files = {self.file}
        box.tag_diff.tag_names = list(PRIMARY_TAGS)
        box.tag_diff.new.__getitem__.side_effect = lambda tag: self.file.metadata.getall(tag)
        box.tag_diff.new.status.return_value = SimpleNamespace(is_grouped=False)
        box._tag_is_editable.side_effect = lambda tag: not tag.startswith('~') or tag == '~rating'
        box._validate_values.return_value = True
        box.item.side_effect = lambda row, column: QtWidgets.QTableWidgetItem(
            (self.file.orig_metadata if column == 1 else self.file.metadata)[PRIMARY_TAGS[row]]
        )
        editor = TagEditor(box)
        self.addCleanup(editor.deleteLater)
        self.addCleanup(lambda: [dialog.reject() for dialog in list(editor._find_dialogs.values())])
        return editor, box

    def test_editor_buttons_readonly_and_hidden_selection_refresh(self):
        editor, box = self.editor()
        self.assertFalse(editor._find_buttons['artist'].isHidden())
        self.assertTrue(editor._find_buttons['~length'].isHidden())
        self.assertFalse(editor._find_buttons['~length'].isEnabled())
        self.file.ui_item = None
        self.file.metadata['title'] = 'Updated in background'
        self.file.metadata_updated.emit()
        QtCore.QCoreApplication.processEvents()
        box.update.assert_called()
        editor.refresh()
        self.assertEqual('Updated in background', editor._rows['title'][2].text())

    def test_editor_field_dialog_is_singleton_and_cancels_on_selection_change(self):
        self.file.metadata['musicbrainz_recordingid'] = 'rec'
        editor, box = self.editor()
        editor._find_tag('artist')
        first = editor._find_dialogs['artist']
        editor._find_tag('artist')
        self.assertIs(first, editor._find_dialogs['artist'])
        self.tagger.mb_api.get_track_by_id.assert_called_once()
        box.files = set()
        editor.refresh()
        self.assertEqual('cancelled', first.request.state)
        self.assertEqual({}, editor._find_dialogs)

    def test_genre_dialog_supports_multiple_real_values(self):
        self.file.metadata['musicbrainz_recordingid'] = 'rec'
        self.file.metadata['genre'] = 'Existing'
        editor, box = self.editor()
        editor._find_tag('genre')
        dialog = editor._find_dialogs['genre']
        self.respond(genres=[{'name': 'rock', 'count': 10}, {'name': 'pop', 'count': 7}])
        self.assertEqual('Existing', self.file.metadata['genre'])
        self.assertEqual(2, dialog.values.count())
        for index in range(dialog.values.count()):
            dialog.values.item(index).setSelected(True)
        self.assertTrue(dialog.apply_button.isEnabled())
        dialog._apply()
        self.assertEqual(['Pop', 'Rock'], self.file.metadata.getall('genre'))

    def test_metadata_box_ignores_out_of_order_worker_results(self):
        box = MagicMock()
        box.selection_dirty = False
        box.editing = False
        box.ignore_updates = False
        box._update_generation = 0
        box.palette.return_value = QtWidgets.QApplication.palette()
        with patch('picard.ui.metadatabox.thread.run_task') as run_task:
            MetadataBox._update_metadata(box)
            old = run_task.call_args.args[1]
            MetadataBox._update_metadata(box)
            new = run_task.call_args.args[1]
        old('stale', None)
        new('current', None)
        box._update_items.assert_called_once_with('current', None)

    def test_field_search_is_in_existing_progress_lifetime(self):
        request = self.search('title')
        results = MagicMock()
        results._targets = [self.file]
        self.file.state = File.State.NORMAL
        self.assertTrue(MatchingResults._has_active_operation(results))
        request.cancel()
        self.assertFalse(MatchingResults._has_active_operation(results))

    def test_draft_in_one_field_does_not_freeze_other_new_tag_suggestions(self):
        editor, box = self.editor()
        editor._rows['comment'][2].setPlainText('Uncommitted user draft')
        self.assertIn('comment', editor._dirty)
        self.file.metadata['title'] = 'Updated Title'
        editor.refresh()
        self.assertEqual('Updated Title', editor._rows['title'][2].text())
        self.assertEqual('Uncommitted user draft', editor._rows['comment'][2].toPlainText())
        self.assertEqual('Manual', editor._source_labels['comment'].text())

    def test_repeat_scan_can_refresh_automatic_values(self):
        self.matched()
        self.file.cancel_smart_genre()
        self.file.start_smart_scan()
        node = acoustid_recording('rec', 'SONG', 'Artist', 232000, 0.95)
        self.tagger._acoustid.analyze.call_args.args[1]({'recordings': [node]}, None, None)
        self.assertEqual('SONG', self.file.metadata['title'])
        self.assertEqual('MusicBrainz', source_for(self.file, 'title'))
