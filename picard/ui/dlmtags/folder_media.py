# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""Read-only, cancellable directory enumeration and duration presentation."""

from functools import partial
import math
import os
import stat
from threading import Event
import weakref

from mutagen import File as MutagenFile
from PyQt6 import (
    QtCore,
    QtGui,
    sip,
)

from picard.i18n import gettext as _
from picard.util.thread import run_task


def _scan(folder, extensions, hidden, cancel, closed):
    paths = []
    unreadable = 0
    if cancel.is_set() or closed.is_set():
        return paths, unreadable
    with os.scandir(folder) as entries:
        for entry in entries:
            if cancel.is_set() or closed.is_set():
                return [], unreadable
            try:
                if not hidden and (entry.name.startswith('.') or (
                    os.name == 'nt' and getattr(entry.stat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_HIDDEN
                )):
                    continue
                directory = entry.is_dir()
                if directory or (os.path.splitext(entry.name)[1].lower().lstrip('.') in extensions and entry.is_file()):
                    paths.append((os.path.abspath(entry.path), directory))
            except OSError:
                unreadable += 1
    return sorted(paths, key=lambda entry: (not entry[1], os.path.basename(entry[0]).casefold(), entry[0])), unreadable


def _durations(paths, cancel, closed):
    results = []
    for path in paths:
        if cancel.is_set() or closed.is_set():
            break
        seconds = None
        try:
            media = MutagenFile(path)
            value = getattr(getattr(media, 'info', None), 'length', None)
            if value is not None and math.isfinite(value) and value >= 0:
                seconds = int(value)
        except Exception:
            # Unsupported/corrupt headers or unreadable files have no duration.
            pass
        results.append((path, seconds))
    return results


class FolderMedia(QtCore.QObject):
    """Transient view model, with all filesystem/header reads off the GUI thread."""

    status_changed = QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = QtGui.QStandardItemModel(self)
        self.model.setHorizontalHeaderLabels([_('#'), _('File name'), _('Length')])
        self._cancel = Event()
        self._closed = Event()
        self.destroyed.connect(self._closed.set)
        self._generation = 0
        self._paths = []
        self._entries = []
        self.folder_icon = QtGui.QIcon()
        self._read_warning = ''
        self._rows = {}
        self._position = 0
        self._duration_position = 0
        self._insert_timer = QtCore.QTimer(self)
        self._insert_timer.setSingleShot(True)
        self._insert_timer.timeout.connect(self._append_rows)

    def set_folder(self, folder, extensions, hidden):
        self._cancel.set()
        self._cancel = Event()
        self._generation += 1
        self._insert_timer.stop()
        self._paths = []
        self._entries = []
        self._read_warning = ''
        self._rows = {}
        self._position = self._duration_position = 0
        self.model.removeRows(0, self.model.rowCount())
        self.status_changed.emit(_('Reading folder…'))
        self._submit(partial(_scan, folder, frozenset(ext.lower().lstrip('.') for ext in extensions),
                             hidden, self._cancel, self._closed), '_scanned')

    def _submit(self, work, handler):
        reference = weakref.ref(self)
        generation = self._generation

        def finished(result=None, error=None):
            owner = reference()
            if owner is not None and not sip.isdeleted(owner) and owner._generation == generation:
                getattr(owner, handler)(result, error)

        run_task(work, finished, traceback=False)

    def _scanned(self, result, error):
        if error is not None:
            self.status_changed.emit(_('Unable to read folder: %s') % str(error))
            return
        self._entries, unreadable = result
        self._paths = [path for path, directory in self._entries if not directory]
        self._read_warning = _('Some folder entries could not be read.') if unreadable else ''
        if not self._entries:
            self.status_changed.emit(self._read_warning or _('No folders or supported media files in this directory.'))
            return
        self._append_rows()

    def _append_rows(self):
        end = min(self._position + 100, len(self._entries))
        for row in range(self._position, end):
            path, directory = self._entries[row]
            items = [QtGui.QStandardItem(str(row + 1)), QtGui.QStandardItem(os.path.basename(path)),
                     QtGui.QStandardItem('—')]
            items[1].setData(path, QtCore.Qt.ItemDataRole.UserRole)
            items[1].setData(directory, QtCore.Qt.ItemDataRole.UserRole + 1)
            if directory:
                items[1].setIcon(self.folder_icon)
                items[2].setText('')
            items[1].setToolTip(os.path.basename(path))
            self.model.appendRow(items)
            self._rows[path] = row
        self._position = end
        if end < len(self._entries):
            self._insert_timer.start(0)
        else:
            self.status_changed.emit(_('Reading durations…'))
            self._next_durations()

    def _next_durations(self):
        paths = self._paths[self._duration_position:self._duration_position + 16]
        if not paths:
            self.status_changed.emit(self._read_warning)
            return
        self._duration_position += len(paths)
        self._submit(partial(_durations, paths, self._cancel, self._closed), '_duration_ready')

    def _duration_ready(self, result, error):
        for path, seconds in result or []:
            row = self._rows.get(path)
            if row is not None and seconds is not None:
                hours, remainder = divmod(seconds, 3600)
                minutes, seconds = divmod(remainder, 60)
                text = f'{hours}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes}:{seconds:02d}'
                self.model.item(row, 2).setText(text)
        self._next_durations()
