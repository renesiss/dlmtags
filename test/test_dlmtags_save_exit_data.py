"""Save/exit integrity checks use only disposable copies of bundled audio fixtures."""

import base64
import hashlib
from pathlib import Path
import queue
import shutil
import threading
import time
from unittest.mock import (
    MagicMock,
    patch,
)

import mutagen
from mutagen.flac import Picture
from mutagen.id3 import TXXX

from PyQt6 import QtCore, QtWidgets

from test.formats.common import settings as FORMAT_SETTINGS
from test.picardtestcase import PicardTestCase
from test.test_smartscan import (
    BASE_SETTINGS,
    recording,
)

from picard.file import File
from picard.formats.id3 import MP3File
from picard.formats.registry import FormatRegistry
from picard.formats.vorbis import FLACFile
from picard.metadata import Metadata
from picard.smartscan import (
    CandidateSummary,
    Decision,
    Outcome,
    Source,
)
from picard.tagsearch import (
    Evidence,
    TagCandidate,
)

import pytest

from picard.ui.dlmtags.exit_dialog import ExitConfirmationDialog
from picard.ui.dlmtags.confirm_results import ConfirmResultsDialog
from picard.ui.dlmtags.find_tag import FindTagDialog
from picard.ui.dlmtags.save_toast import SaveToastManager


