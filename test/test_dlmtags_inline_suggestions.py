# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""Album suggestions, recognition state and inline Artwork, with mocked services only."""

from types import SimpleNamespace
from unittest.mock import (
    MagicMock,
    patch,
)
import uuid

from PyQt6 import (
    QtCore,
    QtGui,
    QtNetwork,
    QtWidgets,
)
from PyQt6.QtNetwork import QNetworkReply

from test.picardtestcase import PicardTestCase
from test.test_smartscan import (
    BASE_SETTINGS,
    PINK_FILENAME,
    PINK_LENGTH,
    PINK_RECORDING,
    acoustid_recording,
    file_metadata,
    loaded_metadata,
    pink_acoustid_recordings,
    pink_musicbrainz_recordings,
    recording,
    release,
)

from picard.config import get_config
from picard.coverart.image import CaaCoverArtImage
from picard.extension_points.options_pages import ext_point_options_pages
from picard.file import File
from picard.metadata import Metadata
from picard.smartscan import (
    Outcome,
)
from picard.tagprovenance import source_for

from picard.ui.dlmtags.api_credentials import API_PROVIDERS
from picard.ui.dlmtags.artwork import (
    MAX_RELEASES,
    ArtworkSearch,
    ArtworkSuggestions,
)
from picard.ui.dlmtags.confirm_results import ConfirmResultsDialog
from picard.ui.dlmtags.suggestions import (
    Status,
    album_search_status,
    status_text,
)
from picard.ui.dlmtags.tag_editor import PRIMARY_TAGS
from picard.ui.dlmtags.tag_form import TagEditor
from picard.ui.dlmtags.workspace import MatchingResults
from picard.ui.enums import MainAction
from picard.ui.options.dialog import OptionsDialog
from picard.ui.options.dlmtags import (
    DEFAULT_VISIBLE_TAGS,
    DLMTAGS_SETTINGS_PAGE_NAMES,
    DLMtagsApiKeysOptionsPage,
    DLMtagsFileSettingsOptionsPage,
    DLMtagsGeneralOptionsPage,
    DLMtagsSettingsNavigation,
    VisibleTagsOptionsPage,
)
from picard.ui.options.genres import GenresOptionsPage


def mbid(name):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


def png(color):
    image = QtGui.QImage(8, 8, QtGui.QImage.Format.Format_RGB32)
    image.fill(QtGui.QColor(color))
    buffer = QtCore.QBuffer()
    buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, 'PNG')
    return bytes(buffer.data())


class InlineTestCase(PicardTestCase):
    def setUp(self):
        super().setUp()
        self.set_config_values(
            dict(
                BASE_SETTINGS,
                use_genres=False,
                max_genres=5,
                genres_filter='',
                join_genres='',
                folksonomy_tags=False,
                only_my_genres=False,
                min_genre_usage=50,
                smart_genre_lastfm_enabled=False,
                lastfm_api_key='',
                dlmtags_discogs_enabled=False,
                dlmtags_discogs_token='',
                caa_image_size=500,
                load_image_behavior='append',
            )
        )
        self.patch_tagger_instance('picard.item')
        self.tagger.window = MagicMock()
        self.tagger.acoustidmanager = MagicMock()
        self.tagger.isrc_submit_manager = MagicMock()
        self.tagger._acoustid = MagicMock()
        self.tagger.mb_api = MagicMock()
        self.tagger.webservice = MagicMock()
        self.tagger.albums = {}
        self.file = self.make_file('/music/Artist - Song.mp3')

    def make_file(self, path):
        file = File(path)
        file.state = File.State.NORMAL
        file.orig_metadata = file_metadata(title='Song', artist='Artist')
        file.metadata = Metadata(file.orig_metadata)
        self.addCleanup(file.cancel_tag_searches)
        return file

    def scan(self, file=None, releases=None):
        """Smart Scan matches the recording but cannot verify a release."""
        file = file or self.file
        candidate = acoustid_recording('rec', 'Song', 'Artist', 232000, 0.95, [release('rel', 'Album', '1999')])
        file.start_smart_scan()
        self.tagger._acoustid.analyze.call_args.args[1]({'recordings': [candidate]}, None, None)
        self.assertEqual(Outcome.MATCHED, file.smart_scan_decision.outcome)
        self.assertFalse(file.smart_scan_decision.release_verified)
        if releases is not None:
            self.browse(releases)
        return file

    def pink_review(self):
        self.file = File('/music/videos/' + PINK_FILENAME)
        self.file.state = File.State.NORMAL
        self.file.orig_metadata = loaded_metadata(PINK_FILENAME, PINK_LENGTH)
        self.file.metadata = Metadata(self.file.orig_metadata)
        self.file.start_smart_scan()
        self.tagger._acoustid.analyze.call_args.args[1]({'recordings': pink_acoustid_recordings()}, None, None)
        self.tagger.mb_api.get_track_by_id.call_args.args[1](pink_musicbrainz_recordings()[0], None, None)
        self.assertEqual(Outcome.NEEDS_REVIEW, self.file.smart_scan_decision.outcome)

    def browse(self, releases, error=None, call=None):
        call = call or self.tagger.mb_api.browse_releases.call_args
        call.args[0]({'releases': releases, 'release-count': len(releases)}, None, error)


