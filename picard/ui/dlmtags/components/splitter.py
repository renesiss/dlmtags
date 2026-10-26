# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

from PyQt6 import (
    QtCore,
    QtWidgets,
)

from picard.ui.dlmtags.components.styles import style_component


class Splitter(QtWidgets.QSplitter):
    """Resizable panes without collapsed navigation or global styling."""

    def __init__(self, orientation=QtCore.Qt.Orientation.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self.setHandleWidth(6)
        self.setChildrenCollapsible(False)
        self._theme = style_component(self, 'splitter')

    def restore_sizes(self, sizes: list[int]) -> bool:
        """Apply validated proportions to the current available extent.

        Call after adding panes and establishing geometry. QSplitter enforces
        each child's minimum size. Persistence remains the caller's concern.
        """
        if len(sizes) != self.count() or not sizes or any(type(size) is not int or size <= 0 for size in sizes):
            return False
        extent = self.width() if self.orientation() == QtCore.Qt.Orientation.Horizontal else self.height()
        available = extent - self.handleWidth() * (self.count() - 1)
        if available <= 0:
            return False
        total = sum(sizes)
        self.setSizes([max(1, int(available * size / total)) for size in sizes])
        return True