DATA = Path(__file__).resolve().parents[1] / 'test' / 'data'
TINY_PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/dZkAAAAASUVORK5CYII='
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.usefixtures('qapp')
class SaveExitAudioTest(PicardTestCase):
    def setUp(self):
        super().setUp()
        self.patch_tagger_instance('picard.item')
        self.set_config_values(
            setting={
                **BASE_SETTINGS,
                **FORMAT_SETTINGS,
                'enable_tag_saving': True,
                'preserve_timestamps': False,
                'rename_files': False,
                'move_files': False,
                'delete_empty_dirs': False,
                'save_images_to_files': False,
                'save_images_to_tags': True,
            }
        )
        self.temp = Path(self.mktmpdir())
        self.tagger.save_thread_pool = QtCore.QThreadPool()
        self.tagger.save_thread_pool.setMaxThreadCount(1)
        self.addCleanup(self.tagger.save_thread_pool.waitForDone)
        self.tagger.save.side_effect = lambda files: [file.save() for file in files]
        self.callbacks = queue.Queue()
        callback_patch = patch('picard.util.thread.to_main', side_effect=self._queue_callback)
        callback_patch.start()
        self.addCleanup(callback_patch.stop)

    def _queue_callback(self, func, **kwargs):
        self.callbacks.put((func, kwargs))

    def pump_callbacks(self):
        while not self.callbacks.empty():
            func, kwargs = self.callbacks.get_nowait()
            func(**kwargs)

    def copy_audio(self, name):
        source = DATA / name
        target = self.temp / name
        shutil.copy2(source, target)
        return target

    def loaded_file(self, path, file_type):
        file = file_type(str(path))
        file._copy_loaded_metadata(file._load(file.filename))
        file.state = File.State.NORMAL
        self.tagger.files[file.filename] = file
        return file

    def wait_for(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.pump_callbacks()
            QtWidgets.QApplication.processEvents()
            time.sleep(0.01)
        self.assertTrue(predicate(), 'save callback did not complete')

    def test_mp3_stage_save_preserves_unrelated_tags_and_no_placeholder(self):
        path = self.copy_audio('test.mp3')
        raw = mutagen.File(path)
        raw.tags.add(TXXX(encoding=3, desc='unrelated', text='keep'))
        raw.save()
        file = self.loaded_file(path, MP3File)
        before = digest(path)
        original_title = file.orig_metadata['title']
        file.metadata['title'] = 'DLMtags test title'
        file.update()
        self.assertEqual(digest(path), before)
        self.assertEqual(file.orig_metadata['title'], original_title)
        results = []
        file.save_finished.connect(results.append)
        file.save()
        self.wait_for(lambda: bool(results))
        self.assertEqual(results, [True])
        saved = mutagen.File(path)
        self.assertEqual(saved.tags.getall('TIT2')[0].text[0], 'DLMtags test title')
        self.assertEqual(saved.tags.getall('TXXX:unrelated')[0].text[0], 'keep')
        self.assertFalse(saved.tags.getall('APIC'))
        self.assertNotEqual(digest(path), before)

    def test_flac_save_preserves_existing_artwork_and_unrelated_tag(self):
        path = self.copy_audio('test.flac')
        raw = mutagen.File(path)
        raw['unrelated'] = ['keep']
        picture = Picture()
        picture.type = 3
        picture.mime = 'image/png'
        picture.data = TINY_PNG
        raw.add_picture(picture)
        raw.save()
        file = self.loaded_file(path, FLACFile)
        before = digest(path)
        file.metadata['title'] = 'DLMtags FLAC title'
        self.assertEqual(digest(path), before)
        results = []
        file.save_finished.connect(results.append)
        file.save()
        self.wait_for(lambda: bool(results))
        self.assertEqual(results, [True])
        saved = mutagen.File(path)
        self.assertEqual(saved['title'], ['DLMtags FLAC title'])
        self.assertEqual(saved['unrelated'], ['keep'])
        self.assertEqual([image.data for image in saved.pictures], [TINY_PNG])

    def test_find_tag_gui_approves_four_fields_then_save_and_exit(self):
        path = self.copy_audio('test.flac')
        raw = mutagen.File(path)
        raw['unrelated'] = ['keep']
        picture = Picture()
        picture.type = 3
        picture.mime = 'image/png'
        picture.data = TINY_PNG
        raw.add_picture(picture)
        raw.save()
        file = self.loaded_file(path, FLACFile)
        before = digest(path)

        for tag, value in (
            ('title', 'Reviewed title'),
            ('artist', 'Reviewed artist'),
            ('date', '2001-05-02'),
            ('genre', 'Rock'),
        ):
            dialog = FindTagDialog(file, tag)
            dialog.request.candidates = [
                TagCandidate((value,), [Evidence('MusicBrainz', 'MusicBrainz', 'Reviewed result')])
            ]
            dialog.request.state = 'ready'
            dialog._refresh_list()
            dialog.values.item(0).setSelected(True)
            self.assertTrue(dialog.apply_button.isEnabled())
            dialog.apply_button.click()
            self.assertEqual(file.metadata.getall(tag), [value])
        self.assertEqual(digest(path), before)

        exit_dialog = ExitConfirmationDialog([file], self.tagger, lambda: True)
        pump = QtCore.QTimer(exit_dialog)
        pump.setInterval(10)
        pump.timeout.connect(self.pump_callbacks)
        pump.start()
        QtCore.QTimer.singleShot(0, exit_dialog.save_button.click)
        QtCore.QTimer.singleShot(3000, exit_dialog.reject)
        self.assertEqual(exit_dialog.exec(), QtWidgets.QDialog.DialogCode.Accepted)
        pump.stop()
        self.assertTrue(path.is_file())
        saved = mutagen.File(path)
        for tag, value in (
            ('title', 'Reviewed title'),
            ('artist', 'Reviewed artist'),
            ('date', '2001-05-02'),
            ('genre', 'Rock'),
        ):
            self.assertEqual(saved[tag], [value])
        self.assertEqual(saved['unrelated'], ['keep'])
        self.assertEqual([image.data for image in saved.pictures], [TINY_PNG])

    def test_confirm_results_gui_reviews_four_fields_then_saves_disposable_audio(self):
        path = self.copy_audio('test.flac')
        raw = mutagen.File(path)
        raw['title'] = ['Original title']
        raw['artist'] = ['Original artist']
        raw['date'] = ['1999']
        raw['unrelated'] = ['keep']
        if 'genre' in raw:
            del raw['genre']
        picture = Picture()
        picture.type = 3
        picture.mime = 'image/png'
        picture.data = TINY_PNG
        raw.add_picture(picture)
        raw.save()
        file = self.loaded_file(path, FLACFile)
        self.tagger.mb_api = MagicMock()
        self.tagger.webservice = MagicMock()
        self.tagger.albums = {}
        self.set_config_values({'use_genres': True, 'max_genres': 5, 'min_genre_usage': 1})
        node = recording('reviewed-recording', 'Reviewed title', 'Reviewed artist', 200000)
        node['first-release-date'] = '2001-05-02'
        candidate = CandidateSummary(
            Source.METADATA, 'reviewed-recording', 'Reviewed title', 'Reviewed artist',
            200000, None, None, None, 0.9, None, node=node,
        )
        file.smart_scan_decision = Decision(Outcome.NEEDS_REVIEW, Source.METADATA, candidates=(candidate,))
        file._smart_scan_expected = Metadata(file.metadata)
        before = digest(path)
        images = file.metadata.images.copy()
        dialog = ConfirmResultsDialog(file)
        dialog.table.selectRow(0)
        for tag in ('title', 'artist', 'date'):
            self.assertTrue(dialog.approve_checks[tag].isEnabled())
            dialog.approve_checks[tag].setChecked(True)
        self.assertEqual('1999', dialog.current_labels['date'].text())
        self.assertEqual('2001-05-02', dialog.proposed_labels['date'].text())
        dialog.accept()
        self.assertEqual(digest(path), before)
        self.assertEqual('1999', file.metadata['date'])
        self.assertEqual('', file.metadata['genre'])
        genre_calls = [
            call for call in self.tagger.mb_api.get_track_by_id.call_args_list
            if {'genres', 'tags', 'user-genres', 'user-tags'} & set(call.kwargs.get('inc') or ())
        ]
        self.assertEqual(1, len(genre_calls))
        genre_calls[0].args[1](
            {'id': 'reviewed-recording', 'genres': [{'name': 'rock', 'count': 4}]}, None, None
        )
        dialog._refresh_suggestions()
        self.assertEqual('Rock', dialog.proposed_labels['genre'].text())
        dialog.approve_checks['genre'].setChecked(True)
        dialog.accept()
        self.assertEqual(digest(path), before)
        self.assertEqual(images, file.metadata.images)
        self.assertEqual('keep', file.metadata['unrelated'])
        exit_dialog = ExitConfirmationDialog([file], self.tagger, lambda: True)
        pump = QtCore.QTimer(exit_dialog)
        pump.setInterval(10)
        pump.timeout.connect(self.pump_callbacks)
        pump.start()
        QtCore.QTimer.singleShot(0, exit_dialog.save_button.click)
        QtCore.QTimer.singleShot(3000, exit_dialog.reject)
        self.assertEqual(exit_dialog.exec(), QtWidgets.QDialog.DialogCode.Accepted)
        pump.stop()
        saved = mutagen.File(path)
        for tag, value in (
            ('title', 'Reviewed title'),
            ('artist', 'Reviewed artist'),
            ('date', '2001-05-02'),
            ('genre', 'Rock'),
        ):
            self.assertEqual(saved[tag], [value])
        self.assertEqual(saved['unrelated'], ['keep'])
        self.assertEqual([image.data for image in saved.pictures], [TINY_PNG])
        self.assertTrue(path.is_file())

    def test_save_and_exit_refuses_hidden_destructive_setting(self):
        path = self.copy_audio('test.flac')
        file = self.loaded_file(path, FLACFile)
        file.metadata['title'] = 'Unsaved title'
        before = digest(path)
        self.set_config_values({'clear_existing_tags': True})
        dialog = ExitConfirmationDialog([file], self.tagger, lambda: True)
        dialog.save_button.click()
        self.wait_for(lambda: file.state == File.State.ERROR)
        dialog._check_saves()
        self.assertNotEqual(dialog.result(), QtWidgets.QDialog.DialogCode.Accepted)
        self.assertIn('could not be saved', dialog.message.text())
        self.assertEqual(digest(path), before)
        self.assertTrue(path.is_file())
        self.assertTrue(path.parent.is_dir())

    def test_hidden_destructive_settings_fail_before_writing_disposable_audio(self):
        for setting in ('clear_existing_tags', 'remove_images_from_tags', 'enable_tagger_scripts'):
            with self.subTest(setting=setting):
                path = self.copy_audio('test.flac')
                raw = mutagen.File(path)
                raw['unrelated'] = ['keep']
                picture = Picture()
                picture.type = 3
                picture.mime = 'image/png'
                picture.data = TINY_PNG
                raw.add_picture(picture)
                raw.save()
                file = self.loaded_file(path, FLACFile)
                file.metadata['title'] = 'New title'
                before = digest(path)
                self.set_config_values({setting: True})
                results = []
                file.save_finished.connect(results.append)
                file.save()
                self.wait_for(lambda: bool(results))
                self.assertEqual(results, [False])
                self.assertEqual(digest(path), before)
                self.assertTrue(path.parent.is_dir())
                saved = mutagen.File(path)
                self.assertEqual(saved['unrelated'], ['keep'])
                self.assertEqual([image.data for image in saved.pictures], [TINY_PNG])
                self.set_config_values({setting: False})

    def test_hidden_organization_settings_do_not_move_or_delete_audio(self):
        path = self.copy_audio('test.mp3')
        target_dir = self.temp / 'other'
        target_dir.mkdir()
        file = self.loaded_file(path, MP3File)
        file.metadata['title'] = 'New title'
        self.set_config_values(
            {
                'rename_files': True,
                'move_files': True,
                'move_files_to': str(target_dir),
                'move_additional_files': True,
                'delete_empty_dirs': True,
                'save_images_to_files': True,
            }
        )
        results = []
        file.save_finished.connect(results.append)
        file.save()
        self.wait_for(lambda: bool(results))
        self.assertEqual(results, [True])
        self.assertTrue(path.is_file())
        self.assertTrue(path.parent.is_dir())
        self.assertEqual(list(target_dir.iterdir()), [])
        self.assertEqual(mutagen.File(path).tags.getall('TIT2')[0].text[0], 'New title')

    def test_exit_without_saving_leaves_audio_unchanged(self):
        path = self.copy_audio('test.mp3')
        file = self.loaded_file(path, MP3File)
        before = digest(path)
        file.metadata['title'] = 'Unsaved title'
        file.update()
        dialog = ExitConfirmationDialog([file], self.tagger, lambda: True)
        QtCore.QTimer.singleShot(0, dialog.discard_button.click)
        self.assertEqual(dialog.exec(), QtWidgets.QDialog.DialogCode.Accepted)
        self.assertEqual(digest(path), before)
        self.assertNotEqual(file.metadata['title'], file.orig_metadata['title'])

    def test_failed_save_keeps_hash_and_shows_error_not_success(self):
        path = self.copy_audio('test.mp3')
        file = self.loaded_file(path, MP3File)
        before = digest(path)
        file.metadata['title'] = 'Unsaved title'
        window = QtWidgets.QMainWindow()
        window.show()
        manager = SaveToastManager(window)
        manager.begin([file])
        with patch.object(file, '_save', side_effect=OSError('simulated write failure')):
            file.save()
            self.wait_for(lambda: not manager._pending)
        self.assertEqual(digest(path), before)
        self.assertEqual(file.state, File.State.ERROR)
        self.assertEqual(manager.toast.title.text(), 'Could not save tags')
        window.close()

    def test_read_only_destination_fails_without_change(self):
        path = self.copy_audio('test.mp3')
        file = self.loaded_file(path, MP3File)
        before = digest(path)
        file.metadata['title'] = 'Unsaved title'
        path.chmod(0o444)
        try:
            results = []
            file.save_finished.connect(results.append)
            file.save()
            self.wait_for(lambda: bool(results))
            self.assertEqual(results, [False])
            self.assertEqual(digest(path), before)
        finally:
            path.chmod(0o644)

    def test_invalid_and_unsupported_files_are_not_modified(self):
        invalid = self.temp / 'invalid.mp3'
        invalid.write_bytes(b'not an mp3')
        before = digest(invalid)
        with self.assertRaises(mutagen.MutagenError):
            MP3File(str(invalid))._load(str(invalid))
        self.assertEqual(digest(invalid), before)
        unsupported = self.temp / 'unsupported.txt'
        unsupported.write_bytes(b'not audio')
        registry = FormatRegistry()
        registry.register(MP3File)
        self.assertIsNone(registry.open(unsupported))
        self.assertEqual(digest(unsupported), hashlib.sha256(b'not audio').hexdigest())

    def test_save_and_exit_waits_for_real_async_writes(self):
        paths = [self.copy_audio('test.mp3'), self.temp / 'second.mp3']
        shutil.copy2(paths[0], paths[1])
        files = [self.loaded_file(path, MP3File) for path in paths]
        before = [digest(path) for path in paths]
        for i, file in enumerate(files):
            file.metadata['title'] = f'DLMtags {i}'
        entered = threading.Event()
        release = threading.Event()
        original_save = files[0]._save

        def delayed_save(filename, metadata):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('test save gate expired')
            return original_save(filename, metadata)

        with patch.object(files[0], '_save', side_effect=delayed_save):
            files[0].save()
            self.wait_for(entered.is_set)
            dialog = ExitConfirmationDialog(files, self.tagger, lambda: True)
            pump = QtCore.QTimer(dialog)
            pump.setInterval(10)
            pump.timeout.connect(self.pump_callbacks)
            pump.start()
            QtCore.QTimer.singleShot(0, dialog.save_button.click)
            QtCore.QTimer.singleShot(80, release.set)
            QtCore.QTimer.singleShot(3000, dialog.reject)
            self.assertEqual(dialog.exec(), QtWidgets.QDialog.DialogCode.Accepted)
            pump.stop()
        self.assertEqual(self.tagger._saving_files_count, 0)
        self.assertTrue(all(digest(path) != old for path, old in zip(paths, before, strict=True)))
        self.assertEqual(
            [mutagen.File(path).tags.getall('TIT2')[0].text[0] for path in paths], ['DLMtags 0', 'DLMtags 1']
        )

    def test_partial_real_save_reports_one_success_and_one_failure(self):
        paths = [self.copy_audio('test.mp3'), self.temp / 'second.mp3']
        shutil.copy2(paths[0], paths[1])
        files = [self.loaded_file(path, MP3File) for path in paths]
        before = [digest(path) for path in paths]
        for i, file in enumerate(files):
            file.metadata['title'] = f'Partial {i}'
        window = QtWidgets.QMainWindow()
        window.show()
        manager = SaveToastManager(window)
        self.assertEqual(manager.begin(files), files)
        with patch.object(files[1], '_save', side_effect=OSError('simulated write failure')):
            self.tagger.save(files)
            self.wait_for(lambda: not manager._pending)
        self.assertNotEqual(digest(paths[0]), before[0])
        self.assertEqual(digest(paths[1]), before[1])
        self.assertEqual(manager.toast.title.text(), '1 file saved; 1 failed')
        window.close()

    def test_repeated_save_request_does_not_queue_second_write(self):
        path = self.copy_audio('test.mp3')
        file = self.loaded_file(path, MP3File)
        file.metadata['title'] = 'One write'
        window = QtWidgets.QMainWindow()
        window.show()
        manager = SaveToastManager(window)
        self.assertEqual(manager.begin([file]), [file])
        file.save()
        self.assertEqual(manager.begin([file]), [])
        self.wait_for(lambda: not manager._pending)
        self.assertEqual(manager.toast.title.text(), 'Tags saved successfully')
        self.assertEqual(mutagen.File(path).tags.getall('TIT2')[0].text[0], 'One write')
        window.close()

    def test_exit_requested_during_real_write_cannot_discard_it(self):
        path = self.copy_audio('test.mp3')
        file = self.loaded_file(path, MP3File)
        before = digest(path)
        file.metadata['title'] = 'Already writing'
        entered = threading.Event()
        release = threading.Event()
        original_save = file._save

        def delayed_save(filename, metadata):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('test save gate expired')
            return original_save(filename, metadata)

        with patch.object(file, '_save', side_effect=delayed_save):
            file.save()
            self.wait_for(entered.is_set)
            dialog = ExitConfirmationDialog([file], self.tagger, lambda: True)
            self.assertFalse(dialog.discard_button.isEnabled())
            QtCore.QTimer.singleShot(0, dialog.discard_button.click)
            QtCore.QTimer.singleShot(30, dialog.cancel_button.click)
            self.assertEqual(dialog.exec(), QtWidgets.QDialog.DialogCode.Rejected)
            self.assertEqual(digest(path), before)
            release.set()
            self.wait_for(lambda: self.tagger._saving_files_count == 0)
        self.assertNotEqual(digest(path), before)
        self.assertEqual(mutagen.File(path).tags.getall('TIT2')[0].text[0], 'Already writing')
