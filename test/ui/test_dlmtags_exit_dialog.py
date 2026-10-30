"""The exit prompt waits for Picard's file saves and respects the choice."""

from unittest.mock import (
    Mock,
    patch,
)

from PyQt6 import QtCore, QtWidgets

from picard.file import File

from picard.ui.dlmtags.exit_dialog import ExitConfirmationDialog
from picard.ui.mainwindow import MainWindow


def _dialog(qapp, count=1):
    files = [Mock(state=File.State.CHANGED) for _ in range(count)]
    for file in files:
        file.is_saved.side_effect = lambda file=file: file.state == File.State.NORMAL
        file.orig_metadata.images = 'original'
        file.metadata.images = 'original'
        file._expected_embedded_images.return_value = 'original'
    tagger = Mock(_saving_files_count=0)
    dialog = ExitConfirmationDialog(files, tagger, lambda: True)
    return dialog, files, tagger


def test_cancel_does_not_save(qapp):
    dialog, _files, tagger = _dialog(qapp)
    QtCore.QTimer.singleShot(0, dialog.cancel_button.click)
    assert dialog.exec() == QtWidgets.QDialog.DialogCode.Rejected
    tagger.save.assert_not_called()


def test_discard_does_not_save(qapp):
    dialog, _files, tagger = _dialog(qapp)
    QtCore.QTimer.singleShot(0, dialog.discard_button.click)
    assert dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted
    tagger.save.assert_not_called()


def test_save_warning_cancellation_keeps_changes(qapp):
    dialog, files, tagger = _dialog(qapp)
    dialog.confirm_save = lambda: False
    QtCore.QTimer.singleShot(0, dialog.save_button.click)
    QtCore.QTimer.singleShot(0, dialog.cancel_button.click)
    assert dialog.exec() == QtWidgets.QDialog.DialogCode.Rejected
    assert files[0].state == File.State.CHANGED
    tagger.save.assert_not_called()


def test_save_success(qapp):
    dialog, files, tagger = _dialog(qapp)
    tagger.save.side_effect = lambda _files: setattr(files[0], 'state', File.State.NORMAL)
    QtCore.QTimer.singleShot(0, dialog.save_button.click)
    assert dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted
    tagger.save.assert_called_once_with(files)


def test_save_failure_keeps_dialog_open(qapp):
    dialog, files, tagger = _dialog(qapp)
    tagger.save.side_effect = lambda _files: setattr(files[0], 'state', File.State.ERROR)
    QtCore.QTimer.singleShot(0, dialog.save_button.click)
    QtCore.QTimer.singleShot(150, dialog.reject)
    assert dialog.exec() == QtWidgets.QDialog.DialogCode.Rejected
    assert files[0].state == File.State.ERROR


def test_save_waits_for_async_completion(qapp):
    dialog, files, tagger = _dialog(qapp, count=2)

    def start_save(_files):
        for file in files:
            file.state = File.State.PENDING
        tagger._saving_files_count = 2
        QtCore.QTimer.singleShot(50, finish_first)
        QtCore.QTimer.singleShot(100, finish_second)

    def finish_first():
        files[0].state = File.State.NORMAL
        tagger._saving_files_count = 1
        assert dialog.isVisible()

    def finish_second():
        files[1].state = File.State.NORMAL
        tagger._saving_files_count = 0

    tagger.save.side_effect = start_save
    QtCore.QTimer.singleShot(0, dialog.save_button.click)
    QtCore.QTimer.singleShot(25, dialog.close)
    assert dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted
    tagger.save.assert_called_once_with(files)


def test_main_window_uses_all_loaded_file_states(qapp):
    saved = Mock(state=File.State.NORMAL)
    saved.is_saved.return_value = True
    saved.orig_metadata.images = 'original'
    saved.metadata.images = 'original'
    saved._expected_embedded_images.return_value = 'original'
    changed = Mock(state=File.State.CHANGED)
    changed.is_saved.return_value = False
    changed.orig_metadata.images = 'original'
    changed.metadata.images = 'original'
    changed._expected_embedded_images.return_value = 'original'
    window = Mock(spec=MainWindow)
    window.tagger = Mock()
    window.tagger.stopping = False
    window.tagger.files = {'album.mp3': saved, 'sidebar.flac': changed}
    window._confirm_file_save.return_value = True
    with patch('picard.ui.mainwindow.ExitConfirmationDialog') as dialog_type:
        dialog_type.return_value.exec.return_value = QtWidgets.QDialog.DialogCode.Rejected
        assert not MainWindow.show_quit_confirmation(window)
    assert dialog_type.call_args.args[:2] == ([changed], window.tagger)


def test_clean_main_window_closes_without_dialog(qapp):
    saved = Mock(state=File.State.NORMAL)
    saved.is_saved.return_value = True
    saved.orig_metadata.images = 'original'
    saved.metadata.images = 'original'
    saved._expected_embedded_images.return_value = 'original'
    window = Mock(spec=MainWindow)
    window.tagger = Mock()
    window.tagger.stopping = False
    window.tagger.files = {'clean.mp3': saved}
    window.tagger._saving_files_count = 0
    with patch('picard.ui.mainwindow.ExitConfirmationDialog') as dialog_type:
        assert MainWindow.show_quit_confirmation(window)
        dialog_type.assert_not_called()