class AlbumSuggestionsTest(InlineTestCase):
    RELEASES = (
        release('single', 'Song', '2024-01-01', primary='Single'),
        release('lp', 'The Album', '2024-05-01'),
    )

    def test_scan_keeps_real_candidates_without_applying_them(self):
        self.scan(releases=self.RELEASES)
        self.assertEqual(['Song', 'The Album'], [c.values[0] for c in self.file.dlmtags_album_suggestions])
        self.assertEqual('', self.file.metadata['album'])
        self.assertEqual(Status.SUGGESTED, album_search_status(self.file)[0])

    def test_applying_through_the_request_writes_only_album_and_never_saves(self):
        self.scan(releases=self.RELEASES)
        before = Metadata(self.file.metadata)
        request = self.file.dlmtags_album_search
        with patch.object(File, 'save') as save:
            self.assertTrue(request.apply([self.file.dlmtags_album_suggestions[1]]))
        save.assert_not_called()
        self.assertEqual('The Album', self.file.metadata['album'])
        for tag in set(before) - {'album'}:
            self.assertEqual(before.getall(tag), self.file.metadata.getall(tag), tag)
        self.assertEqual('MusicBrainz', source_for(self.file, 'album'))
        self.assertEqual(Status.CHOSEN, album_search_status(self.file)[0])
        # Choosing again replaces the earlier choice.
        self.assertTrue(request.apply([self.file.dlmtags_album_suggestions[0]]))
        self.assertEqual(['Song'], self.file.metadata.getall('album'))

    def test_manual_edit_blocks_stale_suggestion(self):
        self.scan(releases=self.RELEASES)
        self.file.metadata['album'] = 'Typed By User'
        self.assertFalse(self.file.dlmtags_album_search.apply([self.file.dlmtags_album_suggestions[0]]))
        self.assertEqual('Typed By User', self.file.metadata['album'])

    def test_old_scan_results_are_ignored(self):
        self.scan()
        old_call = self.tagger.mb_api.browse_releases.call_args
        self.file.clear_pending()
        self.scan()
        self.browse(self.RELEASES, call=old_call)
        self.assertEqual([], self.file.dlmtags_album_suggestions)
        self.browse(self.RELEASES)
        self.assertEqual(2, len(self.file.dlmtags_album_suggestions))

    def test_not_found_and_error_states(self):
        self.scan(releases=[])
        self.assertEqual(Status.NOT_FOUND, album_search_status(self.file)[0])
        other = self.make_file('/music/Other.mp3')
        self.scan(other)
        self.browse([], error=QNetworkReply.NetworkError.HostNotFoundError)
        self.assertEqual(Status.ERROR, album_search_status(other)[0])


