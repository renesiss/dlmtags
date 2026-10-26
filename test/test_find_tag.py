# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""Find Tag across MusicBrainz, Discogs and Last.fm, with mocked APIs only."""

from types import SimpleNamespace
from unittest.mock import (
    MagicMock,
    patch,
)
from urllib.parse import (
    parse_qs,
    urlsplit,
)

from PyQt6 import QtWidgets

from test.picardtestcase import PicardTestCase
from test.test_smartscan import (
    BASE_SETTINGS,
    credit,
    file_metadata,
    release,
)

from picard import discogsgenre
from picard.file import File
from picard.metadata import Metadata
from picard.tagprovenance import (
    provider_name,
    source_for,
)
from picard.tagsearch import (
    DISCOGS_MAX_PAGES,
    MB_BROWSE_PAGE,
    TagSearchRequest,
    filter_candidates,
)
from picard.util import tracknum_and_title_from_filename

from picard.ui.dlmtags.find_tag import FindTagDialog
from picard.ui.dlmtags.tag_editor import PRIMARY_TAGS
from picard.ui.dlmtags.tag_form import TagEditor


def discogs_result(ident, title, *, year='2024', descriptions=('Single',), **extra):
    node = {
        'id': ident,
        'title': title,
        'year': year,
        'country': 'US',
        'format': ['File', 'FLAC'],
        'formats': [{'name': 'File', 'descriptions': list(descriptions)}],
        'uri': '/release/%d-x' % ident,
        'label': ['Interscope'],
        'catno': 'none',
    }
    node.update(extra)
    return node


