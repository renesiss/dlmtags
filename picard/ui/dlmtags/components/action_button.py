# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

from PyQt6 import (
    QtCore,
    QtGui,
    QtWidgets,
)

from picard.ui.dlmtags.components.styles import style_component


class ActionButton(QtWidgets.QToolButton):
    """Native QAction binding for text, icon, state, tooltip and triggering."""

    def __init__(self, action: QtGui.QAction, parent=None):
        if not isinstance(action, QtGui.QAction):
            raise TypeError('ActionButton requires a QAction')
        super().__init__(parent)
        self.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setDefaultAction(action)
        self._theme = style_component(self, 'actionButton')
