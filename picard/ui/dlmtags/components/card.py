# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

from PyQt6 import (
    QtCore,
    QtWidgets,
)

from picard.ui.dlmtags.components.styles import style_component


class Card(QtWidgets.QFrame):
    """Rounded panel with an owned content layout and optional elevation."""

    def __init__(self, parent=None, *, sidebar=False, shadow=False):
        super().__init__(parent)
        self.setProperty('dlmtagsSurface', 'sidebar' if sidebar else 'panel')
        self.content_layout = QtWidgets.QVBoxLayout(self)
        self.content_layout.setContentsMargins(8, 8, 8, 8)
        self.content_layout.setSpacing(6)
        self._theme = style_component(self, 'card')
        self._shadow = None
        if shadow:
            self._shadow = QtWidgets.QGraphicsDropShadowEffect(self)
            self._shadow.setBlurRadius(22)
            self._shadow.setOffset(2, 0)
            self.setGraphicsEffect(self._shadow)
            self._theme.changed.connect(self._update_shadow)
            self._update_shadow()

    def _update_shadow(self):
        if self._shadow is not None:
            color = self._theme.color('shadow')
            color.setAlpha(60)
            self._shadow.setColor(color)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.Type.PaletteChange and hasattr(self, '_theme'):
            self._update_shadow()