class TagFormSuggestionTest(InlineTestCase):
    def editor(self):
        box = MagicMock()
        box.files = {self.file}
        box.tag_diff.tag_names = list(PRIMARY_TAGS)
        box.tag_diff.new.__getitem__.side_effect = lambda tag: self.file.metadata.getall(tag)
        box.tag_diff.new.status.return_value = SimpleNamespace(is_grouped=False)
        box._tag_is_editable.side_effect = lambda tag: not tag.startswith('~')
        box.item.side_effect = lambda row, column: QtWidgets.QTableWidgetItem(
            (self.file.orig_metadata if column == 1 else self.file.metadata)[PRIMARY_TAGS[row]]
        )
        editor = TagEditor(box)
        self.addCleanup(editor.deleteLater)
        return editor

    def test_visible_tags_hide_rows_without_changing_saved_metadata(self):
        self.file.orig_metadata['album'] = 'Original Album'
        self.file.metadata['album'] = 'Original Album'
        self.set_config_values({'dlmtags_visible_tags': list(DEFAULT_VISIBLE_TAGS)})
        editor = self.editor()
        self.assertTrue(editor._primary_pairs['album'][0].isHidden())
        self.assertTrue(editor._primary_pairs['album'][1].isHidden())
        self.assertEqual('Original Album', self.file.metadata['album'])

        self.tagger.save_thread_pool = MagicMock()
        self.tagger._saving_files_count = 0
        with patch('picard.file.thread.run_task') as run_task:
            self.file.save()

        saved_metadata = run_task.call_args.args[0].args[1]
        self.assertEqual('Original Album', saved_metadata['album'])

    def test_api_key_fields_sync_across_settings_pages(self):
        host = QtWidgets.QWidget()
        self.addCleanup(host.deleteLater)
        with patch('picard.ui.options.tagger_instance', return_value=self.tagger):
            api_page = DLMtagsApiKeysOptionsPage(host)
            genres_page = GenresOptionsPage(host)
        api_page.load()
        api_page.api_key_widgets['dlmtags_discogs_token'].setText('discogs-secret')
        genres_page.load()

        self.assertEqual('discogs-secret', api_page.api_key_widgets['dlmtags_discogs_token'].text())
        self.assertEqual('discogs-secret', genres_page.ui.dlmtags_discogs_token.text())
        genres_page.ui.dlmtags_discogs_token.setText('discogs-from-genres')
        self.assertEqual('discogs-from-genres', api_page.api_key_widgets['dlmtags_discogs_token'].text())
        api_page.api_key_widgets['lastfm_api_key'].setText('lastfm-secret')
        self.assertEqual('lastfm-secret', genres_page.ui.lastfm_api_key.text())
        api_page.save()
        config = get_config()
        self.assertEqual('discogs-from-genres', config.setting['dlmtags_discogs_token'])
        self.assertEqual('lastfm-secret', config.setting['lastfm_api_key'])
        self.assertEqual(QtWidgets.QLineEdit.EchoMode.Password, api_page.api_key_widgets['lastfm_api_key'].echoMode())

    def test_visible_tags_defaults_persist_in_field_order(self):
        with patch('picard.ui.options.tagger_instance', return_value=self.tagger):
            page = VisibleTagsOptionsPage()
        self.addCleanup(page.deleteLater)
        page.load()
        self.assertEqual(list(DEFAULT_VISIBLE_TAGS), [tag for tag, check in page.checks.items() if check.isChecked()])
        self.file.orig_metadata['album'] = 'Original Album'
        self.file.metadata['album'] = 'Original Album'
        editor = self.editor()
        editor.box.update.side_effect = editor.refresh
        page.tagger = SimpleNamespace(window=SimpleNamespace(metadata_box=editor.box))
        page.checks['album'].setChecked(True)
        page.save()
        self.assertFalse(editor._primary_pairs['album'][0].isHidden())
        self.assertFalse(editor._primary_pairs['album'][1].isHidden())
        self.assertEqual(
            ['title', 'artist', 'album', 'date', 'genre'],
            get_config().setting['dlmtags_visible_tags'],
        )
        self.assertEqual('Original Album', self.file.metadata['album'])

    def test_suggestion_is_an_orange_preview_not_a_value(self):
        self.scan(releases=[release('lp', 'The Album', '2024')])
        editor = self.editor()
        control = editor._rows['album'][2]
        self.assertEqual('', control.text())
        self.assertEqual('The Album', control.placeholderText())
        self.assertEqual('Suggested', editor._source_labels['album'].text())
        self.assertNotIn('\\n', control.toolTip())
        self.assertEqual('', self.file.metadata['album'])

    def test_unverified_artist_and_title_suggestions_in_new_tags(self):
        self.file = File('/music/videos/' + PINK_FILENAME)
        self.file.state = File.State.NORMAL
        self.file.orig_metadata = loaded_metadata(PINK_FILENAME, PINK_LENGTH)
        self.file.metadata = Metadata(self.file.orig_metadata)
        guess = self.file.metadata['title']
        self.file.start_smart_scan()
        self.tagger._acoustid.analyze.call_args.args[1]({'recordings': pink_acoustid_recordings()}, None, None)
        self.assertEqual(Outcome.NEEDS_REVIEW, self.file.smart_scan_decision.outcome)
        editor = self.editor()
        # Picard's raw file-name guess is replaced on screen by the preview.
        title = editor._rows['title'][2]
        self.assertEqual('', title.text())
        self.assertEqual('Cover Me in Sunshine', title.placeholderText())
        self.assertEqual('MusicBrainz — Unverified', editor._source_labels['title'].text())
        self.assertIn(guess, title.toolTip())
        self.assertIn('not applied', editor._source_labels['title'].toolTip())
        artist = editor._rows['artist'][2]
        self.assertEqual(('', 'P NK Willow Sage Hart'), (artist.text(), artist.placeholderText()))
        self.assertEqual('Filename — Unverified', editor._source_labels['artist'].text())
        self.tagger.mb_api.get_track_by_id.call_args.args[1](pink_musicbrainz_recordings()[0], None, None)
        editor = self.editor()
        self.assertEqual('P!nk + Willow Sage Hart', editor._rows['artist'][2].placeholderText())
        self.assertEqual('MusicBrainz — Unverified', editor._source_labels['artist'].text())
        # Previews only: New Tags keep their values until the user chooses.
        self.assertEqual(('', guess), (self.file.metadata['artist'], self.file.metadata['title']))
        self.file.metadata['title'] = 'My Title'
        editor = self.editor()
        self.assertEqual('My Title', editor._rows['title'][2].text())
        self.assertNotIn('Unverified', editor._source_labels['title'].text())

    def two_plausible_recordings(self):
        self.file.metadata.length = 216000
        self.file.start_smart_scan()
        self.tagger._acoustid.analyze.call_args.args[1]({'recordings': []}, None, None)
        self.tagger.mb_api.find_tracks.call_args.args[0](
            {
                'recordings': [
                    recording('solo', 'Song', 'Artist', 200000),
                    recording('duet', 'Song', ('Artist', 'Friend'), 200000),
                ]
            },
            None,
            None,
        )
        self.assertEqual(Outcome.NEEDS_REVIEW, self.file.smart_scan_decision.outcome)

    def test_single_candidate_opens_four_field_review_from_new_tags(self):
        self.pink_review()
        editor = self.editor()
        self.assertFalse(editor.confirm_banner.isHidden())
        self.assertEqual('Confirm', editor.confirm_results_button.text())
        self.assertIn('P!nk + Willow Sage Hart — Cover Me in Sunshine', editor.confirm_message.text())
        self.assertIn('Length mismatch', editor.confirm_message.text())
        before = Metadata(self.file.metadata)
        with patch('picard.ui.dlmtags.tag_form.ConfirmResultsDialog') as dialog, patch.object(File, 'save') as save:
            editor.confirm_results_button.click()
        dialog.assert_called_once_with(self.file, editor)
        dialog.return_value.open.assert_called_once()
        save.assert_not_called()
        self.assertEqual(before, self.file.metadata)
        self.assertEqual(Outcome.NEEDS_REVIEW, self.file.smart_scan_decision.outcome)

    def test_single_candidate_review_keeps_manual_edits_until_approved(self):
        self.pink_review()
        self.file.metadata['title'] = 'My Title'
        editor = self.editor()
        self.assertIn('Your edits to Title are kept', editor.confirm_message.text())
        with patch('picard.ui.dlmtags.tag_form.ConfirmResultsDialog') as dialog:
            editor.confirm_results_button.click()
        dialog.assert_called_once_with(self.file, editor)
        self.assertEqual(
            ('', 'My Title'),
            (self.file.metadata['artist'], self.file.metadata['title']),
        )

    def test_several_plausible_candidates_open_the_selection_dialog(self):
        self.two_plausible_recordings()
        editor = self.editor()
        self.assertEqual('Confirm', editor.confirm_results_button.text())
        self.assertIn('2 MusicBrainz recordings need your choice', editor.confirm_message.text())
        before = Metadata(self.file.metadata)
        with patch('picard.ui.dlmtags.tag_form.ConfirmResultsDialog') as dialog:
            editor.confirm_results_button.click()
        dialog.assert_called_once_with(self.file, editor)
        dialog.return_value.open.assert_called_once()
        self.assertEqual(before, self.file.metadata)
        self.assertEqual(Outcome.NEEDS_REVIEW, self.file.smart_scan_decision.outcome)

    def test_stale_banner_does_not_confirm(self):
        self.pink_review()
        editor = self.editor()
        before = Metadata(self.file.metadata)
        self.file.start_smart_scan()  # a new scan replaces the review
        with patch('picard.ui.dlmtags.tag_form.ConfirmResultsDialog') as dialog:
            editor.confirm_results_button.click()
        dialog.assert_not_called()
        self.assertEqual(before, self.file.metadata)
        self.assertIsNone(self.file.smart_scan_decision)

    def test_no_confirm_action_without_a_confirmable_recording(self):
        # Matched automatically: nothing to confirm.
        self.scan()
        self.assertTrue(self.editor().confirm_banner.isHidden())
        # Needs Review, but the MusicBrainz artist credit is still loading.
        self.file = File('/music/videos/' + PINK_FILENAME)
        self.file.state = File.State.NORMAL
        self.file.orig_metadata = loaded_metadata(PINK_FILENAME, PINK_LENGTH)
        self.file.metadata = Metadata(self.file.orig_metadata)
        self.file.start_smart_scan()
        self.tagger._acoustid.analyze.call_args.args[1]({'recordings': pink_acoustid_recordings()}, None, None)
        self.assertEqual(Outcome.NEEDS_REVIEW, self.file.smart_scan_decision.outcome)
        editor = self.editor()
        self.assertTrue(editor.confirm_banner.isHidden())
        # Failed credit lookup: still nothing that can be confirmed.
        self.tagger.mb_api.get_track_by_id.call_args.args[1](
            b'',
            None,
            QNetworkReply.NetworkError.ServiceUnavailableError,
        )
        editor.refresh()
        self.assertTrue(editor.confirm_banner.isHidden())

    def test_no_confirm_action_for_several_selected_songs(self):
        self.pink_review()
        other = self.make_file('/music/Other.mp3')
        editor = self.editor()
        editor.box.files = {self.file, other}
        editor.refresh()
        self.assertTrue(editor.confirm_banner.isHidden())

    def test_chosen_album_shows_its_source_not_suggested(self):
        self.scan(releases=[release('lp', 'The Album', '2024')])
        self.file.dlmtags_album_search.apply([self.file.dlmtags_album_suggestions[0]])
        editor = self.editor()
        self.assertEqual('The Album', editor._rows['album'][2].text())
        self.assertEqual('MusicBrainz', editor._source_labels['album'].text())
        self.assertEqual('', editor._source_labels['album'].styleSheet())

    def test_album_search_icon_is_visible_and_opens_find_tag(self):
        self.scan(releases=[release('lp', 'The Album', '2024')])
        editor = self.editor()
        button = editor._find_buttons['album']
        self.assertFalse(button.isHidden())
        self.assertTrue(button.isEnabled())
        self.assertFalse(button.icon().isNull())
        self.assertEqual('Find or change album', button.toolTip())
        self.assertGreaterEqual(button.minimumWidth(), 24)
        started = []
        editor.tag_search_started.connect(started.append)
        with patch('picard.ui.dlmtags.tag_form.FindTagDialog') as dialog:
            button.click()
        dialog.assert_called_once_with(self.file, 'album', editor)
        dialog.return_value.start.assert_called_once()
        self.assertEqual([self.file], started)
        # Opening the dialog accepts nothing: the suggestion stays a preview.
        self.assertEqual('', self.file.metadata['album'])
        self.assertEqual('Suggested', editor._source_labels['album'].text())

    def test_album_search_icon_also_shown_for_a_confirmed_album(self):
        self.file.metadata['album'] = 'Confirmed'
        editor = self.editor()
        self.assertFalse(editor._find_buttons['album'].isHidden())
        self.assertTrue(editor._find_buttons['album'].isEnabled())


