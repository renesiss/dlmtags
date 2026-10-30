# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""Local component styles. All colors resolve through Picard's palette."""

from PyQt6 import (
    QtCore,
    QtWidgets,
)

from picard.ui.dlmtags.theme import DLMtagsTheme


EDITOR_ROW_HEIGHT = 28
EDITOR_FIELD_PADDING = 20
FORM_LABEL_WIDTH = 110
FORM_ROW_HEIGHT = 32
FORM_PANEL_PADDING = 12
FORM_PANEL_GAP = 12


COMPONENT_STYLE = """
QWidget#dlmtagsTagForm { background: palette(base); }
QWidget#dlmtagsTagForm QFrame[notebookRow="true"] {
    background: transparent; border: none;
    border-bottom: 1px solid palette(alternate-base);
}
QWidget#dlmtagsTagForm QFrame[notebookRow="true"]:hover { background: palette(alternate-base); }
QWidget#dlmtagsTagForm QFrame[dlmtagsSurface="sidebar"] QFrame[notebookRow="true"] {
    border-bottom-color: palette(mid);
}
QWidget#dlmtagsTagForm QFrame[dlmtagsSurface="sidebar"] QFrame[notebookRow="true"]:hover {
    background: palette(button);
}
QWidget#dlmtagsTagForm QLabel[formLabel="true"] {
    font-size: 12px; font-weight: 500; background: transparent;
    color: palette(placeholder-text); padding: 0px 5px; border: none;
}
QWidget#dlmtagsTagForm QLabel[dualPanelHeading="true"] {
    font-size: 13px; font-weight: 600; color: palette(text);
    background: transparent; padding: 0px 0px 8px 0px;
}
QWidget#dlmtagsTagForm QLabel[originalValue="true"] {
    background: transparent; color: palette(window-text);
    border: none; padding: 0px 5px; font-size: 13px; font-weight: 400;
}
QWidget#dlmtagsTagForm QLineEdit,
QWidget#dlmtagsTagForm QComboBox,
QWidget#dlmtagsTagForm QPlainTextEdit {
    background: transparent; color: palette(text); font-size: 13px; font-weight: 400;
    border: none; border-bottom: 1px solid transparent; border-radius: 0px;
    min-height: 22px; padding: 0px 5px;
}
QWidget#dlmtagsTagForm QLineEdit:focus,
QWidget#dlmtagsTagForm QComboBox:focus,
QWidget#dlmtagsTagForm QPlainTextEdit:focus { border-bottom-color: palette(highlight); }
QWidget#dlmtagsTagForm QComboBox::drop-down { border: none; background: transparent; width: 14px; }
QWidget#dlmtagsTagForm QLabel[changeArrow="true"] {
    color: palette(highlight); border: none; border-left: 1px solid palette(mid);
    padding-left: 3px; background: transparent;
}
QWidget#dlmtagsTagForm QLabel[metadataIndicator="true"] {
    background: transparent; color: palette(highlight); border: none; padding: 0px;
}
QWidget#dlmtagsTagForm QToolButton {
    background: transparent; color: palette(text); border: none;
    border-radius: 0px; padding: 2px;
}
QWidget#dlmtagsTagForm QToolButton:hover { color: palette(highlight); }
QFrame[dlmtagsComponent="card"] {
    background: palette(base); color: palette(text);
    border: none; border-radius: 14px;
}
QFrame[dlmtagsComponent="card"][dlmtagsSurface="sidebar"] {
    background: palette(alternate-base);
}
QFrame[dlmtagsComponent="separator"] { background: palette(mid); border: none; }
QFrame[dlmtagsSurface="sidebar"] QTreeView[dlmtagsComponent="scrollArea"],
QFrame[dlmtagsSurface="sidebar"] QListView[dlmtagsComponent="scrollArea"],
QFrame[dlmtagsSurface="sidebar"] QTableView[dlmtagsComponent="table"] {
    background: palette(alternate-base);
}
QFrame[dlmtagsSurface="sidebar"] QAbstractScrollArea::corner {
    background: palette(alternate-base); border: none;
}
QWidget[dlmtagsComponent="sectionHeader"] { background: transparent; }
QWidget[dlmtagsComponent="sectionHeader"] QLabel {
    color: palette(text); background: transparent; border: none;
}
QLabel[dlmtagsHint="true"] { color: palette(placeholder-text); }
QWidget[dlmtagsComponent="songInfo"] { background: transparent; }
QWidget[dlmtagsComponent="songInfo"] QLabel { background: transparent; border: none; color: palette(text); }
QWidget[dlmtagsComponent="songInfo"] QLabel[songInfoRole="title"] { font-size: 17px; font-weight: 600; }
QWidget[dlmtagsComponent="songInfo"] QLabel[songInfoRole="artist"] { font-size: 13px; }
QWidget[dlmtagsComponent="songInfo"] QLabel[songInfoRole="section"] {
    font-size: 11px; font-weight: 600; letter-spacing: 1px;
    color: palette(placeholder-text); padding-top: 6px;
}
QWidget[dlmtagsComponent="songInfo"] QLabel[songInfoRole="caption"],
QWidget[dlmtagsComponent="songInfo"] QLabel[songInfoRole="muted"] {
    font-size: 11px; color: palette(placeholder-text);
}
QWidget[dlmtagsComponent="songInfo"] QLabel[songInfoRole="value"],
QWidget[dlmtagsComponent="songInfo"] QLabel[songInfoRole="body"] { font-size: 13px; }
QWidget[dlmtagsComponent="songInfo"] QLabel[songInfoRole="heading"] {
    font-size: 13px; font-weight: 600; padding-top: 4px;
}
QProgressBar#dlmtagsSearchProgress {
    min-height: 4px; max-height: 4px; border: none;
    border-radius: 2px; background: palette(alternate-base);
}
QProgressBar#dlmtagsSearchProgress::chunk { background: palette(highlight); border-radius: 2px; }
QToolButton[dlmtagsComponent="actionButton"] {
    color: palette(button-text); background: transparent;
    border: 1px solid transparent; border-radius: 6px;
    min-height: 24px; padding: 3px 8px;
}
QToolButton[dlmtagsComponent="actionButton"]:hover:enabled {
    background: palette(alternate-base);
}
QToolButton[dlmtagsComponent="actionButton"]:checked,
QToolButton[dlmtagsComponent="actionButton"]:pressed {
    background: palette(highlight); color: palette(highlighted-text);
}
QToolButton[dlmtagsComponent="actionButton"]:focus {
    border-color: palette(highlight);
}
QToolButton[dlmtagsComponent="actionButton"]:disabled { color: palette(mid); }
QToolButton[dlmtagsSettingsNav="true"] {
    color: palette(text); background: transparent;
    border: 1px solid transparent; border-radius: 8px;
    min-height: 34px; padding: 5px 8px; text-align: left;
}
QToolButton[dlmtagsSettingsNav="true"]:hover:enabled {
    background: palette(button);
}
QToolButton[dlmtagsSettingsNav="true"]:checked {
    background: palette(alternate-base); border-color: palette(mid); color: palette(text);
}
QToolButton[dlmtagsSettingsNav="true"]:focus {
    border-color: palette(mid);
}
QToolButton[dlmtagsSettingsNav="true"]:disabled {
    color: palette(mid); background: transparent;
}
QWidget#dlmtagsApiKeysPage { background: palette(base); }
QDialog#dlmtagsWelcomeDialog {
    background: palette(window); color: palette(text);
}
QDialog#dlmtagsWelcomeDialog QLabel {
    background: transparent; border: none; color: palette(text); font-size: 13px;
}
QDialog#dlmtagsWelcomeDialog QLabel#dlmtagsWelcomeTitle {
    font-size: 21px; font-weight: 650;
}
QDialog#dlmtagsWelcomeDialog QLabel#dlmtagsWelcomeLicense {
    color: palette(placeholder-text); font-size: 12px;
}
QDialog#dlmtagsWelcomeDialog QLabel[dlmtagsWelcomeBullet="true"] {
    color: palette(highlight); font-size: 16px; font-weight: 700;
}
QDialog#dlmtagsWelcomeDialog QPushButton#dlmtagsWelcomeContinue {
    background: palette(highlight); border: 1px solid palette(highlight);
    border-radius: 7px; color: palette(highlighted-text); min-height: 30px;
    min-width: 104px; padding: 3px 16px; font-weight: 600;
}
QDialog#dlmtagsWelcomeDialog QPushButton#dlmtagsWelcomeContinue:hover {
    background: palette(button); color: palette(text);
}
QDialog#dlmtagsWelcomeDialog QPushButton#dlmtagsWelcomeContinue:focus {
    border-color: palette(text);
}
QDialog#dlmtagsWelcomeDialog QCheckBox#dlmtagsWelcomeDontShowAgain {
    background: transparent; border: none; color: palette(text); spacing: 8px;
}
QDialog#dlmtagsWelcomeDialog QCheckBox#dlmtagsWelcomeDontShowAgain::indicator {
    width: 16px; height: 16px; background: palette(base);
    border: 1px solid palette(mid); border-radius: 4px;
}
QDialog#dlmtagsWelcomeDialog QCheckBox#dlmtagsWelcomeDontShowAgain::indicator:checked {
    background: palette(highlight); border-color: palette(highlight);
}
QFrame[dlmtagsApiCard="true"] {
    background: palette(alternate-base); border: 1px solid palette(mid); border-radius: 10px;
}
QLabel[dlmtagsApiTitle="true"] {
    background: transparent; border: none; color: palette(text);
    font-size: 18px; font-weight: 650;
}
QLabel[dlmtagsApiDescription="true"] {
    background: transparent; border: none; color: palette(placeholder-text); font-size: 12px;
}
QLabel[dlmtagsApiProvider="true"] {
    background: transparent; border: none; color: palette(text); font-size: 14px; font-weight: 600;
}
QLabel[dlmtagsApiStatus="true"] {
    background: palette(base); border: 1px solid palette(mid); border-radius: 10px;
    color: palette(placeholder-text); font-size: 11px; padding: 3px 9px;
}
QLabel[dlmtagsApiStatus="true"][apiCredentialStatus="connected"] {
    color: #69c98c; border-color: #397c52;
}
QLabel[dlmtagsApiStatus="true"][apiCredentialStatus="failed"] {
    color: #ef7777; border-color: #8c4444;
}
QLabel[dlmtagsApiStatus="true"][apiCredentialStatus="testing"] {
    color: #e5b85c; border-color: #80662f;
}
QLineEdit[dlmtagsApiCredential="true"] {
    background: palette(base); border: 1px solid palette(mid); border-radius: 7px;
    color: palette(text); min-height: 32px; padding: 3px 10px;
}
QLineEdit[dlmtagsApiCredential="true"]:focus { border-color: palette(highlight); }
QPushButton[dlmtagsApiAction="true"] {
    background: transparent; border: 1px solid palette(mid); border-radius: 7px;
    color: palette(text); min-height: 30px; padding: 3px 12px;
}
QPushButton[dlmtagsApiAction="true"]:hover:enabled {
    background: palette(button); border-color: palette(highlight);
}
QPushButton[dlmtagsApiAction="true"]:pressed { background: palette(alternate-base); }
QPushButton[dlmtagsApiAction="true"]:focus { border-color: palette(highlight); }
QPushButton[dlmtagsApiAction="true"]:disabled { color: palette(mid); border-color: palette(alternate-base); }
QTableView[dlmtagsComponent="table"] {
    background: palette(base); color: palette(text); border: none;
    selection-background-color: palette(highlight);
    selection-color: palette(highlighted-text);
}
QTableView[dlmtagsComponent="table"]::item { padding: 2px 6px; }
QTableView#dlmtagsTagEditor::item {
    border-bottom: 1px solid palette(alternate-base);
}
QTableView[dlmtagsComponent="table"]::item:hover:!selected { background: palette(alternate-base); }
QTableView[dlmtagsComponent="table"] QHeaderView::section {
    background: palette(alternate-base); color: palette(text);
    border: none; padding: 4px 6px; font-weight: 600;
}
QTableView[dlmtagsComponent="table"] QTableCornerButton::section {
    background: palette(alternate-base); border: none;
}
QSplitter[dlmtagsComponent="splitter"]::handle { background: transparent; }
QSplitter[dlmtagsComponent="splitter"]::handle:hover { background: palette(mid); }
QLineEdit[dlmtagsComponent="search"] {
    background: palette(base); color: palette(text);
    border: 1px solid palette(mid); border-radius: 6px;
    min-height: 24px; padding: 3px 6px;
}
QLineEdit[dlmtagsComponent="search"]:focus { border-color: palette(highlight); }
QTreeView[dlmtagsComponent="scrollArea"] {
    border: none; background: palette(base); color: palette(text);
    selection-background-color: palette(highlight);
    selection-color: palette(highlighted-text);
}
QTreeView[dlmtagsComponent="scrollArea"]::item { min-height: 26px; padding: 2px 6px; }
QListView[dlmtagsComponent="scrollArea"] {
    border: none; background: palette(alternate-base); color: palette(text);
    selection-background-color: palette(highlight);
    selection-color: palette(highlighted-text);
}
QListView[dlmtagsComponent="scrollArea"]::item {
    min-height: 26px; padding: 2px 6px; border-radius: 6px;
}
QAbstractScrollArea[dlmtagsComponent="table"] QScrollBar:vertical,
QAbstractScrollArea[dlmtagsComponent="scrollArea"] QScrollBar:vertical {
    width: 6px; margin: 0px; border: none; background: transparent;
}
QAbstractScrollArea[dlmtagsComponent="table"] QScrollBar:horizontal,
QAbstractScrollArea[dlmtagsComponent="scrollArea"] QScrollBar:horizontal {
    height: 6px; margin: 0px; border: none; background: transparent;
}
QAbstractScrollArea[dlmtagsComponent="table"] QScrollBar::handle,
QAbstractScrollArea[dlmtagsComponent="scrollArea"] QScrollBar::handle {
    background: palette(mid); border-radius: 3px;
}
QAbstractScrollArea[dlmtagsComponent="table"] QScrollBar::handle:vertical,
QAbstractScrollArea[dlmtagsComponent="scrollArea"] QScrollBar::handle:vertical { min-height: 24px; }
QAbstractScrollArea[dlmtagsComponent="table"] QScrollBar::handle:horizontal,
QAbstractScrollArea[dlmtagsComponent="scrollArea"] QScrollBar::handle:horizontal { min-width: 24px; }
QAbstractScrollArea[dlmtagsComponent="table"] QScrollBar::handle:hover,
QAbstractScrollArea[dlmtagsComponent="scrollArea"] QScrollBar::handle:hover { background: palette(highlight); }
QAbstractScrollArea[dlmtagsComponent="table"] QScrollBar::add-line,
QAbstractScrollArea[dlmtagsComponent="table"] QScrollBar::sub-line,
QAbstractScrollArea[dlmtagsComponent="scrollArea"] QScrollBar::add-line,
QAbstractScrollArea[dlmtagsComponent="scrollArea"] QScrollBar::sub-line {
    width: 0px; height: 0px; border: none; background: transparent;
}
QAbstractScrollArea[dlmtagsComponent="table"] QScrollBar::add-page,
QAbstractScrollArea[dlmtagsComponent="table"] QScrollBar::sub-page,
QAbstractScrollArea[dlmtagsComponent="scrollArea"] QScrollBar::add-page,
QAbstractScrollArea[dlmtagsComponent="scrollArea"] QScrollBar::sub-page { background: transparent; }
"""


def style_component(widget: QtWidgets.QWidget, component: str) -> DLMtagsTheme:
    """Opt one widget into local QSS and runtime refresh, once per instance.

    Owns this widget's stylesheet. Use 'search' on QLineEdit and 'scrollArea'
    on an existing filesystem view without replacing its model or handlers.
    """
    facade = widget.findChild(DLMtagsTheme, 'dlmtagsTheme', QtCore.Qt.FindChildOption.FindDirectChildrenOnly)
    widget.setProperty('dlmtagsComponent', component)
    if isinstance(widget, QtWidgets.QAbstractScrollArea):
        widget.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        widget.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    widget.setStyleSheet(COMPONENT_STYLE)
    if facade is None:
        facade = DLMtagsTheme(widget)
        facade.setObjectName('dlmtagsTheme')
    return facade
