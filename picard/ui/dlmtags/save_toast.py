# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""Non-modal feedback for completed, explicitly requested file saves."""

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


_CHECK_ICON = os.path.join(os.path.dirname(__file__), 'icons', 'save-success.svg')


class SaveToast(QtWidgets.QFrame):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName('dlmtagsSaveToast')
        self.setFixedWidth(320)
        self.setStyleSheet('''
            QFrame#dlmtagsSaveToast {
                background: palette(base); color: palette(text);
                border: 1px solid palette(mid); border-radius: 10px;
            }
            QFrame#dlmtagsSaveToast QLabel { background: transparent; border: none; color: palette(text); }
            QFrame#dlmtagsSaveToast QLabel#saveToastDetail { color: palette(placeholder-text); }
            QFrame#dlmtagsSaveToast QToolButton {
                background: transparent; border: none; color: palette(text);
                border-radius: 4px; padding: 2px;
            }
            QFrame#dlmtagsSaveToast QToolButton:hover { background: palette(alternate-base); }
        ''')
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(12, 10, 8, 10)
        row.setSpacing(9)
        self.icon = QtWidgets.QLabel(self)
        self.icon.setFixedSize(24, 24)
        row.addWidget(self.icon, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        labels = QtWidgets.QVBoxLayout()
        labels.setSpacing(2)
        self.title = QtWidgets.QLabel(self)
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        self.detail = QtWidgets.QLabel(self)
        self.detail.setObjectName('saveToastDetail')
        self.detail.setWordWrap(True)
        labels.addWidget(self.title)
        labels.addWidget(self.detail)
        row.addLayout(labels, 1)
        close = QtWidgets.QToolButton(self)
        close.setText('×')
        close.setAccessibleName(_('Close notification'))
        close.setFixedSize(22, 22)
        close.clicked.connect(self.hide)
        row.addWidget(close, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        self.close_button = close
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self.hide)
        parent.installEventFilter(self)
        self.hide()

    def eventFilter(self, watched, event):
        if watched is self.parent() and event.type() == QtCore.QEvent.Type.Resize:
            self._position()
        return super().eventFilter(watched, event)

    def _position(self):
        parent = self.parentWidget()
        if parent is not None:
            self.move(max(8, parent.width() - self.width() - 16), max(8, parent.height() - self.height() - 16))

    def show_result(self, title, detail, success):
        self.title.setText(title)
        self.detail.setText(detail)
        if success:
            self.icon.setPixmap(QtGui.QIcon(_CHECK_ICON).pixmap(24, 24))
        else:
            warning = self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MessageBoxWarning)
            self.icon.setPixmap(warning.pixmap(24, 24))
        self.adjustSize()
        self._position()
        self.show()
        self.raise_()
        self._timer.start()


class SaveToastManager(QtCore.QObject):
    """Coalesce File.save_finished callbacks for one explicit Save batch."""

    def __init__(self, window):
        super().__init__(window)
        self.toast = SaveToast(window)
        self._pending = set()
        self._saved = set()
        self._failed = set()

    def begin(self, files):
        new_files = []
        for file in files:
            if (
                file in self._pending
                or file in self._saved
                or file in self._failed
                or file.state
                in {
                    File.State.PENDING,
                    File.State.REMOVED,
                }
            ):
                continue
            self._pending.add(file)
            file.save_finished.connect(self._file_finished)
            new_files.append(file)
        return new_files

    def _file_finished(self, success):
        file = self.sender()
        if file not in self._pending:
            return
        file.save_finished.disconnect(self._file_finished)
        self._pending.remove(file)
        (self._saved if success else self._failed).add(file)
        if self._pending:
            return
        saved, failed = len(self._saved), len(self._failed)
        self._saved.clear()
        self._failed.clear()
        if failed:
            if saved:
                title = ngettext('%d file saved; %d failed', '%d files saved; %d failed', saved) % (saved, failed)
                detail = _('Some changes could not be saved.')
            else:
                title = _('Could not save tags')
                detail = ngettext('%d file failed to save.', '%d files failed to save.', failed) % failed
            self.toast.show_result(title, detail, False)
        elif saved:
            title = (
                _('Tags saved successfully')
                if saved == 1
                else ngettext('%d file saved successfully', '%d files saved successfully', saved) % saved
            )
            self.toast.show_result(title, _('Your changes have been saved.'), True)