class DLMtagsSettingsVisibilityTest(InlineTestCase):
    def api_keys_page(self):
        with patch('picard.ui.options.tagger_instance', return_value=self.tagger):
            page = DLMtagsApiKeysOptionsPage()
        self.addCleanup(page.deleteLater)
        page.load()
        return page

    def test_api_key_edit_connect_and_save_cancel_semantics(self):
        self.set_config_values(
            {
                'dlmtags_discogs_enabled': True,
                'dlmtags_discogs_token': 'saved-token',
            }
        )
        page = self.api_keys_page()
        edit = page.api_key_widgets['dlmtags_discogs_token']
        self.assertTrue(edit.isReadOnly())
        page.edit_buttons['discogs'].click()
        self.assertFalse(edit.isReadOnly())
        edit.setText('pending-token')
        self.assertEqual('saved-token', get_config().setting['dlmtags_discogs_token'])
        with patch('picard.ui.options.dlmtags.QtGui.QDesktopServices.openUrl') as open_url:
            page.connect_buttons['discogs'].click()
            page.connect_buttons['lastfm'].click()
        self.assertEqual(2, open_url.call_count)
        self.assertEqual(
            API_PROVIDERS['discogs']['connect_url'],
            open_url.call_args_list[0].args[0].toString(),
        )
        self.assertEqual(
            API_PROVIDERS['lastfm']['connect_url'],
            open_url.call_args_list[1].args[0].toString(),
        )
        # Without page.save(), rejecting Settings leaves the stored key unchanged.
        self.assertEqual('saved-token', get_config().setting['dlmtags_discogs_token'])
        page.save()
        self.assertEqual('pending-token', get_config().setting['dlmtags_discogs_token'])

    def test_api_key_test_requires_verified_provider_response_and_handles_timeout(self):
        class FakeReply(QtCore.QObject):
            finished = QtCore.pyqtSignal()

            def __init__(self, payload, status=200, error=QtNetwork.QNetworkReply.NetworkError.NoError):
                super().__init__()
                self.payload = payload
                self.status = status
                self.network_error = error

            def error(self):
                return self.network_error

            def errorString(self):
                return 'simulated network failure'

            def attribute(self, attribute):
                return self.status

            def readAll(self):
                return QtCore.QByteArray(self.payload)

        class FakeNetwork:
            def __init__(self, reply):
                self.reply = reply
                self.request = None

            def get(self, request):
                self.request = request
                return self.reply

        self.set_config_values(
            {
                'dlmtags_discogs_enabled': True,
                'dlmtags_discogs_token': 'test-token',
            }
        )
        page = self.api_keys_page()
        reply = FakeReply(b'{"username":"verified-user"}')
        network = FakeNetwork(reply)
        page.credential_tester._network = network
        self.assertEqual('Not Tested', page.status_labels['discogs'].text())
        self.assertEqual(
            QtWidgets.QLineEdit.EchoMode.Password,
            page.api_key_widgets['dlmtags_discogs_token'].echoMode(),
        )
        self.assertIn(
            'API Keys',
            [label.text() for label in page.findChildren(QtWidgets.QLabel) if label.property('dlmtagsApiTitle')],
        )
        for provider in ('discogs', 'lastfm'):
            for button in (
                page.edit_buttons[provider],
                page.connect_buttons[provider],
                page.test_buttons[provider],
            ):
                self.assertFalse(button.icon().isNull())
                self.assertTrue(button.property('dlmtagsApiAction'))
                self.assertTrue(button.toolTip())
        page._test_provider('discogs')
        self.assertEqual('Testing…', page.status_labels['discogs'].text())
        self.assertEqual('api.discogs.com', network.request.url().host())
        reply.finished.emit()
        self.assertEqual('Connected', page.status_labels['discogs'].text())

        timeout = FakeReply(b'', error=QtNetwork.QNetworkReply.NetworkError.TimeoutError)
        page.credential_tester._network = FakeNetwork(timeout)
        page._test_provider('discogs')
        timeout.finished.emit()
        self.assertEqual('Failed', page.status_labels['discogs'].text())
        self.assertIn('timed out', page.status_labels['discogs'].toolTip())

        invalid = FakeReply(b'{"unexpected":true}')
        page.credential_tester._network = FakeNetwork(invalid)
        page._test_provider('discogs')
        invalid.finished.emit()
        self.assertEqual('Failed', page.status_labels['discogs'].text())

        unavailable = FakeReply(b'{"error":"busy"}', status=503)
        page.credential_tester._network = FakeNetwork(unavailable)
        page._test_provider('discogs')
        unavailable.finished.emit()
        self.assertEqual('Failed', page.status_labels['discogs'].text())
        self.assertIn('503', page.status_labels['discogs'].toolTip())

        page.enable_widgets['lastfm'].setChecked(True)
        page.api_key_widgets['lastfm_api_key'].setText('lastfm-test-key')
        lastfm = FakeReply(b'{"toptags":{"tag":[]}}')
        network = FakeNetwork(lastfm)
        page.credential_tester._network = network
        page._test_provider('lastfm')
        self.assertEqual('ws.audioscrobbler.com', network.request.url().host())
        lastfm.finished.emit()
        self.assertEqual('Connected', page.status_labels['lastfm'].text())

    def test_dlmsettings_dialog_size_and_footer_controls(self):
        class DLMtagsWindow(QtWidgets.QWidget):
            modern_workspace = object()

        parent = DLMtagsWindow()
        self.addCleanup(parent.deleteLater)
        config = get_config()
        config.profiles['user_profiles'] = []
        config.profiles['user_profile_settings'] = {}
        self.tagger.get_plugin_manager.return_value = None
        self.tagger._no_restore = True
        with (
            patch('picard.ui.tagger_instance', return_value=self.tagger),
            patch('picard.ui.options.tagger_instance', return_value=self.tagger),
            patch('picard.ui.options.dialog.tagger_instance', return_value=self.tagger),
            patch('picard.util.tagger_instance', return_value=self.tagger),
        ):
            dialog = OptionsDialog(default_page='dlmtags_general', parent=parent)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(QtCore.QSize(780, 480), dialog.size())
        self.assertEqual(QtCore.QSize(620, 360), dialog.minimumSize())
        self.assertEqual(170, dialog.settings_navigation.minimumWidth())
        self.assertTrue(dialog.ui.reset_all_button.isHidden())
        self.assertTrue(dialog.ui.attached_profiles_button.isHidden())
        self.assertTrue(dialog.ui.buttonbox.button(QtWidgets.QDialogButtonBox.StandardButton.Help).isHidden())
        for width, height in ((720, 440), (1024, 700)):
            dialog.resize(width, height)
            self.assertEqual(QtCore.QSize(width, height), dialog.size())

    def test_settings_navigation_buttons_and_icons(self):
        navigation = DLMtagsSettingsNavigation()
        self.addCleanup(navigation.deleteLater)
        self.assertEqual(
            ['dlmtags_general', 'dlmtags_api_keys', 'dlmtags_visible_tags', 'dlmtags_file_settings', 'dlmtags_about'],
            list(navigation.buttons),
        )
        selected = []
        navigation.page_selected.connect(selected.append)
        for name, button in navigation.buttons.items():
            self.assertTrue(button.text())
            self.assertFalse(button.icon().isNull())
            self.assertTrue(button.property('dlmtagsSettingsNav'))
            button.click()
            self.assertEqual(name, selected[-1])
        navigation.set_current_page('dlmtags_visible_tags')
        self.assertTrue(navigation.buttons['dlmtags_visible_tags'].defaultAction().isChecked())
        self.assertFalse(navigation.buttons['dlmtags_general'].defaultAction().isChecked())

    def test_dlmsettings_tree_contains_only_curated_pages(self):
        class PageBuilder:
            def add_pages(self, parent_name, default_name, parent_item):
                return OptionsDialog.add_pages(self, parent_name, default_name, parent_item)

        builder = PageBuilder()
        builder._page_classes = list(ext_point_options_pages)
        builder.dlmtags_mode = True
        builder.item_to_page = {}
        builder.pagename_to_item = {}
        builder.default_item = None
        tree = QtWidgets.QTreeWidget()
        self.addCleanup(tree.deleteLater)
        builder.add_pages(None, 'dlmtags_general', tree)
        self.assertEqual(DLMTAGS_SETTINGS_PAGE_NAMES, set(builder.pagename_to_item))
        builder.ui = SimpleNamespace(pages_tree=tree)
        navigation = DLMtagsSettingsNavigation()
        self.addCleanup(navigation.deleteLater)
        navigation.page_selected.connect(lambda name: OptionsDialog._select_settings_nav_page(builder, name))
        navigation.buttons['dlmtags_api_keys'].click()
        self.assertIs(tree.currentItem(), builder.pagename_to_item['dlmtags_api_keys'])

        legacy_builder = PageBuilder()
        legacy_builder._page_classes = list(ext_point_options_pages)
        legacy_builder.dlmtags_mode = False
        legacy_builder.item_to_page = {}
        legacy_builder.pagename_to_item = {}
        legacy_builder.default_item = None
        legacy_tree = QtWidgets.QTreeWidget()
        self.addCleanup(legacy_tree.deleteLater)
        legacy_builder.add_pages(None, 'interface', legacy_tree)
        legacy_pages = set(legacy_builder.pagename_to_item)
        self.assertTrue({'general', 'interface', 'tags'} <= legacy_pages)
        self.assertTrue(DLMTAGS_SETTINGS_PAGE_NAMES.isdisjoint(legacy_pages))

    def test_curated_settings_leave_other_picard_configuration_intact(self):
        self.set_config_values(
            {
                'toolbar_show_labels': True,
                'clear_existing_tags': True,
                'preserve_timestamps': False,
            }
        )
        with patch('picard.ui.options.tagger_instance', return_value=self.tagger):
            general = DLMtagsGeneralOptionsPage()
            file_settings = DLMtagsFileSettingsOptionsPage()
        self.addCleanup(general.deleteLater)
        self.addCleanup(file_settings.deleteLater)
        general.load()
        general.ui.toolbar_show_labels.setChecked(False)
        general.save()
        file_settings.load()
        file_settings.checks['preserve_timestamps'].setChecked(True)
        file_settings.save()
        config = get_config()
        self.assertTrue(config.setting['toolbar_show_labels'])
        self.assertTrue(config.setting['clear_existing_tags'])
        self.assertTrue(config.setting['preserve_timestamps'])