def test_artwork_removal_prompts_even_if_file_state_is_normal(qapp):
    file = Mock(state=File.State.NORMAL)
    file.is_saved.return_value = True
    file.orig_metadata.images = 'embedded artwork'
    file._expected_embedded_images.return_value = 'no artwork'
    window = Mock(spec=MainWindow)
    window.tagger = Mock(stopping=False, files={'song.mp3': file})
    with patch('picard.ui.mainwindow.ExitConfirmationDialog') as dialog_type:
        dialog_type.return_value.exec.return_value = QtWidgets.QDialog.DialogCode.Rejected
        assert not MainWindow.show_quit_confirmation(window)
    assert dialog_type.call_args.args[0] == [file]


def test_staged_external_artwork_prompts(qapp):
    file = Mock(state=File.State.NORMAL)
    file.is_saved.return_value = True
    file.orig_metadata.images = 'original'
    file.metadata.images = 'staged artwork'
    file._expected_embedded_images.return_value = 'original'
    window = Mock(spec=MainWindow)
    window.tagger = Mock(stopping=False, files={'song.mp3': file})
    with patch('picard.ui.mainwindow.ExitConfirmationDialog') as dialog_type:
        dialog_type.return_value.exec.return_value = QtWidgets.QDialog.DialogCode.Rejected
        assert not MainWindow.show_quit_confirmation(window)
    assert dialog_type.call_args.args[0] == [file]


def test_discard_unavailable_while_prior_save_is_running(qapp):
    dialog, files, tagger = _dialog(qapp)
    files[0].state = File.State.PENDING
    tagger._saving_files_count = 1
    QtCore.QTimer.singleShot(0, dialog.discard_button.click)
    QtCore.QTimer.singleShot(0, dialog.cancel_button.click)
    assert dialog.exec() == QtWidgets.QDialog.DialogCode.Rejected
    assert not dialog.discard_button.isEnabled()
    tagger.save.assert_not_called()


def test_save_and_exit_waits_for_prior_and_new_writes(qapp):
    dialog, files, tagger = _dialog(qapp, count=2)
    files[0].state = File.State.PENDING
    tagger._saving_files_count = 1

    def start_save(new_files):
        assert new_files == [files[1]]
        files[1].state = File.State.PENDING
        tagger._saving_files_count = 2
        QtCore.QTimer.singleShot(40, finish_prior)
        QtCore.QTimer.singleShot(90, finish_new)

    def finish_prior():
        files[0].state = File.State.NORMAL
        tagger._saving_files_count = 1
        assert dialog.isVisible()

    def finish_new():
        files[1].state = File.State.NORMAL
        tagger._saving_files_count = 0

    tagger.save.side_effect = start_save
    QtCore.QTimer.singleShot(0, dialog.save_button.click)
    assert dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted
    tagger.save.assert_called_once_with([files[1]])


def test_active_save_blocks_exit_when_confirmation_setting_is_off(qapp):
    window = Mock(spec=MainWindow)
    window.tagger = Mock(_saving_files_count=1)
    window.player = None
    window.script_editor_dialog = None
    window.show_quit_confirmation.return_value = False
    event = QtCore.QEvent(QtCore.QEvent.Type.Close)
    with patch('picard.ui.mainwindow.get_config') as config:
        config.return_value.setting = {'quit_confirmation': False}
        MainWindow.closeEvent(window, event)
    window.show_quit_confirmation.assert_called_once()
    assert not event.isAccepted()


def test_prior_save_completion_without_remaining_changes_exits(qapp):
    dialog, files, tagger = _dialog(qapp)
    files[0].state = File.State.PENDING
    tagger._saving_files_count = 1
    dialog._wait_for_active_saves()

    def finish():
        files[0].state = File.State.NORMAL
        tagger._saving_files_count = 0

    QtCore.QTimer.singleShot(50, finish)
    assert dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted
    tagger.save.assert_not_called()


def test_prior_save_failure_reenables_discard_after_completion(qapp):
    dialog, files, tagger = _dialog(qapp)
    files[0].state = File.State.PENDING
    tagger._saving_files_count = 1
    dialog._wait_for_active_saves()

    def finish():
        files[0].state = File.State.ERROR
        tagger._saving_files_count = 0
        dialog._check_saves()
        assert dialog.discard_button.isEnabled()
        assert dialog.isVisible()
        dialog.cancel_button.click()

    QtCore.QTimer.singleShot(50, finish)
    assert dialog.exec() == QtWidgets.QDialog.DialogCode.Rejected


def test_save_and_exit_keeps_open_when_staged_artwork_remains(qapp):
    dialog, files, tagger = _dialog(qapp)
    files[0].metadata.images = 'staged external artwork'
    tagger.save.side_effect = lambda _files: setattr(files[0], 'state', File.State.NORMAL)
    QtCore.QTimer.singleShot(0, dialog.save_button.click)
    QtCore.QTimer.singleShot(80, dialog.cancel_button.click)
    assert dialog.exec() == QtWidgets.QDialog.DialogCode.Rejected
    assert files[0].metadata.images == 'staged external artwork'
    tagger.save.assert_called_once_with(files)
