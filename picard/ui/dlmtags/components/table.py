# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

from PyQt6 import (
    QtCore,
    QtWidgets,
)

from picard.ui.dlmtags.components.styles import style_component


class CompactTable(QtWidgets.QTableView):
    """Read-only row selection view; callers supply the real data model."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.verticalHeader().hide()
        self.horizontalHeader().setStretchLastSection(True)
        self._update_row_height()
        self._theme = style_component(self, 'table')

    def _update_row_height(self):
        height = max(30, self.fontMetrics().height() + 8)
        self.verticalHeader().setMinimumSectionSize(height)
        self.verticalHeader().setDefaultSectionSize(height)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in {QtCore.QEvent.Type.FontChange, QtCore.QEvent.Type.ApplicationFontChange}:
            self._update_row_height()