class MatchingResultsTest(InlineTestCase):
    def genre_reply(self, document):
        calls = [
            call for call in self.tagger.mb_api.get_track_by_id.call_args_list
            if {'genres', 'tags', 'user-genres', 'user-tags'} & set(call.kwargs.get('inc') or ())
        ]
        self.assertEqual(1, len(calls))
        calls[0].args[1](document, None, None)

    def results(self):
        window = MagicMock()
        window.action_map = {
            MainAction.ANALYZE: QtGui.QAction('Scan'),
            MainAction.AUTOTAG: QtGui.QAction('Lookup'),
        }
        window.tagger.webservice.num_pending_web_requests = 0
        results = MatchingResults(window)
        self.addCleanup(results.deleteLater)
        return results

    def test_compact_status_and_details_only_on_request(self):
        results = self.results()
        results.set_files({self.file})
        self.assertTrue(results.status_label.isHidden())
        self.scan(releases=[release('lp', 'The Album', '2024')])
        results.set_files({self.file})
        self.assertEqual('Matched', results.status_label.text())
        self.assertTrue(results.details.isHidden())
        results.details_toggle.setChecked(True)
        self.assertFalse(results.details.isHidden())
        self.assertIn('Artist — Song', results.hint.text())
        self.assertIn('Album suggestions: ★ Suggested', results.hint.text())
        # No second Scan button and no album list or Use Album here.
        buttons = [b.text() for b in results.findChildren(QtWidgets.QAbstractButton)]
        self.assertNotIn('Scan', buttons)
        self.assertNotIn('Use Album', buttons)
        self.assertIsNone(results.findChild(QtWidgets.QListWidget))

    @staticmethod
    def rows(dialog):
        model = dialog.table.model()
        return [
            [model.index(row, column).data() for column in range(model.columnCount())]
            for row in range(model.rowCount())
        ]

    def test_confirm_results_dialog_confirms_or_cancels(self):
        results = self.results()
        self.pink_review()
        results.set_files({self.file})
        # One Confirm action only: the NEW TAGS banner, not the recognition header.
        buttons = [b.text() for b in results.findChildren(QtWidgets.QAbstractButton)]
        self.assertNotIn('Confirm Results…', buttons)
        dialog = ConfirmResultsDialog(self.file)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(
            [
                [
                    'P!nk + Willow Sage Hart',
                    'Cover Me in Sunshine',
                    '141.0 s',
                    '98.6%',
                    'Cover Me in Sunshine (2021-02-12)',
                    'Length mismatch',
                ]
            ],
            self.rows(dialog),
        )
        self.assertIn('Length mismatch', dialog.reason_label.text())
        self.assertTrue(dialog.confirm_button.isEnabled())  # the single plausible recording
        self.assertTrue(dialog.replace_check.isHidden())
        before = Metadata(self.file.metadata)
        dialog.reject()
        self.assertEqual(before, self.file.metadata)
        self.assertEqual(Outcome.NEEDS_REVIEW, self.file.smart_scan_decision.outcome)
        dialog = ConfirmResultsDialog(self.file)
        self.addCleanup(dialog.deleteLater)
        with patch.object(File, 'save') as save:
            dialog.accept()
        save.assert_not_called()
        self.assertEqual('P!nk + Willow Sage Hart', self.file.metadata['artist'])
        results.set_files({self.file})
        self.assertEqual('Confirmed by user', results.status_label.text())

    def test_confirm_results_reviews_year_and_async_genre_per_field(self):
        self.set_config_values({'use_genres': True, 'min_genre_usage': 1})
        self.pink_review()
        dialog = ConfirmResultsDialog(self.file)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual('', self.file.metadata['date'])
        self.assertEqual('2021-02-12', dialog.proposed_labels['date'].text())
        self.assertEqual('Empty', dialog.current_labels['date'].text())
        self.assertFalse(dialog.approve_checks['date'].isChecked())
        self.assertFalse(dialog.approve_checks['genre'].isEnabled())
        dialog.approve_checks['date'].setChecked(True)
        before = Metadata(self.file.metadata)
        dialog.accept()
        self.assertTrue(dialog._confirmed)
        self.assertEqual(before.getall('date'), self.file.metadata.getall('date'))
        self.genre_reply({'id': PINK_RECORDING, 'genres': [{'name': 'pop', 'count': 4}]})
        dialog._refresh_suggestions()
        self.assertEqual('Pop', dialog.proposed_labels['genre'].text())
        self.assertTrue(dialog.approve_checks['genre'].isEnabled())
        self.assertFalse(dialog.approve_checks['genre'].isChecked())
        dialog.approve_checks['genre'].setChecked(True)
        dialog.accept()
        self.assertEqual('2021-02-12', self.file.metadata['date'])
        self.assertEqual(['Pop'], self.file.metadata.getall('genre'))
        self.assertEqual(before.getall('album'), self.file.metadata.getall('album'))
        self.assertEqual(before.images, self.file.metadata.images)

    def test_confirm_results_can_reject_year_and_genre_after_recording_confirmation(self):
        self.set_config_values({'use_genres': True, 'min_genre_usage': 1})
        self.pink_review()
        dialog = ConfirmResultsDialog(self.file)
        self.addCleanup(dialog.deleteLater)
        dialog.accept()
        self.genre_reply({'id': PINK_RECORDING, 'genres': [{'name': 'pop', 'count': 4}]})
        dialog._refresh_suggestions()
        dialog.reject()
        self.assertEqual('', self.file.metadata['date'])
        self.assertEqual('', self.file.metadata['genre'])

    def test_confirm_results_stale_genre_review_does_not_apply_year(self):
        self.set_config_values({'use_genres': True, 'min_genre_usage': 1})
        self.pink_review()
        dialog = ConfirmResultsDialog(self.file)
        self.addCleanup(dialog.deleteLater)
        dialog.accept()
        self.genre_reply({'id': PINK_RECORDING, 'genres': [{'name': 'pop', 'count': 4}]})
        dialog._refresh_suggestions()
        dialog.approve_checks['date'].setChecked(True)
        dialog.approve_checks['genre'].setChecked(True)
        self.file.metadata['genre'] = 'My genre'
        dialog.accept()
        self.assertEqual('', self.file.metadata['date'])
        self.assertEqual('My genre', self.file.metadata['genre'])
        self.assertFalse(dialog.error_label.isHidden())

    def test_ambiguous_recordings_need_a_selection(self):
        TagFormSuggestionTest.two_plausible_recordings(self)
        dialog = ConfirmResultsDialog(self.file)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(2, len(self.rows(dialog)))
        self.assertFalse(dialog.confirm_button.isEnabled())
        dialog.table.selectRow(1)
        self.assertTrue(dialog.confirm_button.isEnabled())

    def test_manual_edit_offers_explicit_replacement(self):
        self.pink_review()
        self.file.metadata['title'] = 'My Title'
        dialog = ConfirmResultsDialog(self.file)
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.replace_check.isHidden())
        self.assertFalse(dialog.replace_check.isChecked())
        dialog.accept()
        self.assertEqual('My Title', self.file.metadata['title'])

    def test_selection_switch_shows_the_selected_files_state(self):
        results = self.results()
        self.scan(releases=[])
        other = self.make_file('/music/Other.mp3')
        results.set_files({other})
        self.assertTrue(results.status_label.isHidden())
        results.set_files({self.file, other})
        self.assertIn('Select one song', results.hint.text())


