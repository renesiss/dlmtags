# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""A widget-owned facade over Picard's existing theme and palette."""

from PyQt6 import (
    QtCore,
    QtGui,
    QtWidgets,
)

from picard.ui.theme import (
    DLMTAGS_COLOR_ROLES,
    theme,
)


def original_value_palette(source: QtGui.QPalette) -> QtGui.QPalette:
    """Readable neutral text derived from the current Picard theme, not RGB tokens."""
    palette = QtGui.QPalette(source)
    for group in (QtGui.QPalette.ColorGroup.Active, QtGui.QPalette.ColorGroup.Inactive,
                  QtGui.QPalette.ColorGroup.Disabled):
        text = source.color(group, QtGui.QPalette.ColorRole.Text)
        base = source.color(group, QtGui.QPalette.ColorRole.Base)
        color = QtGui.QColor.fromRgbF(
            text.redF() * 0.85 + base.redF() * 0.15,
            text.greenF() * 0.85 + base.greenF() * 0.15,
            text.blueF() * 0.85 + base.blueF() * 0.15,
        )
        palette.setColor(group, QtGui.QPalette.ColorRole.WindowText, color)
    return palette


class DLMtagsTheme(QtCore.QObject):
    """Refresh local styling; never change application colors or settings.

    Parent this facade to the widget it styles. Qt disconnects its slots when
    the widget is destroyed, including connections to the global theme object.
    """

    changed = QtCore.pyqtSignal()

    def __init__(self, widget: QtWidgets.QWidget):
        super().__init__(widget)
        self._widget = widget
        theme.theme_changed.connect(self.refresh)
        theme.colors_changed.connect(self.refresh)

    def color(self, token: str) -> QtGui.QColor:
        """Resolve a semantic role in the widget's current palette group."""
        palette = self._widget.palette()
        return palette.color(palette.currentColorGroup(), DLMTAGS_COLOR_ROLES[token])

    @QtCore.pyqtSlot()
    def refresh(self):
        # Re-evaluate palette() QSS expressions without touching the app style.
        self._widget.setStyleSheet(self._widget.styleSheet())
        QtWidgets.QWidget.update(self._widget)
        self.changed.emit()
