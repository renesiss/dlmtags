# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""Sidebar-only geometry, palette-based presentation and symbolic SVG icons."""

from string import Template

from PyQt6 import (
    QtCore,
    QtGui,
)

from picard.ui.dlmtags.components.styles import (
    COMPONENT_STYLE,
    style_component,
)


SIDEBAR_METRICS = {
    'minimum_width': 220,
    'margin': 8,
    'spacing': 6,
    'row': 28,
    'icon': 16,
    'radius': 14,
    'control': 22,
    'scrollbar': 6,
    'shadow_blur': 22,
    'shadow_offset': 2,
}

SIDEBAR_STYLE = Template("""
QFrame[dlmtagsSurface="sidebar"] {
    background: palette(alternate-base); border: none; border-radius: ${radius}px;
}
QWidget[dlmtagsSidebarSection="true"] QLabel {
    color: palette(placeholder-text); font-size: 11px; font-weight: 600;
}
QWidget[dlmtagsSidebarBrand="true"] QLabel { font-size: 16px; padding-left: ${margin}px; }
QFrame[dlmtagsSurface="sidebar"] QLineEdit[dlmtagsComponent="search"] {
    min-height: 20px; padding: 3px 6px; border-radius: 7px;
    border: 1px solid transparent; background: palette(base);
}
QFrame[dlmtagsSurface="sidebar"] QLineEdit[dlmtagsComponent="search"]:focus {
    border-color: palette(mid);
}
QFrame[dlmtagsSurface="sidebar"] QToolButton[dlmtagsComponent="actionButton"] {
    min-height: ${control}px; min-width: ${control}px; padding: 2px;
    border: 1px solid transparent; border-radius: 6px;
}
QFrame[dlmtagsSurface="sidebar"] QToolButton[dlmtagsComponent="actionButton"]:hover:enabled {
    background: palette(button);
}
QFrame[dlmtagsSurface="sidebar"] QToolButton[dlmtagsComponent="actionButton"]:focus {
    border-color: palette(mid);
}
QFrame[dlmtagsSurface="sidebar"] QListView[dlmtagsComponent="scrollArea"]::item {
    min-height: 24px; padding: 2px 8px; margin: 1px 0px; border-radius: 6px;
}
QFrame[dlmtagsSurface="sidebar"] QListView[dlmtagsComponent="scrollArea"]::item:hover:!selected,
QFrame[dlmtagsSurface="sidebar"] QTableView[dlmtagsComponent="table"]::item:hover:!selected {
    background: palette(button);
}
QFrame[dlmtagsSurface="sidebar"] QListView[dlmtagsComponent="scrollArea"]::item:selected,
QFrame[dlmtagsSurface="sidebar"] QTableView[dlmtagsComponent="table"]::item:selected {
    background: palette(highlight); color: palette(highlighted-text);
}
QFrame[dlmtagsSurface="sidebar"] QTableView[dlmtagsComponent="table"] QHeaderView::section {
    padding: 3px 6px; font-size: 11px; color: palette(placeholder-text);
    background: palette(alternate-base); border: none;
}
QFrame[dlmtagsSurface="sidebar"] QScrollBar:vertical { width: ${scrollbar}px; background: palette(base); }
QFrame[dlmtagsSurface="sidebar"] QScrollBar:horizontal { height: ${scrollbar}px; background: palette(base); }
QFrame[dlmtagsSurface="sidebar"] QScrollBar::handle { background: palette(mid); border-radius: 3px; }
QFrame[dlmtagsSurface="sidebar"] QScrollBar::handle:hover { background: palette(button-text); }
""").substitute(SIDEBAR_METRICS)

SVG_PATHS = {
    'folder': '<path d="M3 7V5h6l2 2h10v13H3Z"/>',
    'up': '<path d="M12 20V4m-6 6 6-6 6 6"/>',
    'music': '<path d="M9 18V5l11-2v13M9 8l11-2"/><ellipse cx="6" cy="18" rx="3" ry="2"/>'
             '<ellipse cx="17" cy="16" rx="3" ry="2"/>',
    'video': '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3Z"/>',
    'bookmark': '<path d="M6 4h12v17l-6-4-6 4Z"/>',
    'browse': '<rect x="3" y="3" width="7" height="7" rx="1"/>'
              '<rect x="14" y="3" width="7" height="7" rx="1"/>'
              '<rect x="3" y="14" width="7" height="7" rx="1"/>'
              '<rect x="14" y="14" width="7" height="7" rx="1"/>',
    'add': '<path d="M12 5v14M5 12h14"/>',
    'search': '<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/>',
    'external': '<path d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6"/>',
}