class ArtworkTestCase(InlineTestCase):
    def releases_reply(self, releases, error=None, http=None):
        self.tagger.mb_api.get_track_by_id.call_args.args[1]({'releases': releases}, http, error)

    def thumbnails(self):
        return {
            call.kwargs['url'].toString(): call.kwargs['handler']
            for call in self.tagger.webservice.download_url.call_args_list
            if 'front-250' in call.kwargs['url'].toString()
        }

    def answer(self, release_id, data=None, error=None):
        url = 'https://coverartarchive.org/release/%s/front-250' % release_id
        http = MagicMock()
        http.errorString.return_value = 'failed'
        self.thumbnails()[url](data or b'', http, error)


class ArtworkSearchTest(ArtworkTestCase):
    def test_releases_then_deduplicated_thumbnails_through_picard_webservice(self):
        search = ArtworkSearch(self.tagger, 'rec')
        self.addCleanup(search.deleteLater)
        search.start()
        self.assertEqual('rec', self.tagger.mb_api.get_track_by_id.call_args.args[0])
        self.assertEqual(('releases', 'release-groups'), self.tagger.mb_api.get_track_by_id.call_args.kwargs['inc'])
        self.releases_reply(
            [
                release(mbid('a'), 'Album', '2001', status='Official'),
                release(mbid('b'), 'Album', '2002', status='Official'),
                release(mbid('c'), 'Other', '2003', status='Official'),
                release(mbid('a'), 'Album', '2001', status='Official'),
            ]
        )
        self.assertEqual(3, len(self.thumbnails()))
        red = png('red')
        self.answer(mbid('a'), red)
        self.answer(mbid('b'), red)  # identical cover, another release
        self.answer(mbid('c'), error=QNetworkReply.NetworkError.ContentNotFoundError)
        self.assertEqual('ready', search.state)
        self.assertEqual([mbid('a')], [image.release_id for image in search.images])

    def test_release_count_is_bounded(self):
        search = ArtworkSearch(self.tagger, 'rec')
        self.addCleanup(search.deleteLater)
        search.start()
        self.releases_reply([release(mbid('r%d' % i), 'Album', '2001') for i in range(30)])
        self.assertEqual(MAX_RELEASES, len(self.thumbnails()))

    def test_not_found_and_error(self):
        search = ArtworkSearch(self.tagger, 'rec')
        self.addCleanup(search.deleteLater)
        search.start()
        self.releases_reply([release(mbid('a'), 'Album')])
        self.answer(mbid('a'), error=QNetworkReply.NetworkError.ContentNotFoundError)
        self.assertEqual('empty', search.state)
        failed = ArtworkSearch(self.tagger, 'rec2')
        self.addCleanup(failed.deleteLater)
        failed.start()
        http = MagicMock()
        http.errorString.return_value = 'offline'
        self.releases_reply([], error=QNetworkReply.NetworkError.HostNotFoundError, http=http)
        self.assertEqual('error', failed.state)

    def test_cancel_aborts_requests_and_ignores_late_replies(self):
        search = ArtworkSearch(self.tagger, 'rec')
        self.addCleanup(search.deleteLater)
        search.start()
        self.releases_reply([release(mbid('a'), 'Album')])
        search.cancel()
        self.assertTrue(self.tagger.webservice.abort_task.called)
        self.answer(mbid('a'), png('red'))
        self.assertEqual([], search.images)


