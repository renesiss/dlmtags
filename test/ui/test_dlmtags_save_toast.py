"""Save feedback follows completed Picard file callbacks, without touching audio."""

from unittest.mock import Mock, patch

from PyQt6 import QtCore, QtWidgets

from picard.file import File

from picard.ui.dlmtags.save_toast import SaveToastManager
from picard.ui.mainwindow import MainWindow


class FakeFile(QtCore.QObject):
    save_finished = QtCore.pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        self.state = File.State.CHANGED


def _manager(qapp):
    window = QtWidgets.QMainWindow()
    window.resize(640, 480)
    window.show()
    qapp.processEvents()
    manager = SaveToastManager(window)
    return window, manager


def test_single_save_only_after_completion(qapp):
    window, manager = _manager(qapp)
    file = FakeFile()
    assert manager.begin([file]) == [file]
    assert not manager.toast.isVisible()
    file.state = File.State.PENDING
    qapp.processEvents()
    assert not manager.toast.isVisible()
    file.state = File.State.NORMAL
    file.save_finished.emit(True)
    assert manager.toast.title.text() == 'Tags saved successfully'
    assert manager.toast.detail.text() == 'Your changes have been saved.'
    assert manager.toast._timer.interval() == 3000
    assert manager.toast._timer.isActive()
    assert manager.toast.icon.pixmap() is not None
    manager.toast.close_button.click()
    assert not manager.toast.isVisible()
    window.close()


def test_async_bulk_save_is_consolidated_and_deduplicated(qapp):
    window, manager = _manager(qapp)
    files = [FakeFile() for _ in range(16)]
    assert manager.begin(files) == files
    assert manager.begin(files) == []
    for file in files[:-1]:
        file.save_finished.emit(True)
    assert manager.begin(files) == []
    assert not manager.toast.isVisible()
    QtCore.QTimer.singleShot(0, lambda: files[-1].save_finished.emit(True))
    qapp.processEvents()
    assert manager.toast.isVisible()
    assert manager.toast.title.text() == '16 files saved successfully'
    assert manager.toast.detail.text() == 'Your changes have been saved.'
    window.close()


def test_failed_save_never_claims_success(qapp):
    window, manager = _manager(qapp)
    file = FakeFile()
    manager.begin([file])
    file.state = File.State.ERROR
    file.save_finished.emit(False)
    assert manager.toast.isVisible()
    assert manager.toast.title.text() == 'Could not save tags'
    assert 'failed to save' in manager.toast.detail.text()
    window.close()


def test_partial_save_reports_exact_counts(qapp):
    window, manager = _manager(qapp)
    files = [FakeFile() for _ in range(3)]
    manager.begin(files)
    files[0].save_finished.emit(True)
    files[1].save_finished.emit(False)
    assert not manager.toast.isVisible()
    files[2].save_finished.emit(True)
    assert manager.toast.title.text() == '2 files saved; 1 failed'
    assert manager.toast.detail.text() == 'Some changes could not be saved.'
    window.close()


def test_empty_save_shows_nothing(qapp):
    window, manager = _manager(qapp)
    assert manager.begin([]) == []
    assert not manager.toast.isVisible()
    window.close()


def test_main_window_dispatches_only_new_files(qapp):
    file = FakeFile()
    window = Mock(spec=MainWindow)
    window.selected_objects = [file]
    window._save_notifications = Mock()
    window.tutorial = Mock()
    window.tagger = Mock()
    window._confirm_file_save.return_value = True
    window._save_notifications.begin.side_effect = [[file], []]
    with (
        patch('picard.ui.mainwindow.iter_files_from_objects', return_value=iter([file])),
        patch('picard.ui.mainwindow.get_config') as config,
    ):
        config.return_value.setting = {'enable_tag_saving': True}
        MainWindow.save(window)
    window.tagger.save.assert_called_once_with([file])
    with (
        patch('picard.ui.mainwindow.iter_files_from_objects', return_value=iter([file])),
        patch('picard.ui.mainwindow.get_config') as config,
    ):
        config.return_value.setting = {'enable_tag_saving': True}
        MainWindow.save(window)
    window.tagger.save.assert_called_once_with([file])


def test_disabled_tag_writing_never_claims_tags_saved(qapp):
    file = FakeFile()
    window = Mock(spec=MainWindow)
    window.selected_objects = [file]
    window._save_notifications = Mock()
    window.tutorial = Mock()
    window.tagger = Mock()
    window._confirm_file_save.return_value = True
    with (
        patch('picard.ui.mainwindow.iter_files_from_objects', return_value=iter([file])),
        patch('picard.ui.mainwindow.get_config') as config,
    ):
        config.return_value.setting = {'enable_tag_saving': False}
        MainWindow.save(window)
    window.tagger.save.assert_called_once_with([file])
    window._save_notifications.begin.assert_not_called()
