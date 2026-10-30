"""DLMtags window chrome keeps its own name without changing Picard attribution."""

from PyQt6 import QtWidgets

import pytest

from picard.ui import PicardDialog
from picard.ui.aboutdialog import AboutDialog
from picard.ui.mainwindow import MainWindow


class _StopSetup(Exception):
    pass


class _TitleProbe(QtWidgets.QMainWindow):
    def _create_actions(self):
        raise _StopSetup


def test_main_window_title_is_dlmtags(qapp):
    window = _TitleProbe()
    with pytest.raises(_StopSetup):
        MainWindow.setupUi(window)
    assert window.windowTitle() == 'DLMtags'
    window.deleteLater()


def test_parentless_dialog_title_uses_dlmtags(qapp):
    dialog = QtWidgets.QDialog()
    PicardDialog.set_window_title(dialog, 'Log')
    assert dialog.windowTitle() == 'Log — DLMtags'
    dialog.deleteLater()


def test_about_dialog_title_keeps_picard_attribution(qapp, monkeypatch):
    monkeypatch.setattr('picard.ui.tagger_instance', lambda: object())
    monkeypatch.setattr(AboutDialog, '_apply_styling', lambda _self: None)
    monkeypatch.setattr(AboutDialog, '_update_content', lambda _self: None)
    parent = QtWidgets.QWidget()
    dialog = AboutDialog(parent)
    assert dialog.windowTitle() == 'About DLMtags'
    assert dialog.ui.app_name.text() == 'MusicBrainz Picard'
    dialog.deleteLater()
    parent.deleteLater()
