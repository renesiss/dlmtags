# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""Persistent bookmarks and a read-only projection of the selected directory."""

import json
import os
import weakref

from PyQt6 import (
    QtCore,
    QtGui,
    QtWidgets,
    sip,
)

from picard import tagger_instance
from picard.config import get_config
from picard.file import File
from picard.i18n import gettext as _
from picard.util import (
    find_existing_path,
    resolve_fs_path,
)

from picard.ui.dlmtags.components.action_button import ActionButton
from picard.ui.dlmtags.components.card import Card
from picard.ui.dlmtags.components.section_header import SectionHeader
from picard.ui.dlmtags.components.splitter import Splitter
from picard.ui.dlmtags.components.table import CompactTable
from picard.ui.dlmtags.folder_media import FolderMedia
from picard.ui.dlmtags.sidebar_style import (
    SIDEBAR_METRICS,
    sidebar_icon,
    style_sidebar,
)


class FileBrowserSidebar(QtWidgets.QWidget):
    """One navigation pane; no matching, tag editing or disk-writing logic."""

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._window = window
        self._tagger = tagger_instance()
        self._folder = ''
        self._metadata_status = ''
        self._folder_status = ''
        self._selection_path = None
        self._pending_selection_loads = set()
        self._selection_timer = QtCore.QTimer(self)
        self._selection_timer.setInterval(80)
        self._selection_timer.timeout.connect(self._select_ready_song)
        self.setMinimumWidth(SIDEBAR_METRICS['minimum_width'])
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred, QtWidgets.QSizePolicy.Policy.Expanding)
        layout = QtWidgets.QVBoxLayout(self)
        margin = SIDEBAR_METRICS['margin']
        layout.setContentsMargins(margin, margin, margin, 0)
        layout.setSpacing(SIDEBAR_METRICS['spacing'])
        brand = SectionHeader('DLMtags', self)
        brand.setProperty('dlmtagsSidebarBrand', True)
        style_sidebar(brand, 'sectionHeader')
        layout.addWidget(brand)
        self.card = Card(self, sidebar=True, shadow=True)
        self.card.content_layout.setContentsMargins(margin, margin, margin, margin)
        self.card.content_layout.setSpacing(SIDEBAR_METRICS['spacing'])
        effect = self.card.graphicsEffect()
        effect.setBlurRadius(SIDEBAR_METRICS['shadow_blur'])
        effect.setOffset(SIDEBAR_METRICS['shadow_offset'], 0)
        self._sidebar_theme = style_sidebar(self.card, 'card')
        layout.addWidget(self.card, 1)

        search_row = QtWidgets.QHBoxLayout()
        search_row.setSpacing(SIDEBAR_METRICS['spacing'])
        self.search = QtWidgets.QLineEdit(self.card)
        self.search.setPlaceholderText(_('Search folders, files or music…'))
        self.search.setToolTip(_('Filter songs by file name, or enter a folder path and press Enter.'))
        self.search.setClearButtonEnabled(True)
        style_sidebar(self.search, 'search')
        search_row.addWidget(self.search, 1)
        browse = QtGui.QAction(_('Choose folder…'), self)
        self._browse_action = browse
        browse.triggered.connect(self._choose_folder)
        button = ActionButton(browse, self.card)
        style_sidebar(button, 'actionButton')
        button.setIconSize(QtCore.QSize(SIDEBAR_METRICS['icon'], SIDEBAR_METRICS['icon']))
        button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly)
        search_row.addWidget(button)
        self.card.content_layout.addLayout(search_row)

        self.splitter = Splitter(QtCore.Qt.Orientation.Vertical, self.card)
        self.card.content_layout.addWidget(self.splitter, 1)
        folders = QtWidgets.QWidget()
        folders_layout = QtWidgets.QVBoxLayout(folders)
        folders_layout.setContentsMargins(0, 0, 0, 0)
        folders_layout.setSpacing(SIDEBAR_METRICS['spacing'])
        add_bookmark = QtGui.QAction(_('Add bookmark'), self)
        self._add_action = add_bookmark
        add_bookmark.setToolTip(_('Add bookmark'))
        add_bookmark.triggered.connect(self._add_bookmark)
        folders_layout.addWidget(self._section_header(_('Bookmarks'), folders, actions=(add_bookmark,)))
        self.bookmarks = QtWidgets.QListWidget(folders)
        self.bookmarks.setAccessibleName(_('Bookmarks'))
        self.bookmarks.setMinimumHeight(SIDEBAR_METRICS['row'] * 2)
        self.bookmarks.setMouseTracking(True)
        self.bookmarks.setIconSize(QtCore.QSize(SIDEBAR_METRICS['icon'], SIDEBAR_METRICS['icon']))
        self.bookmarks.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.bookmarks.customContextMenuRequested.connect(self._bookmark_menu)
        style_sidebar(self.bookmarks, 'scrollArea')
        folders_layout.addWidget(self.bookmarks, 1)
        self.splitter.addWidget(folders)

        songs_panel = QtWidgets.QWidget()
        songs_layout = QtWidgets.QVBoxLayout(songs_panel)
        songs_layout.setContentsMargins(0, 0, 0, 0)
        songs_layout.setSpacing(SIDEBAR_METRICS['spacing'])
        separator = QtWidgets.QFrame(songs_panel)
        separator.setFixedHeight(1)
        style_sidebar(separator, 'separator')
        songs_layout.addWidget(separator)
        self._up_action = QtGui.QAction(_('Parent folder'), self)
        self._up_action.triggered.connect(self._parent_folder)
        songs_layout.addWidget(self._section_header(_('Songs'), songs_panel, actions=(self._up_action,)))
        self.songs = CompactTable(songs_panel)
        self.songs.setMinimumHeight(120)
        self.songs.setMouseTracking(True)
        self.songs.viewport().setMouseTracking(True)
        style_sidebar(self.songs, 'table')
        self.songs.setAccessibleName(_('Songs in selected folder'))
        self.folder_media = FolderMedia(self)
        self.song_model = self.folder_media.model
        self.song_filter = QtCore.QSortFilterProxyModel(self)
        self.song_filter.setSourceModel(self.song_model)
        self.song_filter.setFilterCaseSensitivity(QtCore.Qt.CaseSensitivity.CaseInsensitive)
        self.song_filter.setFilterKeyColumn(1)
        self.songs.setModel(self.song_filter)
        self.songs.hideColumn(0)
        self.songs.hideColumn(2)
        self.songs.setTextElideMode(QtCore.Qt.TextElideMode.ElideRight)
        self.songs.horizontalHeader().setStretchLastSection(False)
        self.songs.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.song_status = QtWidgets.QLabel(songs_panel)
        self.song_status.setWordWrap(True)
        # Long paths in loading/error messages must not widen the window.
        self.song_status.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        self.song_status.setProperty('dlmtagsHint', True)
        self.song_status.hide()
        songs_layout.addWidget(self.song_status)
        self.folder_media.status_changed.connect(self._folder_status_changed)
        songs_layout.addWidget(self.songs, 1)
        self.splitter.addWidget(songs_panel)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([140, 380])

        self.bookmarks.currentItemChanged.connect(self._bookmark_selected)
        self.bookmarks.itemClicked.connect(self._bookmark_clicked)
        self._tagger.format_registry.formats_changed.connect(self._update_song_filters)
        self._update_song_filters()
        self.search.textChanged.connect(self.song_filter.setFilterFixedString)
        self.search.returnPressed.connect(self._search_path)
        self.songs.doubleClicked.connect(self._open_entry)
        self.songs.selectionModel().currentChanged.connect(self._song_selected)
        load = QtGui.QAction(_('Load selected files'), self.songs)
        load.triggered.connect(self._load_songs)
        self.songs.addAction(load)
        self.songs.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.ActionsContextMenu)
        with QtCore.QSignalBlocker(self.bookmarks):
            self._restore_bookmarks()
        self.restore_state()
        self._search_icon_action = self.search.addAction(
            sidebar_icon(self.search, 'search'), QtWidgets.QLineEdit.ActionPosition.LeadingPosition
        )
        self._sidebar_theme.changed.connect(self._refresh_icons)
        self._refresh_icons()

    def _section_header(self, title, parent, *, actions=()):
        header = SectionHeader(title, parent, actions=actions)
        header.setProperty('dlmtagsSidebarSection', True)
        style_sidebar(header, 'sectionHeader')
        for button in header.findChildren(ActionButton):
            button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly)
            button.setIconSize(QtCore.QSize(SIDEBAR_METRICS['icon'], SIDEBAR_METRICS['icon']))
            style_sidebar(button, 'actionButton')
        return header

    def _bookmark_icon(self, path):
        for location, name in (
            (QtCore.QStandardPaths.StandardLocation.MusicLocation, 'music'),
            (QtCore.QStandardPaths.StandardLocation.MoviesLocation, 'video'),
        ):
            standard = QtCore.QStandardPaths.writableLocation(location)
            if standard and self._path_key(path) == self._path_key(standard):
                return name
        return 'bookmark'

    def _refresh_icons(self):
        self._browse_action.setIcon(sidebar_icon(self.card, 'browse'))
        self._add_action.setIcon(sidebar_icon(self.card, 'add'))
        self._search_icon_action.setIcon(sidebar_icon(self.search, 'search'))
        self._up_action.setIcon(sidebar_icon(self.card, 'up'))
        self.folder_media.folder_icon = sidebar_icon(self.songs, 'folder')
        for row in range(self.song_model.rowCount()):
            item = self.song_model.item(row, 1)
            if item.data(QtCore.Qt.ItemDataRole.UserRole + 1):
                item.setIcon(self.folder_media.folder_icon)
        for row in range(self.bookmarks.count()):
            item = self.bookmarks.item(row)
            item.setIcon(sidebar_icon(
                self.bookmarks, self._bookmark_icon(item.data(QtCore.Qt.ItemDataRole.UserRole))
            ))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.Type.PaletteChange and hasattr(self, '_search_icon_action'):
            self._refresh_icons()

    def _song_status_changed(self, text):
        self._metadata_status = text
        self._render_song_status()

    def _folder_status_changed(self, text):
        self._folder_status = text
        self._render_song_status()

    def _render_song_status(self):
        text = self._metadata_status or self._folder_status
        self.song_status.setText(text)
        self.song_status.setVisible(bool(text))

    def _update_song_filters(self):
        if self._folder:
            self.folder_media.set_folder(
                self._folder, self._tagger.format_registry.supported_extensions(),
                get_config().persist['show_hidden_files'],
            )

    @staticmethod
    def _path_key(path):
        return os.path.normcase(os.path.realpath(os.path.expanduser(path)))

    def _insert_bookmark(self, name, path):
        item = QtWidgets.QListWidgetItem(sidebar_icon(self.bookmarks, self._bookmark_icon(path)), name)
        item.setData(QtCore.Qt.ItemDataRole.UserRole, path)
        item.setToolTip(path)
        self.bookmarks.addItem(item)
        return item

    def _restore_bookmarks(self):
        raw = get_config().persist['dlmtags_bookmarks']
        entries = None
        if raw:
            try:
                entries = json.loads(raw)
            except (ValueError, TypeError):
                pass
        if not isinstance(entries, list):
            entries = [
                {'name': _('Music'), 'path': QtCore.QStandardPaths.writableLocation(
                    QtCore.QStandardPaths.StandardLocation.MusicLocation)},
                {'name': _('Videos'), 'path': QtCore.QStandardPaths.writableLocation(
                    QtCore.QStandardPaths.StandardLocation.MoviesLocation)},
            ]
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name, path = entry.get('name'), entry.get('path')
            if not isinstance(name, str) or not name.strip() or not isinstance(path, str) or not path:
                continue
            if not os.path.isabs(path):
                continue
            key = self._path_key(path)
            if key not in seen:
                self._insert_bookmark(name, path)
                seen.add(key)

    def _save_bookmarks(self):
        entries = []
        for row in range(self.bookmarks.count()):
            item = self.bookmarks.item(row)
            entries.append({'name': item.text(), 'path': item.data(QtCore.Qt.ItemDataRole.UserRole)})
        get_config().persist['dlmtags_bookmarks'] = json.dumps(entries, ensure_ascii=False)

    def _bookmark_selected(self, current, previous):
        if current is not None:
            self._preview_folder(current.data(QtCore.Qt.ItemDataRole.UserRole))

    def _bookmark_clicked(self, item):
        # Clicking the active bookmark returns from a subfolder to its root.
        path = item.data(QtCore.Qt.ItemDataRole.UserRole)
        if self._path_key(path) != self._path_key(self._folder):
            self._preview_folder(path)

    def _preview_folder(self, path, preserve_bookmark=False):
        self._selection_path = None
        self._selection_timer.stop()
        self._song_status_changed('')
        self._folder = os.path.abspath(os.path.expanduser(path))
        self.search.clear()
        self.songs.clearSelection()
        self.songs.setCurrentIndex(QtCore.QModelIndex())
        self.songs.setToolTip(self._folder)
        self._up_action.setEnabled(os.path.dirname(self._folder) != self._folder)
        self._update_song_filters()
        if preserve_bookmark:
            return
        with QtCore.QSignalBlocker(self.bookmarks):
            self.bookmarks.setCurrentRow(-1)
            closest = None
            closest_length = -1
            for row in range(self.bookmarks.count()):
                item = self.bookmarks.item(row)
                root = self._path_key(item.data(QtCore.Qt.ItemDataRole.UserRole))
                try:
                    inside = os.path.commonpath((root, self._path_key(self._folder))) == root
                except ValueError:
                    inside = False
                if inside and len(root) > closest_length:
                    closest, closest_length = item, len(root)
            if closest is not None:
                self.bookmarks.setCurrentItem(closest)

    def _parent_folder(self):
        self._preview_folder(os.path.dirname(self._folder), preserve_bookmark=True)

    def _open_entry(self, index):
        item = self.song_filter.mapToSource(index).siblingAtColumn(1)
        path = item.data(QtCore.Qt.ItemDataRole.UserRole)
        if item.data(QtCore.Qt.ItemDataRole.UserRole + 1):
            self._preview_folder(path, preserve_bookmark=True)
        elif path:
            self._request_song(path)

    def _add_bookmark(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, _('Add bookmark'), self._folder)
        if not path:
            return
        for row in range(self.bookmarks.count()):
            item = self.bookmarks.item(row)
            if self._path_key(item.data(QtCore.Qt.ItemDataRole.UserRole)) == self._path_key(path):
                self._preview_folder(path)
                return
        self._insert_bookmark(os.path.basename(os.path.normpath(path)) or path, path)
        self._save_bookmarks()
        self._preview_folder(path)

    def _bookmark_menu(self, position):
        item = self.bookmarks.itemAt(position)
        if item is None:
            return
        menu = QtWidgets.QMenu(self.bookmarks)
        rename = menu.addAction(_('Rename'))
        remove = menu.addAction(_('Remove'))
        chosen = menu.exec(self.bookmarks.viewport().mapToGlobal(position))
        if chosen == rename:
            name, accepted = QtWidgets.QInputDialog.getText(
                self, _('Rename bookmark'), _('Name'), text=item.text()
            )
            if accepted and name.strip():
                item.setText(name.strip())
                self._save_bookmarks()
        elif chosen == remove:
            with QtCore.QSignalBlocker(self.bookmarks):
                self.bookmarks.takeItem(self.bookmarks.row(item))
                self.bookmarks.setCurrentRow(-1)
                self.bookmarks.clearSelection()
            self._save_bookmarks()

    def save_state(self):
        if self._folder:
            get_config().persist['current_browser_path'] = self._folder

    def restore_state(self):
        config = get_config()
        path = (
            config.setting['starting_directory_path']
            if config.setting['starting_directory']
            else config.persist['current_browser_path']
        )
        path = (find_existing_path(path) if path else '') or QtCore.QDir.homePath()
        self._preview_folder(path)

    def _choose_folder(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, _('Choose folder'), self._folder)
        if path:
            self.search.clear()
            self._preview_folder(path)

    def _search_path(self):
        path = os.path.expanduser(self.search.text().strip())
        if os.path.isdir(path):
            self.search.clear()
            self._preview_folder(path)

    def _selected_paths(self):
        return [
            self.song_filter.mapToSource(index).siblingAtColumn(1).data(QtCore.Qt.ItemDataRole.UserRole)
            for index in self.songs.selectionModel().selectedRows(0)
            if not self.song_filter.mapToSource(index).siblingAtColumn(1).data(QtCore.Qt.ItemDataRole.UserRole + 1)
        ]

    def _load_songs(self, *args):
        paths = self._selected_paths()
        if paths:
            self._tagger.add_paths(paths, metadata_only=True)
            self._request_song(paths[0])

    def _song_selected(self, current, previous):
        self._selection_path = None
        self._selection_timer.stop()
        self._song_status_changed('')
        if not current.isValid():
            return
        item = self.song_filter.mapToSource(current).siblingAtColumn(1)
        if item.data(QtCore.Qt.ItemDataRole.UserRole + 1):
            return
        path = item.data(QtCore.Qt.ItemDataRole.UserRole)
        self._request_song(path)

    def _request_song(self, path):
        path = os.path.normpath(resolve_fs_path(path))
        self._selection_path = path
        self._song_status_changed(_('Loading metadata…'))
        if path not in self._tagger.files and path not in self._pending_selection_loads:
            self._pending_selection_loads.add(path)
            reference = weakref.ref(self)

            def loaded(file):
                owner = reference()
                if owner is not None and not sip.isdeleted(owner):
                    owner._pending_selection_loads.discard(path)
                    if owner._selection_path == path:
                        owner._select_ready_song()

            self._tagger.add_paths([path], metadata_only=True, on_loaded=loaded)
            if path not in self._tagger.files:
                self._pending_selection_loads.discard(path)
        self._select_ready_song()

    def _select_ready_song(self):
        path = self._selection_path
        if path is None:
            self._selection_timer.stop()
            return
        file = self._tagger.files.get(path)
        if file is None:
            self._selection_timer.stop()
            self._song_status_changed(_('File could not be loaded or was excluded by Picard settings.'))
            return
        if file.state == File.State.REMOVED:
            self._selection_timer.stop()
            self._song_status_changed(_('File is no longer loaded.'))
            return
        if file.state == File.State.PENDING or file.ui_item is None or self._window.ignore_selection_changes:
            self._selection_timer.start()
            return
        self._selection_timer.stop()
        self._window.panel.select_object(file)
        if file.has_error():
            self._song_status_changed(_('Unable to read metadata: %s') % '; '.join(str(error) for error in file.errors))
        else:
            self._song_status_changed('')