class FindTagTest(PicardTestCase):
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
                smart_genre_lastfm_enabled=True,
                lastfm_api_key='test-key',
                dlmtags_discogs_enabled=True,
                dlmtags_discogs_token='test-token',
            )
        )
        self.patch_tagger_instance('picard.item')
        self.tagger.window = MagicMock()
        self.tagger.mb_api = MagicMock()
        self.tagger.webservice = MagicMock()
        self.tagger.albums = {}
        self.file = File('/music/Lady Gaga & Bruno Mars - Die With a Smile.mp3')
        self.file.state = File.State.NORMAL
        self.file.orig_metadata = file_metadata(title='Die With a Smile')
        self.file.metadata = Metadata(self.file.orig_metadata)
        self.file.metadata['artist'] = 'Lady Gaga & Bruno Mars'
        self.file.metadata['musicbrainz_recordingid'] = 'rec'

    def tearDown(self):
        for request in list(self.file._tag_search_requests.values()):
            request.cancel()
        super().tearDown()

    # Helpers

    def search(self, tag):
        request = TagSearchRequest(self.file, tag)
        request.start()
        return request

    def web(self, host):
        """Latest Picard web service handler for a provider host."""
        for call in reversed(self.tagger.webservice.get_url.call_args_list):
            if host in call.kwargs['url'].host():
                return call.kwargs
        raise AssertionError('no request for ' + host)

    def lastfm(self, document, error=None):
        self.web('audioscrobbler.com')['handler'](document, None, error)

    def discogs(self, document, error=None):
        self.web('discogs.com')['handler'](document, None, error)

    def browse(self, releases, count=None, error=None):
        self.tagger.mb_api.browse_releases.call_args.args[0](
            {
                'releases': releases,
                'release-count': len(releases) if count is None else count,
            },
            None,
            error,
        )

    def recording(self, **extra):
        node = dict(id='rec', title='Die With a Smile', length=251000, **extra)
        node.setdefault('artist-credit', credit('Lady Gaga', 'Bruno Mars', joinphrase=' & '))
        self.tagger.mb_api.get_track_by_id.call_args.args[1](node, None, None)

    def album_search(self):
        request = self.search('album')
        self.browse(
            [
                release('single', 'Die With a Smile', '2024-08-16', primary='Single'),
                release('mayhem', 'MAYHEM', '2025-03-07'),
                release('hits', 'Die With a Smile', '2025', secondary=['Compilation']),
            ]
        )
        self.discogs(
            {
                'pagination': {'page': 1, 'pages': 1},
                'results': [
                    discogs_result(1, 'Lady Gaga & Bruno Mars - Die With A Smile'),
                    discogs_result(2, 'Someone Else - Die With A Smile'),
                ],
            }
        )
        self.lastfm(
            {
                'track': {
                    'name': 'Die With a Smile',
                    'artist': {'name': 'Lady Gaga & Bruno Mars'},
                    'url': 'https://www.last.fm/music/x/_/y',
                    'album': {'title': 'Die With a Smile', 'url': 'https://www.last.fm/music/x/y'},
                }
            }
        )
        return request

    def editor(self):
        box = MagicMock()
        box.files = {self.file}
        box.tag_diff.tag_names = list(PRIMARY_TAGS)
        box.tag_diff.new.__getitem__.side_effect = lambda tag: self.file.metadata.getall(tag)
        box.tag_diff.new.status.return_value = SimpleNamespace(is_grouped=False)
        box._tag_is_editable.side_effect = lambda tag: not tag.startswith('~')
        box._validate_values.return_value = True
        box.item.side_effect = lambda row, column: QtWidgets.QTableWidgetItem(
            (self.file.orig_metadata if column == 1 else self.file.metadata)[PRIMARY_TAGS[row]]
        )
        editor = TagEditor(box)
        self.addCleanup(editor.deleteLater)
        self.addCleanup(lambda: [dialog.reject() for dialog in list(editor._find_dialogs.values())])
        return editor, box

    def dialog(self, tag):
        dialog = FindTagDialog(self.file, tag)
        self.addCleanup(lambda: dialog.isVisible() and dialog.reject())
        dialog.start()
        return dialog

    # Manual Internet fallback

    def finish_empty_album(self):
        self.browse([])
        self.discogs({'pagination': {'page': 1, 'pages': 1}, 'results': []})
        self.lastfm({'error': 6})

    def test_internet_fallback_waits_for_all_providers(self):
        dialog = self.dialog('album')
        self.assertTrue(dialog.internet_button.isHidden())
        self.browse([])
        self.assertTrue(dialog.internet_button.isHidden())
        self.discogs({'pagination': {'page': 1, 'pages': 1}, 'results': []})
        self.assertTrue(dialog.request.active)
        self.assertTrue(dialog.internet_button.isHidden())
        self.lastfm({'error': 6})
        self.assertFalse(dialog.request.active)
        self.assertFalse(dialog.internet_button.isHidden())
        self.assertFalse(dialog.apply_button.isEnabled())

    def test_internet_fallback_hidden_when_results_are_filtered_out(self):
        dialog = self.dialog('album')
        self.browse([release('mayhem', 'MAYHEM', '2025')])
        self.discogs({'pagination': {'page': 1, 'pages': 1}, 'results': []})
        self.lastfm({'error': 6})
        dialog.filter_edit.setText('no matching result')
        self.assertEqual(0, dialog.values.count())
        self.assertTrue(dialog.internet_button.isHidden())

    def test_internet_search_queries_use_resolved_identity_and_field(self):
        self.loaded_file('P!nk - 90 Days (Official Video).mp4')
        for tag, suffix in {
            'genre': 'music genre',
            'style': 'music style',
            'date': 'release year',
            'album': 'album',
        }.items():
            with self.subTest(tag=tag):
                dialog = self.dialog(tag)
                # Finish the real request through its providers; unsupported
                # providers have already settled synchronously.
                for provider in dialog.request.providers.values():
                    if provider.busy:
                        provider.done('empty')
                with patch('picard.util.webbrowser2.open') as browser:
                    dialog.internet_button.click()
                browser.assert_called_once()
                query = parse_qs(urlsplit(browser.call_args.args[0]).query)
                self.assertEqual({'q': ['"P!nk" "90 Days" ' + suffix]}, query)
                dialog.reject()

    def test_internet_search_unicode_encoding_and_no_automatic_changes(self):
        self.loaded_file('track.mp3', artist='Σάκης & P!nk', title="Σ’ έχω + ερωτευτεί? #90/Days")
        self.file.metadata['musicbrainz_recordingid'] = 'rec'
        before = Metadata(self.file.metadata)
        original = Metadata(self.file.orig_metadata)
        dialog = self.dialog('album')
        with (
            patch.object(File, 'save') as save,
            patch.object(File, 'update') as update,
            patch('picard.util.webbrowser2.open') as browser,
        ):
            self.finish_empty_album()
            browser.assert_not_called()
            dialog.internet_button.click()
            browser.assert_called_once()
            url = browser.call_args.args[0]
            self.assertTrue(url.isascii())
            self.assertIn('%26', url)
            self.assertIn('%2B', url)
            self.assertIn('%23', url)
            self.assertEqual('', urlsplit(url).fragment)
            self.assertEqual(
                {'q': ['"Σάκης & P!nk" "Σ’ έχω + ερωτευτεί? #90/Days" album']},
                parse_qs(urlsplit(url).query),
            )
            self.assertEqual(before, self.file.metadata)
            self.assertEqual(original, self.file.orig_metadata)
            self.assertEqual({}, self.file.dlmtags_tag_sources)
            self.assertEqual([], dialog.request.applied)
            self.assertFalse(dialog.apply_button.isEnabled())
            self.assertEqual('empty', dialog.request.state)
            dialog.reject()
            save.assert_not_called()
            update.assert_not_called()

    def test_internet_search_allows_manual_paste_into_new_tags(self):
        editor, box = self.editor()
        editor._find_tag('album')
        dialog = editor._find_dialogs['album']
        with patch.object(File, 'save') as save, patch('picard.util.webbrowser2.open') as browser:
            self.finish_empty_album()
            dialog.internet_button.click()
            browser.assert_called_once()
            dialog.reject()
            control = editor._rows['album'][2]
            clipboard = QtWidgets.QApplication.clipboard()
            assert clipboard is not None
            previous = clipboard.text()
            try:
                clipboard.setText('Manually researched album')
                control.selectAll()
                control.paste()
                self.assertTrue(editor.commit_pending())
            finally:
                clipboard.setText(previous)
            box._set_tag_values.assert_called_once_with('album', ['Manually researched album'])
            save.assert_not_called()

    def test_internet_fallback_after_unconfigured_providers_settle(self):
        self.set_config_values({'dlmtags_discogs_token': '', 'lastfm_api_key': ''})
        dialog = self.dialog('album')
        self.assertTrue(dialog.internet_button.isHidden())
        self.browse([])
        self.assertFalse(dialog.internet_button.isHidden())

    def test_internet_fallback_cancelled_request_ignores_late_callbacks(self):
        dialog = self.dialog('album')
        late = self.web('audioscrobbler.com')['handler']
        dialog.reject()
        late({'error': 6}, None, None)
        self.assertTrue(dialog.internet_button.isHidden())
        with patch('picard.util.webbrowser2.open') as browser:
            dialog.internet_button.click()
        browser.assert_not_called()

    # Providers and merging

    def test_all_configured_providers_are_queried_in_parallel(self):
        request = self.search('album')
        self.tagger.mb_api.browse_releases.assert_called_once()
        hosts = {call.kwargs['url'].host() for call in self.tagger.webservice.get_url.call_args_list}
        self.assertEqual({'api.discogs.com', 'ws.audioscrobbler.com'}, hosts)
        self.assertEqual({'searching'}, {p.state for p in request.providers.values()})
        self.assertTrue(request.active)

    def test_identical_values_merge_with_every_supporting_provider(self):
        request = self.album_search()
        self.assertEqual('ready', request.state)
        first = request.candidates[0]
        self.assertEqual(('Die With a Smile',), first.values)
        self.assertEqual(('MusicBrainz', 'Discogs', 'Last.fm'), first.providers)
        self.assertIn('Single', first.detail)
        self.assertIn('2024', first.detail)
        self.assertEqual('https://musicbrainz.org/release/single', first.url)
        self.assertEqual(
            {'MusicBrainz', 'Discogs', 'Last.fm'},
            {e.provider for e in first.evidence},
        )

    def test_compilation_is_never_merged_with_original_release(self):
        request = self.album_search()
        same_title = [c for c in request.candidates if c.values == ('Die With a Smile',)]
        self.assertEqual(2, len(same_title))
        compilation = next(c for c in same_title if c.compilation)
        self.assertEqual(('MusicBrainz',), compilation.providers)
        self.assertIn('Compilation', compilation.detail)
        self.assertGreater(request.candidates.index(compilation), request.candidates.index(same_title[0]))

    def test_exact_matches_rank_before_loose_matches(self):
        request = self.search('album')
        self.browse([])
        self.discogs(
            {
                'pagination': {'page': 1, 'pages': 1},
                'results': [
                    discogs_result(1, 'Lady Gaga & Bruno Mars - Loose Only'),
                ],
            }
        )
        self.lastfm(
            {
                'track': {
                    'name': 'Die With a Smile',
                    'artist': {'name': 'Lady Gaga & Bruno Mars'},
                    'album': {'title': 'Exact Album'},
                }
            }
        )
        self.assertEqual(['Exact Album', 'Loose Only'], [c.values[0] for c in request.candidates])
        self.assertIn('loose match', request.candidates[1].detail)

    def test_mismatched_discogs_artist_is_not_attributed(self):
        request = self.album_search()
        descriptions = [e.description for c in request.candidates for e in c.evidence if e.provider == 'Discogs']
        self.assertEqual(1, len(descriptions))

    def test_different_tag_types_use_the_right_providers(self):
        cases = {
            'title': {'MusicBrainz', 'Last.fm'},
            'artist': {'MusicBrainz', 'Last.fm'},
            'album': {'MusicBrainz', 'Discogs', 'Last.fm'},
            'date': {'MusicBrainz', 'Discogs'},
            'label': {'MusicBrainz', 'Discogs'},
            'tracknumber': {'MusicBrainz'},
            'genre': {'MusicBrainz', 'Discogs', 'Last.fm'},
            'style': {'Discogs'},
        }
        for tag, expected in cases.items():
            with self.subTest(tag=tag):
                request = self.search(tag)
                searching = {name for name, p in request.providers.items() if p.state == 'searching'}
                self.assertEqual(expected, searching)
                unsupported = {name for name, p in request.providers.items() if p.state == 'unsupported'}
                self.assertEqual(set(request.providers) - expected, unsupported)
                request.cancel()

    def test_title_from_recording_and_lastfm(self):
        request = self.search('title')
        self.recording()
        self.lastfm({'track': {'name': 'Die With A Smile', 'artist': {'name': 'Lady Gaga & Bruno Mars'}}})
        self.assertEqual(1, len(request.candidates))
        self.assertEqual(('Die With a Smile',), request.candidates[0].values, 'MusicBrainz spelling is kept')
        self.assertEqual('MusicBrainz + Last.fm', request.candidates[0].source)

    def test_track_number_from_browsed_release_tracklist(self):
        request = self.search('tracknumber')
        self.assertIn('recordings', self.tagger.mb_api.browse_releases.call_args.kwargs['inc'])
        node = release('mayhem', 'MAYHEM', '2025')
        node['media'] = [
            {
                'format': 'CD',
                'position': 1,
                'track-count': 14,
                'tracks': [
                    {'id': 't1', 'position': 1, 'recording': {'id': 'other'}},
                    {'id': 't14', 'position': 14, 'recording': {'id': 'rec'}},
                ],
            }
        ]
        self.browse([node])
        self.assertEqual([('14',)], [c.values for c in request.candidates])

    def test_genre_from_every_provider_and_style_kept_separate(self):
        request = self.search('genre')
        self.recording(genres=[{'name': 'pop', 'count': 100}, {'name': 'soul', 'count': 60}])
        self.lastfm({'toptags': {'tag': [{'name': 'Pop', 'count': 100}, {'name': 'seen live', 'count': 90}]}})
        self.discogs(
            {
                'pagination': {'page': 1, 'pages': 1},
                'results': [
                    discogs_result(5, 'Lady Gaga & Bruno Mars - Die With A Smile', genre=['Pop'], style=['Ballad']),
                ],
            }
        )
        self.discogs(
            {
                'title': 'Die With A Smile',
                'uri': 'https://www.discogs.com/release/5',
                'artists': [{'name': 'Lady Gaga & Bruno Mars'}],
                'tracklist': [{'type_': 'track', 'title': 'Die With a Smile'}],
                'genres': ['Pop', 'Rock'],
                'styles': ['Ballad'],
            }
        )
        self.assertFalse(request.active)
        values = {c.values[0]: c.providers for c in request.candidates}
        self.assertEqual(('MusicBrainz', 'Discogs', 'Last.fm'), values['Pop'])
        self.assertEqual(('Discogs',), values['Rock'])
        self.assertEqual(('MusicBrainz',), values['Soul'])
        self.assertNotIn('Ballad', values)
        self.assertNotIn('Seen Live', values)

    def test_discogs_genre_requires_verified_single_track_release(self):
        request = self.search('style')
        self.discogs(
            {
                'pagination': {'page': 1, 'pages': 1},
                'results': [
                    discogs_result(5, 'Lady Gaga & Bruno Mars - MAYHEM', style=['Ballad']),
                ],
            }
        )
        self.discogs(
            {
                'artists': [{'name': 'Lady Gaga & Bruno Mars'}],
                'tracklist': [{'type_': 'track', 'title': 'Die With a Smile'}, {'type_': 'track', 'title': 'Other'}],
                'styles': ['Ballad'],
            }
        )
        self.assertEqual([], request.candidates)
        self.assertEqual('empty', request.state)

    # Provider availability and failures

    def test_provider_failure_while_others_succeed(self):
        request = self.search('album')
        self.discogs({}, error=3)
        self.browse([release('mayhem', 'MAYHEM', '2025')])
        self.lastfm({'error': 6, 'message': 'Track not found'})
        self.assertEqual('ready', request.state)
        self.assertEqual('error', request.providers['Discogs'].state)
        self.assertEqual('empty', request.providers['Last.fm'].state)
        self.assertEqual('ready', request.providers['MusicBrainz'].state)
        self.assertEqual([('MAYHEM',)], [c.values for c in request.candidates])
        self.assertEqual(('MusicBrainz',), request.candidates[0].providers)

    def test_unconfigured_providers_are_reported_and_not_queried(self):
        self.set_config_values({'dlmtags_discogs_token': '', 'lastfm_api_key': ''})
        request = self.search('album')
        self.tagger.webservice.get_url.assert_not_called()
        self.assertEqual('not_configured', request.providers['Discogs'].state)
        self.assertEqual('not_configured', request.providers['Last.fm'].state)
        self.assertEqual('searching', request.providers['MusicBrainz'].state)

    def test_all_providers_fail_reports_error(self):
        request = self.search('album')
        self.browse([], error=3)
        self.discogs({}, error=3)
        self.lastfm({}, error=3)
        self.assertEqual('error', request.state)
        self.assertEqual([], request.candidates)

    def test_timeout_marks_pending_providers_and_keeps_results(self):
        request = self.search('album')
        self.browse([release('mayhem', 'MAYHEM', '2025')])
        late = self.web('discogs.com')['handler']
        request._timeout()
        late({'pagination': {}, 'results': [discogs_result(1, 'Lady Gaga & Bruno Mars - Late')]}, None, None)
        self.assertEqual('ready', request.state)
        self.assertEqual('error', request.providers['Discogs'].state)
        self.assertEqual([('MAYHEM',)], [c.values for c in request.candidates])

    # Filtering

    def test_search_within_results_and_provider_filter(self):
        request = self.album_search()
        self.assertEqual(['MAYHEM'], [c.values[0] for c in filter_candidates(request.candidates, 'mayh')])
        self.assertEqual(3, len(filter_candidates(request.candidates, '')))
        discogs = filter_candidates(request.candidates, provider='Discogs')
        self.assertEqual([('Die With a Smile',)], [c.values for c in discogs])
        lastfm = filter_candidates(request.candidates, 'mayhem', 'Last.fm')
        self.assertEqual([], lastfm)
        # Evidence (e.g. compilation, year) is searchable too.
        self.assertTrue(all(c.compilation for c in filter_candidates(request.candidates, 'compilation')))

    def test_dialog_filters_list(self):
        dialog = self.dialog('album')
        self.browse(
            [
                release('single', 'Die With a Smile', '2024', primary='Single'),
                release('mayhem', 'MAYHEM', '2025'),
            ]
        )
        self.discogs({'pagination': {'page': 1, 'pages': 1}, 'results': []})
        self.lastfm({'error': 6})
        self.assertEqual(2, dialog.values.count())
        dialog.filter_edit.setText('mayhem')
        self.assertEqual(1, dialog.values.count())
        dialog.filter_edit.setText('')
        dialog.source_filter.setCurrentIndex(dialog.source_filter.findData('Discogs'))
        self.assertEqual(0, dialog.values.count())
        self.assertIs(dialog.empty_label, dialog.list_stack.currentWidget())

    # Pagination

    def test_musicbrainz_load_more_uses_offset_and_stops(self):
        request = self.search('album')
        self.discogs({'pagination': {'page': 1, 'pages': 1}, 'results': []})
        self.lastfm({'error': 6})
        page = [release('r%d' % i, 'Album %d' % i, '2020') for i in range(MB_BROWSE_PAGE)]
        self.browse(page, count=MB_BROWSE_PAGE + 2)
        self.assertTrue(request.has_more)
        self.assertTrue(request.providers['MusicBrainz'].has_more)
        self.assertFalse(request.providers['Last.fm'].has_more)
        self.assertTrue(request.load_more())
        self.assertEqual(MB_BROWSE_PAGE, self.tagger.mb_api.browse_releases.call_args.kwargs['offset'])
        self.assertEqual(
            1,
            len([c for c in self.tagger.webservice.get_url.call_args_list if 'discogs' in c.kwargs['url'].host()]),
            'no new Discogs request',
        )
        self.browse([release('r98', 'Album 98', '2020'), release('r99', 'Album 99', '2020')], count=MB_BROWSE_PAGE + 2)
        self.assertFalse(request.has_more)
        self.assertEqual(MB_BROWSE_PAGE + 2, len(request.candidates))
        self.assertFalse(request.load_more())

    def test_discogs_pagination_is_bounded(self):
        self.set_config_values({'lastfm_api_key': ''})
        request = self.search('label')
        self.browse([])
        for page in range(1, DISCOGS_MAX_PAGES + 1):
            self.assertEqual(page, self.web('discogs.com')['unencoded_queryargs']['page'])
            self.discogs(
                {
                    'pagination': {'page': page, 'pages': 50},
                    'results': [
                        discogs_result(page, 'Lady Gaga & Bruno Mars - Die With A Smile', label=['Label %d' % page]),
                    ],
                }
            )
            self.assertEqual(page < DISCOGS_MAX_PAGES, request.has_more)
            request.load_more()
        self.assertEqual(DISCOGS_MAX_PAGES, len(request.candidates))
        self.assertFalse(request.active)

    def test_discogs_token_is_sent_in_header_not_url(self):
        self.search('album')
        call = self.web('discogs.com')
        self.assertEqual('Discogs token=test-token', call['headers']['Authorization'])
        self.assertNotIn('test-token', call['url'].toString())
        self.assertNotIn('test-token', str(call.get('unencoded_queryargs')))

    def test_dialog_load_more_button(self):
        dialog = self.dialog('album')
        self.discogs({'pagination': {'page': 1, 'pages': 1}, 'results': []})
        self.lastfm({'error': 6})
        self.browse([release('r1', 'Album 1', '2020')], count=40)
        self.assertFalse(dialog.load_more_button.isHidden())
        dialog.load_more_button.click()
        self.assertTrue(dialog.request.active)
        self.assertTrue(dialog.load_more_button.isHidden())
        self.browse([release('r2', 'Album 2', '2020')], count=2)
        self.assertTrue(dialog.load_more_button.isHidden())
        self.assertEqual(2, dialog.values.count())

    # Selection and apply

    def test_selecting_shows_full_value_and_sources(self):
        dialog = self.dialog('album')
        self.browse([release('single', 'Die With a Smile', '2024', primary='Single')])
        self.discogs(
            {
                'pagination': {'page': 1, 'pages': 1},
                'results': [
                    discogs_result(1, 'Lady Gaga & Bruno Mars - Die With A Smile'),
                ],
            }
        )
        self.lastfm({'error': 6})
        self.assertEqual('FIND ALBUM', dialog.heading.text())
        self.assertIn('Lady Gaga & Bruno Mars — Die With a Smile', dialog.song.text())
        dialog.values.item(0).setSelected(True)
        self.assertEqual('Die With a Smile', dialog.detail_value.text())
        self.assertIn('MusicBrainz', dialog.evidence.text())
        self.assertIn('Discogs', dialog.evidence.text())
        self.assertIn('https://musicbrainz.org/release/single', dialog.evidence.text())
        self.assertTrue(dialog.apply_button.isEnabled())
        self.assertIn('1 result', dialog.provider_labels['MusicBrainz'].text())
        self.assertIn('No results', dialog.provider_labels['Last.fm'].text())

    def test_apply_changes_only_field_and_records_selected_providers(self):
        request = self.album_search()
        before = Metadata(self.file.metadata)
        with patch.object(File, 'save') as save, patch('picard.util.webbrowser2.open') as browser:
            self.assertTrue(request.apply([request.candidates[0]]))
        save.assert_not_called()
        browser.assert_not_called()
        before['album'] = 'Die With a Smile'
        self.assertEqual(before, self.file.metadata)
        self.assertEqual('', self.file.orig_metadata['album'])
        self.assertEqual('MusicBrainz + Discogs + Last.fm', source_for(self.file, 'album'))
        self.assertEqual('MusicBrainz + Discogs + Last.fm', provider_name(source_for(self.file, 'album')))
        self.assertEqual('https://musicbrainz.org/release/single', self.file.dlmtags_tag_urls['album'])

    def test_apply_single_provider_candidate_records_only_that_provider(self):
        request = self.album_search()
        mayhem = next(c for c in request.candidates if c.values == ('MAYHEM',))
        self.assertTrue(request.apply([mayhem]))
        self.assertEqual('MusicBrainz', source_for(self.file, 'album'))

    def test_apply_while_load_more_runs_stops_pending_requests(self):
        request = self.search('album')
        self.discogs({'pagination': {'page': 1, 'pages': 1}, 'results': []})
        self.lastfm({'error': 6})
        self.browse([release('r1', 'Album 1', '2020')], count=40)
        request.load_more()
        late = self.tagger.mb_api.browse_releases.call_args.args[0]
        self.assertTrue(request.apply([request.candidates[0]]))
        late({'releases': [release('r2', 'Late', '2020')], 'release-count': 40}, None, None)
        self.assertEqual('Album 1', self.file.metadata['album'])
        self.assertFalse(self.file.tag_search_pending)

    def test_cancel_leaves_file_unchanged(self):
        before = Metadata(self.file.metadata)
        dialog = self.dialog('album')
        self.browse([release('mayhem', 'MAYHEM', '2025')])
        dialog.values.item(0).setSelected(True)
        with patch.object(File, 'save') as save:
            dialog.reject()
        save.assert_not_called()
        self.assertEqual('cancelled', dialog.request.state)
        self.assertEqual(before, self.file.metadata)
        self.assertEqual({}, self.file.dlmtags_tag_sources)
        self.assertFalse(self.file.tag_search_pending)

    def test_switching_files_during_requests_cancels_and_ignores_late_responses(self):
        editor, box = self.editor()
        editor._find_tag('album')
        dialog = editor._find_dialogs['album']
        mb_late = self.tagger.mb_api.browse_releases.call_args.args[0]
        discogs_late = self.web('discogs.com')['handler']
        other = File('/music/Other.mp3')
        other.orig_metadata = Metadata()
        other.metadata = Metadata()
        box.files = {other}
        editor.refresh()
        self.assertEqual({}, editor._find_dialogs)
        self.assertEqual('cancelled', dialog.request.state)
        mb_late({'releases': [release('mayhem', 'MAYHEM', '2025')], 'release-count': 1}, None, None)
        discogs_late({'results': [discogs_result(1, 'Lady Gaga & Bruno Mars - X')]}, None, None)
        self.assertEqual([], dialog.request.candidates)
        self.assertEqual('', self.file.metadata['album'])
        self.assertEqual('', other.metadata['album'])

    def test_manual_edit_during_search_discards_results(self):
        request = self.search('album')
        self.file.metadata['album'] = 'My Album'
        self.browse([release('mayhem', 'MAYHEM', '2025')])
        self.assertEqual('cancelled', request.state)
        self.assertEqual([], request.candidates)
        self.assertEqual('My Album', self.file.metadata['album'])
        self.assertFalse(request.load_more())

    def test_genre_multiple_selection_from_several_providers(self):
        request = self.search('genre')
        self.recording(genres=[{'name': 'pop', 'count': 100}])
        self.lastfm({'toptags': {'tag': [{'name': 'soul', 'count': 100}]}})
        self.discogs({'pagination': {'page': 1, 'pages': 1}, 'results': []})
        self.assertTrue(request.apply(request.candidates))
        self.assertEqual(['Pop', 'Soul'], self.file.metadata.getall('genre'))
        self.assertEqual('MusicBrainz + Last.fm', source_for(self.file, 'genre'))
        self.assertEqual('', self.file.metadata['style'])

    # Discogs requests shared with Smart Genre

    def discogs_urls(self):
        return [
            call.kwargs['url'].path()
            for call in self.tagger.webservice.get_url.call_args_list
            if 'discogs.com' in call.kwargs['url'].host()
        ]

    def smart_genre_discogs(self):
        request = discogsgenre.DiscogsGenreRequest(
            self.file,
            'Lady Gaga & Bruno Mars',
            'Die With a Smile',
            'test-token',
            webservice=self.tagger.webservice,
            responses=self.file.dlmtags_discogs_responses,
        )
        results = []
        request.finished.connect(lambda values, status: results.append((values, status)))
        request.start()
        return results

    def discogs_release(self, ident):
        return {
            'id': ident,
            'uri': 'https://www.discogs.com/release/%d' % ident,
            'artists': [{'name': 'Lady Gaga & Bruno Mars'}],
            'tracklist': [{'type_': 'track', 'title': 'Die With a Smile'}],
            'genres': ['Pop'],
            'styles': ['Ballad'],
        }

    def test_smart_genre_reuses_find_tag_discogs_search(self):
        self.set_config_values({'lastfm_api_key': ''})
        self.search('album')
        self.discogs(
            {
                'pagination': {'page': 1, 'pages': 1},
                'results': [
                    discogs_result(1, 'Lady Gaga & Bruno Mars - Die With A Smile'),
                ],
            }
        )
        results = self.smart_genre_discogs()
        # The 25-result first page covers Smart Genre's 5 results: only the release is requested.
        self.assertEqual(['/database/search', '/releases/1'], self.discogs_urls())
        self.discogs(self.discogs_release(1))
        self.assertEqual([(['Pop'], 'https://www.discogs.com/release/1')], results)

    def test_find_tag_does_not_reuse_smaller_smart_genre_search(self):
        self.set_config_values({'lastfm_api_key': ''})
        self.smart_genre_discogs()
        self.discogs({'pagination': {'page': 1, 'pages': 1}, 'results': [{'id': 1}]})
        self.search('album')
        urls = self.discogs_urls()
        self.assertEqual(['/database/search', '/releases/1', '/database/search'], urls)
        self.assertEqual(5, self.tagger.webservice.get_url.call_args_list[0].kwargs['unencoded_queryargs']['per_page'])
        self.assertEqual(25, self.web('discogs.com')['unencoded_queryargs']['per_page'])

    def test_verified_release_is_fetched_once_for_both_paths(self):
        self.set_config_values({'lastfm_api_key': ''})
        self.smart_genre_discogs()
        self.discogs({'pagination': {'page': 1, 'pages': 1}, 'results': [{'id': 1}]})
        self.discogs(self.discogs_release(1))
        request = self.search('style')
        self.discogs(
            {
                'pagination': {'page': 1, 'pages': 1},
                'results': [
                    discogs_result(1, 'Lady Gaga & Bruno Mars - Die With A Smile', style=['Ballad']),
                ],
            }
        )
        self.assertEqual(['/database/search', '/releases/1', '/database/search'], self.discogs_urls())
        self.assertEqual([('Ballad',)], [c.values for c in request.candidates])
        self.assertEqual('Discogs', request.candidates[0].source)

    # Discogs and Last.fm receive the resolved artist/title (file_hints)

    def loaded_file(self, name, **tags):
        """A loaded file: embedded tags, else Picard's filename guess as title."""
        self.file = File('/music/videos/' + name)
        self.file.state = File.State.NORMAL
        self.file.orig_metadata = file_metadata(**tags)
        if 'title' not in tags:
            self.file.orig_metadata['title'] = tracknum_and_title_from_filename(name).title
        self.file.metadata = Metadata(self.file.orig_metadata)

    def provider_queries(self):
        discogs = self.web('discogs.com')['unencoded_queryargs']
        lastfm = self.web('audioscrobbler.com')['unencoded_queryargs']
        return (discogs['artist'], discogs['track']), (lastfm['artist'], lastfm['track'])

    def test_untagged_playlist_video_sends_parsed_artist_and_title(self):
        self.loaded_file('437-Lady Gaga - Lady Gaga - Bad Romance (Official Music Video) [qrO4YZeyl0I].mp4')
        request = self.search('album')
        self.assertEqual((('Lady Gaga', 'Bad Romance'),) * 2, self.provider_queries())
        self.assertEqual('Lady Gaga — Bad Romance', request.song)

    def test_untagged_artist_title_video_sends_parsed_values(self):
        self.loaded_file("173-RHINO - Sugar Ray - When It's Over (Official Music Video) [xmLv8rfunPo].mp4")
        self.search('album')
        self.assertEqual((('Sugar Ray', "When It's Over"),) * 2, self.provider_queries())

    def test_embedded_tags_are_sent_unchanged(self):
        self.loaded_file(
            '101-Uploader - Someone Else - Other Song [Xk3pQ9rT2bM].mp4',
            artist='Lady Gaga & Bruno Mars',
            title='Die With a Smile',
        )
        self.search('album')
        self.assertEqual((('Lady Gaga & Bruno Mars', 'Die With a Smile'),) * 2, self.provider_queries())

    def test_unicode_tags_with_version_are_sent_unchanged(self):
        self.loaded_file('track01.mp3', artist='Σάκης Ρουβάς', title='Σ’ έχω ερωτευτεί (Live)')
        self.search('album')
        self.assertEqual((('Σάκης Ρουβάς', 'Σ’ έχω ερωτευτεί (Live)'),) * 2, self.provider_queries())

    def greek_file(self):
        del self.file.metadata['musicbrainz_recordingid']
        self.file.metadata['artist'] = 'Σάκης Ρουβάς'
        self.file.metadata['title'] = 'Σ’ έχω ερωτευτεί'

    def test_greek_search_results_are_exact_only_when_the_text_matches(self):
        self.greek_file()
        self.set_config_values({'dlmtags_discogs_token': '', 'lastfm_api_key': ''})
        request = self.search('title')
        handler = self.tagger.mb_api.find_tracks.call_args.args[0]
        handler(
            {
                'count': 2,
                'recordings': [
                    dict(id='a', title="Σ' έχω ερωτευτεί", **{'artist-credit': credit('Σάκης Ρουβάς')}),
                    dict(id='b', title='Άλλο τραγούδι', **{'artist-credit': credit('Άννα Βίσση')}),
                ],
            },
            None,
            None,
        )
        exact = {c.values[0]: c.exact for c in request.candidates}
        self.assertEqual({"Σ' έχω ερωτευτεί": True, 'Άλλο τραγούδι': False}, exact)

    def test_greek_lastfm_track_is_not_exact_for_another_song(self):
        self.greek_file()
        self.set_config_values({'dlmtags_discogs_token': ''})
        request = self.search('album')
        self.lastfm(
            {
                'track': {
                    'name': 'Άλλο τραγούδι',
                    'artist': {'name': 'Άννα Βίσση'},
                    'album': {'title': 'Άλμπουμ'},
                }
            }
        )
        self.assertEqual([('Άλμπουμ', False)], [(c.values[0], c.exact) for c in request.candidates])

    def test_greek_discogs_artist_is_matched_not_dropped(self):
        self.greek_file()
        self.set_config_values({'lastfm_api_key': ''})
        request = self.search('album')
        self.discogs(
            {
                'pagination': {'page': 1, 'pages': 1},
                'results': [
                    discogs_result(1, 'Σάκης Ρουβάς - Το Άλμπουμ'),
                    discogs_result(2, 'Άννα Βίσση - Άλλο Άλμπουμ'),
                ],
            }
        )
        self.assertEqual(['Το Άλμπουμ'], [c.values[0] for c in request.candidates])

    def test_search_without_recording_id_paginates_with_offset(self):
        del self.file.metadata['musicbrainz_recordingid']
        self.set_config_values({'dlmtags_discogs_token': '', 'lastfm_api_key': ''})
        request = self.search('title')
        query = self.tagger.mb_api.find_tracks.call_args.kwargs
        self.assertEqual(('Lady Gaga & Bruno Mars', 'Die With a Smile'), (query['artist'], query['track']))
        self.assertNotIn('offset', query)
        handler = self.tagger.mb_api.find_tracks.call_args.args[0]
        artists = credit('Lady Gaga', 'Bruno Mars', joinphrase=' & ')
        handler(
            {
                'count': 30,
                'recordings': [
                    dict(id='a', title='Die With a Smile', **{'artist-credit': artists}),
                    dict(id='b', title='Die With a Smile (cover)', **{'artist-credit': credit('Someone')}),
                ],
            },
            None,
            None,
        )
        self.assertEqual(['Die With a Smile', 'Die With a Smile (cover)'], [c.values[0] for c in request.candidates])
        self.assertTrue(request.candidates[0].exact)
        self.assertFalse(request.candidates[1].exact)
        self.assertTrue(request.load_more())
        self.assertEqual(2, self.tagger.mb_api.find_tracks.call_args.kwargs['offset'])
        self.assertEqual('', self.file.metadata['musicbrainz_recordingid'])