class ArtworkSuggestionsTest(ArtworkTestCase):
    def setUp(self):
        super().setUp()
        self.file.metadata['musicbrainz_recordingid'] = mbid('rec')

    def panel(self, releases, covers, album='Album'):
        self.scan(releases=[])
        self.file.metadata['album'] = album
        panel = ArtworkSuggestions(self.tagger)
        self.addCleanup(panel.deleteLater)
        panel.set_file(self.file)
        self.releases_reply(releases)
        for release_id, color in covers.items():
            self.answer(release_id, png(color))
        return panel

    def full_image(self, index=-1):
        call = self.tagger.webservice.download_url.call_args_list[index]
        self.assertNotIn('front-250', call.kwargs['url'].toString())
        return call

    def test_search_starts_after_scan_and_ranks_album_title_as_suggested(self):
        panel = self.panel(
            [release(mbid('x'), 'Something Else', '2001', status='Official'), release(mbid('y'), 'The Album', '2002')],
            {mbid('x'): 'red', mbid('y'): 'blue'},
            album='The Album',
        )
        self.assertEqual(mbid('y'), panel._suggested.release_id)
        self.assertFalse(panel.suggested_button.isHidden())
        self.assertIn(status_text(Status.SUGGESTED), panel.suggested_label.text())
        self.assertEqual(1, panel.others.count())

    def test_no_search_without_scan_and_no_dialog(self):
        panel = ArtworkSuggestions(self.tagger)
        self.addCleanup(panel.deleteLater)
        panel.set_file(self.file)
        self.tagger.mb_api.get_track_by_id.assert_not_called()
        self.assertTrue(panel.find_button.isEnabled())
        with patch.object(QtWidgets.QDialog, 'exec') as dialog:
            panel.search()
        dialog.assert_not_called()
        self.tagger.mb_api.get_track_by_id.assert_called_once()

    def test_choose_updates_new_tags_artwork_only_and_never_saves(self):
        panel = self.panel([release(mbid('x'), 'Album', '2001')], {mbid('x'): 'red'})
        self.assertEqual([], list(self.file.orig_metadata.images))
        panel.choose(panel._suggested)
        call = self.full_image()
        self.assertEqual('https://coverartarchive.org/release/%s/front-500' % mbid('x'), call.kwargs['url'].toString())
        with patch.object(File, 'save') as save:
            call.kwargs['handler'](png('red'), MagicMock(), None)
        save.assert_not_called()
        self.assertEqual(1, len(self.file.metadata.images))
        self.assertEqual([], list(self.file.orig_metadata.images))
        self.assertIn(status_text(Status.CHOSEN), panel.suggested_label.text())

    def test_manual_confirm_stages_artwork_but_cancel_does_not_save(self):
        panel = ArtworkSuggestions(self.tagger)
        self.addCleanup(panel.deleteLater)
        panel.set_file(self.file)
        cover = CaaCoverArtImage(
            url='https://coverartarchive.org/release/example/front', types=['front'], is_front=True, data=png('red')
        )
        existing = CaaCoverArtImage(
            url='https://coverartarchive.org/release/existing/front', types=['front'], is_front=True, data=png('blue')
        )
        self.file.metadata.images.append(existing)
        self.file.orig_metadata.images.append(existing)
        dialog = MagicMock()
        dialog.cover = cover
        with (
            patch('picard.ui.dlmtags.artwork.ManualArtworkDialog', return_value=dialog),
            patch.object(File, 'save') as save,
        ):
            dialog.exec.return_value = QtWidgets.QDialog.DialogCode.Rejected
            panel.open_manual_search()
            self.assertEqual([existing], list(self.file.metadata.images))
            dialog.exec.return_value = QtWidgets.QDialog.DialogCode.Accepted
            panel.open_manual_search()
        save.assert_not_called()
        self.assertEqual([existing, cover], list(self.file.metadata.images))
        self.assertEqual([existing], list(self.file.orig_metadata.images))

    def test_choosing_again_replaces_the_previous_pick(self):
        panel = self.panel(
            [release(mbid('x'), 'Album', '2001'), release(mbid('y'), 'Other', '2002')],
            {mbid('x'): 'red', mbid('y'): 'blue'},
        )
        panel.choose(panel._suggested)
        self.full_image().kwargs['handler'](png('red'), MagicMock(), None)
        panel.choose(panel.others.item(0).data(QtCore.Qt.ItemDataRole.UserRole))
        self.full_image().kwargs['handler'](png('blue'), MagicMock(), None)
        self.assertEqual(1, len(self.file.metadata.images))

    def test_reply_after_switching_song_is_ignored(self):
        panel = self.panel([release(mbid('x'), 'Album', '2001')], {mbid('x'): 'red'})
        panel.choose(panel._suggested)
        call = self.full_image()
        other = self.make_file('/music/Other.mp3')
        panel.set_file(other)
        call.kwargs['handler'](png('red'), MagicMock(), None)
        self.assertEqual(0, len(self.file.metadata.images))
        self.assertEqual(0, len(other.metadata.images))

    def test_multiple_selection_shows_no_artwork_choices(self):
        panel = self.panel([release(mbid('x'), 'Album', '2001')], {mbid('x'): 'red'})
        panel.set_file(None)
        self.assertTrue(panel.suggested_button.isHidden())
        self.assertTrue(panel.others.isHidden())
        panel.choose(SimpleNamespace(release_id=mbid('x')))
        self.assertEqual(0, len(self.file.metadata.images))

    def test_album_change_refreshes_the_suggested_artwork(self):
        panel = self.panel(
            [release(mbid('x'), 'First', '2001', status='Official'), release(mbid('y'), 'Second', '2002')],
            {mbid('x'): 'red', mbid('y'): 'blue'},
        )
        self.file.metadata['album'] = 'First'
        panel.refresh()
        self.assertEqual(mbid('x'), panel._suggested.release_id)
        self.file.metadata['album'] = 'Second'
        panel.refresh()
        self.assertEqual(mbid('y'), panel._suggested.release_id)