def style_sidebar(widget, component):
    """Own only this sidebar widget's stylesheet, retaining runtime refresh."""
    facade = style_component(widget, component)
    widget.setStyleSheet(COMPONENT_STYLE + SIDEBAR_STYLE)
    return facade


def sidebar_icon(widget, name):
    """Render symbolic SVGs for normal/selected/disabled states and current DPI."""
    icon = QtGui.QIcon()
    palette = widget.palette()
    size = SIDEBAR_METRICS['icon']
    ratio = widget.devicePixelRatioF()
    for mode, group, role in (
        (QtGui.QIcon.Mode.Normal, palette.currentColorGroup(), QtGui.QPalette.ColorRole.Text),
        (QtGui.QIcon.Mode.Selected, palette.currentColorGroup(), QtGui.QPalette.ColorRole.HighlightedText),
        (QtGui.QIcon.Mode.Disabled, QtGui.QPalette.ColorGroup.Disabled, QtGui.QPalette.ColorRole.Text),
    ):
        color = palette.color(group, role).name()
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
            f'fill="none" stroke="{color}" stroke-width="1.6" stroke-linecap="round" '
            f'stroke-linejoin="round">{SVG_PATHS[name]}</svg>'
        )
        pixmap = QtGui.QPixmap(round(size * ratio), round(size * ratio))
        # Use Qt's SVG image plugin without requiring the optional QtSvg binding.
        if pixmap.loadFromData(svg.encode(), 'SVG'):
            pixmap = pixmap.scaled(
                round(size * ratio), round(size * ratio),
                QtCore.Qt.AspectRatioMode.KeepAspectRatio, QtCore.Qt.TransformationMode.SmoothTransformation,
            )
            pixmap.setDevicePixelRatio(ratio)
            icon.addPixmap(pixmap, mode)
            continue
        pixmap = QtGui.QPixmap(round(size * ratio), round(size * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.scale(size / 24, size / 24)
        painter.setPen(QtGui.QPen(palette.color(group, role), 1.6))
        _paint_symbol(painter, name)
        painter.end()
        icon.addPixmap(pixmap, mode)
    return icon


def _paint_symbol(painter, name):
    """Vector fallback on systems whose Qt installation has no SVG image plugin."""
    path = QtGui.QPainterPath()
    if name == 'music':
        path.moveTo(9, 18)
        path.lineTo(9, 5)
        path.lineTo(20, 3)
        path.lineTo(20, 16)
        path.addEllipse(QtCore.QRectF(3, 16, 6, 4))
        path.addEllipse(QtCore.QRectF(14, 14, 6, 4))
    elif name == 'video':
        path.addRoundedRect(QtCore.QRectF(3, 5, 18, 14), 2, 2)
        path.moveTo(10, 9)
        path.lineTo(15, 12)
        path.lineTo(10, 15)
        path.closeSubpath()
    elif name == 'search':
        path.addEllipse(QtCore.QRectF(4, 4, 12, 12))
        path.moveTo(15, 15)
        path.lineTo(20, 20)
    elif name == 'folder':
        path.moveTo(3, 7)
        for x, y in ((3, 5), (9, 5), (11, 7), (21, 7), (21, 20), (3, 20)):
            path.lineTo(x, y)
        path.closeSubpath()
    elif name == 'up':
        path.moveTo(12, 20)
        path.lineTo(12, 4)
        path.moveTo(6, 10)
        path.lineTo(12, 4)
        path.lineTo(18, 10)
    elif name == 'add':
        path.moveTo(12, 5)
        path.lineTo(12, 19)
        path.moveTo(5, 12)
        path.lineTo(19, 12)
    elif name == 'external':
        path.moveTo(14, 4)
        path.lineTo(20, 4)
        path.lineTo(20, 10)
        path.moveTo(20, 4)
        path.lineTo(11, 13)
        path.moveTo(18, 14)
        for x, y in ((18, 20), (4, 20), (4, 6), (10, 6)):
            path.lineTo(x, y)
    elif name == 'browse':
        for x, y in ((3, 3), (14, 3), (3, 14), (14, 14)):
            path.addRoundedRect(QtCore.QRectF(x, y, 7, 7), 1, 1)
    else:
        path.moveTo(6, 4)
        for x, y in ((18, 4), (18, 21), (12, 17), (6, 21)):
            path.lineTo(x, y)
        path.closeSubpath()
    painter.drawPath(path)
