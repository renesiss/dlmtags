# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""Confirm exit while preserving Picard's asynchronous file-save path."""

import os.path

from PyQt6 import (
    QtCore,
    QtGui,
    QtWidgets,
)

from picard.file import File
from picard.i18n import (
    gettext as _,
    ngettext,
)


_ICONS = os.path.join(os.path.dirname(__file__), 'icons')


def has_unsaved_changes(file):
    """Use Picard's file state and its expected embedded artwork after saving."""
    return not file.is_saved() or file.orig_metadata.images != file._expected_embedded_images()


class ExitConfirmationDialog(QtWidgets.QDialog):
    def __init__(self, files, tagger, confirm_save, parent=None):
        super().__init__(parent)
        self.files = files
        self.tagger = tagger
        self.confirm_save = confirm_save
        self._saving = False
        self.setWindowTitle(_('Unsaved Changes — DLMtags'))
        self.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        self.setMinimumWidth(370)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        heading = QtWidgets.QLabel(_('Unsaved changes'), self)
        font = heading.font()
        font.setBold(True)
        heading.setFont(font)
        layout.addWidget(heading)
        self.message = QtWidgets.QLabel(
            ngettext(
                'Save changes to %d file before exiting?',
                'Save changes to %d files before exiting?',
                len(files),
            )
            % len(files),
            self,
        )
        self._prompt_text = self.message.text()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

        buttons = QtWidgets.QDialogButtonBox(self)
        self.cancel_button = QtWidgets.QPushButton(_('Cancel'), self)
        self.discard_button = QtWidgets.QPushButton(_('Exit Without Saving'), self)
        self.save_button = QtWidgets.QPushButton(_('Save & Exit'), self)
        buttons.addButton(self.cancel_button, QtWidgets.QDialogButtonBox.ButtonRole.RejectRole)
        buttons.addButton(self.discard_button, QtWidgets.QDialogButtonBox.ButtonRole.DestructiveRole)
        buttons.addButton(self.save_button, QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        self.cancel_button.setIcon(QtGui.QIcon(os.path.join(_ICONS, 'exit-cancel.svg')))
        self.discard_button.setIcon(QtGui.QIcon(os.path.join(_ICONS, 'exit-discard.svg')))
        self.save_button.setIcon(QtGui.QIcon(os.path.join(_ICONS, 'save.svg')))
        for button in (self.cancel_button, self.discard_button, self.save_button):
            button.setIconSize(QtCore.QSize(22, 22))
        self.save_button.setDefault(True)
        self.cancel_button.clicked.connect(self.reject)
        self.discard_button.clicked.connect(self._discard_and_exit)
        self.save_button.clicked.connect(self._save_and_exit)
        layout.addWidget(buttons)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._check_saves)
        if self.tagger._saving_files_count:
            self._wait_for_active_saves()

    def _wait_for_active_saves(self):
        self.discard_button.setEnabled(False)
        self.message.setText(_('A file save is in progress. Wait for it to finish before exiting without saving.'))
        self._timer.start()

    def _discard_and_exit(self):
        # A queued click must not bypass the disabled button if a save just started.
        if self.tagger._saving_files_count:
            self._wait_for_active_saves()
            return
        self.accept()

    def _save_and_exit(self):
        if not self.confirm_save():
            return
        self._saving = True
        self.cancel_button.setEnabled(False)
        self.discard_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.message.setText(_('Saving files…'))
        try:
            # File.save() applies Picard's pre-save processors and uses its save thread pool.
            self.tagger.save([file for file in self.files if file.state != File.State.PENDING])
        except Exception:
            self._save_failed()
            return
        self._timer.start()
        self._check_saves()

    def _check_saves(self):
        if self.tagger._saving_files_count:
            return
        if not self._saving:
            self._timer.stop()
            if not any(
                has_unsaved_changes(file) or file.metadata.images != file.orig_metadata.images for file in self.files
            ):
                self.accept()
            else:
                self.discard_button.setEnabled(True)
                self.message.setText(self._prompt_text)
            return
        if any(file.state == File.State.PENDING for file in self.files):
            return
        self._timer.stop()
        if not any(
            has_unsaved_changes(file) or file.metadata.images != file.orig_metadata.images for file in self.files
        ):
            self._saving = False
            self.accept()
        else:
            self._save_failed()

    def _save_failed(self):
        self._timer.stop()
        self._saving = False
        self.message.setText(_('Some files could not be saved. Review the errors and try again.'))
        self.cancel_button.setEnabled(True)
        self.discard_button.setEnabled(not self.tagger._saving_files_count)
        if self.tagger._saving_files_count:
            self._timer.start()
        self.save_button.setEnabled(True)
        self.cancel_button.setDefault(True)

    def reject(self):
        if not self._saving:
            super().reject()

    def closeEvent(self, a0):
        if self._saving:
            a0.ignore()
        else:
            super().closeEvent(a0)
