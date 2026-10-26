# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

from PyQt6 import (
    QtCore,
    QtWidgets,
)

from picard.ui.dlmtags.components.action_button import ActionButton
from picard.ui.dlmtags.components.styles import style_component


class SectionHeader(QtWidgets.QWidget):
    """Compact title with optional controls backed by caller-owned actions."""

    def __init__(self, title: str, parent=None, *, actions=()):
        super().__init__(parent)
        self.content_layout = QtWidgets.QHBoxLayout(self)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(6)
        self.title_label = QtWidgets.QLabel(title, self)
        self.title_label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        font = self.title_label.font()
        font.setBold(True)
        self.title_label.setFont(font)
        self.content_layout.addWidget(self.title_label, 1)
        for action in actions:
            self.content_layout.addWidget(ActionButton(action, self))
        self._theme = style_component(self, 'sectionHeader')
