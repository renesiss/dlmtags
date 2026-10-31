# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""Synchronized original/new panels backed by the existing MetadataBox controller."""

from decimal import (
    Decimal,
    InvalidOperation,
)

from PyQt6 import (
    QtCore,
    QtGui,
    QtWidgets,
)

from picard.config import get_config
from picard.const import RATING_STEPS
from picard.i18n import gettext as _
from picard.smartscan import (
    REASON_LABELS,
    Outcome,
    release_year,
)
from picard.tags import display_tag_name
from picard.tagprovenance import (
    provider_name,
    source_for,
)

from picard.ui.dlmtags.components.card import Card
from picard.ui.dlmtags.components.styles import (
    FORM_LABEL_WIDTH,
    FORM_PANEL_GAP,
    FORM_PANEL_PADDING,
    FORM_ROW_HEIGHT,
    style_component,
)
from picard.ui.dlmtags.confirm_results import (
    ConfirmResultsDialog,
    can_confirm,
)
from picard.ui.dlmtags.find_tag import FindTagDialog
from picard.ui.dlmtags.sidebar_style import sidebar_icon
from picard.ui.dlmtags.suggestions import (
    SUGGESTED_COLOR,
    album_suggestion,
    provenance_text,
    tag_suggestion,
)
from picard.ui.dlmtags.tag_editor import PRIMARY_FIELDS
from picard.ui.dlmtags.theme import original_value_palette


FIND_ICON_SIZE = 16


def display_value(tag, value):
    """Year shows only the year ('2021' for '2021-04-09'); the tag keeps the full date."""
    if tag == 'date':
        return release_year(value) or value
    return value
# Larger than the icon so the button is easy to hit.
FIND_BUTTON_SIZE = 26
# Narrowest readable value and (wrapped) source label in a New Tags row.
FORM_VALUE_MIN_WIDTH = 80
FORM_SOURCE_MIN_WIDTH = 70



# Fields that show an unverified TagSuggestion of the last Smart Scan.
SUGGESTED_TAGS = frozenset({'artist', 'title', 'date', 'genre', 'style'})

class OriginalValue(QtWidgets.QLabel):
    """Elide a displayed value without changing its full tooltip or metadata."""

    def set_value(self, value):
        self._full_text = value
        self.setToolTip(value)
        self._elide()
        self._update_tone()

    def _update_tone(self):
        if getattr(self, '_updating_tone', False) or self.parentWidget() is None:
            return
        self._updating_tone = True
        try:
            self.setPalette(original_value_palette(self.parentWidget().palette()))
        finally:
            self._updating_tone = False

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.Type.PaletteChange:
            self._update_tone()
        elif event.type() == QtCore.QEvent.Type.FontChange:
            self._elide()

    def _elide(self):
        self.setText(self.fontMetrics().elidedText(
            getattr(self, '_full_text', ''), QtCore.Qt.TextElideMode.ElideRight, max(0, self.width() - 12),
        ))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()


class FormLineEdit(QtWidgets.QLineEdit):
    """Show an elided preview while unfocused, keeping the actual editable text."""

    def paintEvent(self, event):
        if self.hasFocus() or self.fontMetrics().horizontalAdvance(self.text()) <= self.width() - 12:
            super().paintEvent(event)
            return
        painter = QtWidgets.QStylePainter(self)
        option = QtWidgets.QStyleOptionFrame()
        option.initFrom(self)
        option.rect = self.rect()
        option.lineWidth = self.style().pixelMetric(QtWidgets.QStyle.PixelMetric.PM_DefaultFrameWidth)
        painter.drawPrimitive(QtWidgets.QStyle.PrimitiveElement.PE_PanelLineEdit, option)
        painter.setPen(self.palette().color(QtGui.QPalette.ColorRole.Text))
        text = self.fontMetrics().elidedText(self.text(), QtCore.Qt.TextElideMode.ElideRight, self.width() - 12)
        painter.drawText(self.rect().adjusted(6, 0, -6, 0), QtCore.Qt.AlignmentFlag.AlignVCenter, text)


class NotebookRow(QtWidgets.QFrame):
    """Shared geometry keeps every separator and value divider aligned."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty('notebookRow', True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_Hover)
        self.grid = QtWidgets.QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 1)
        self.grid.setSpacing(0)
        self.grid.setColumnMinimumWidth(0, FORM_LABEL_WIDTH)
        self.grid.setColumnStretch(1, 1)
        self.grid.setColumnMinimumWidth(2, 12)
        self.grid.setColumnStretch(3, 1)
        self.setMinimumHeight(FORM_ROW_HEIGHT)


class TagEditor(QtWidgets.QScrollArea):
    """No independent tag store: commits delegate to Picard's metadata APIs."""

    tag_search_started = QtCore.pyqtSignal(object)

    def __init__(self, metadata_box, parent=None):
        super().__init__(parent)
        self.box = metadata_box
        self._rows = {}
        self._find_buttons = {}
        self._source_labels = {}
        self._find_dialogs = {}
        self._observed_files = set()
        self._metadata_timer = QtCore.QTimer(self)
        self._metadata_timer.setSingleShot(True)
        self._metadata_timer.timeout.connect(self.box.update)
        self._dirty = set()
        self._refreshing = False
        self._committing = False
        self.setWidgetResizable(True)
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.body = QtWidgets.QWidget(self)
        self.body.setObjectName('dlmtagsTagForm')
        self.setWidget(self.body)
        self.layout_grid = QtWidgets.QGridLayout(self.body)
        self.layout_grid.setContentsMargins(8, 8, 8, 8)
        self.layout_grid.setSpacing(FORM_PANEL_GAP)
        self.layout_grid.setColumnStretch(0, 1)
        self.layout_grid.setColumnStretch(1, 1)
        self._primary_pairs = {}
        self.original_panel, self.original_grid = self._make_panel(_('ORIGINAL TAGS'))
        self.new_panel, self.new_grid = self._make_panel(_('NEW TAGS'))
        self._confirm_dialog = None
        self._make_confirm_banner()
        panels = QtWidgets.QWidget(self.body)
        panel_layout = QtWidgets.QHBoxLayout(panels)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(FORM_PANEL_GAP)
        panel_layout.addWidget(self.original_panel, 1)
        panel_layout.addWidget(self.new_panel, 1)
        # Keep equal panel widths and accessible controls at narrow splitter
        # sizes. Only this viewport scrolls; metadata and tooltips stay intact.
        # A New Tags row also holds its source label and Find button, so the
        # value keeps FORM_VALUE_MIN_WIDTH instead of being squeezed to nothing.
        panel_width = (
            FORM_LABEL_WIDTH + FORM_PANEL_PADDING * 2 + 6 + FORM_VALUE_MIN_WIDTH
            + FORM_SOURCE_MIN_WIDTH + FIND_BUTTON_SIZE
        )
        panels.setMinimumWidth(2 * panel_width + FORM_PANEL_GAP)
        self.layout_grid.addWidget(panels, 0, 0, 1, 2)
        for row, (tag, label) in enumerate(PRIMARY_FIELDS):
            self._add_row(self.new_grid, row, tag, _(label), primary=True)
        self.other_search = QtWidgets.QToolButton(self.new_panel)
        self.other_search.setText(_('Find another tag…'))
        self.other_search.clicked.connect(self._find_other_tag)
        self.new_panel.content_layout.addWidget(self.other_search)
        self.layout_grid.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        self.box.presentation_changed.connect(self.refresh)
        style_component(self, 'scrollArea')
        self.refresh()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in {QtCore.QEvent.Type.PaletteChange, QtCore.QEvent.Type.StyleChange}:
            for button in getattr(self, '_find_buttons', {}).values():
                button.setIcon(sidebar_icon(button, 'search'))

    def _make_confirm_banner(self):
        """Needs Review with a recording the user can confirm: a visible action
        next to the unverified values, opening Confirm Results."""
        banner = self.confirm_banner = QtWidgets.QFrame(self.new_panel)
        banner.setObjectName('dlmtags_confirm_banner')
        banner.setStyleSheet(
            'QFrame#dlmtags_confirm_banner { border: 1px solid %s; border-radius: 6px; }' % SUGGESTED_COLOR
        )
        layout = QtWidgets.QHBoxLayout(banner)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)
        self.confirm_message = QtWidgets.QLabel(banner)
        self.confirm_message.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.confirm_message.setWordWrap(True)
        self.confirm_message.setStyleSheet('border: none;')
        layout.addWidget(self.confirm_message, 1)
        self.confirm_results_button = QtWidgets.QPushButton(_('Confirm'), banner)
        self.confirm_results_button.setObjectName('dlmtags_confirm_results_action')
        self.confirm_results_button.setToolTip(_('Confirm the recording, then find the remaining tags'))
        self.confirm_results_button.clicked.connect(self._confirm_results)
        layout.addWidget(self.confirm_results_button)
        # Below the NEW TAGS heading, above the fields.
        self.new_panel.content_layout.insertWidget(1, banner)
        banner.hide()

    def _confirmable_file(self):
        file = next(iter(self.box.files)) if len(self.box.files) == 1 else None
        decision = getattr(file, 'smart_scan_decision', None)
        if decision is None or decision.outcome != Outcome.NEEDS_REVIEW or not can_confirm(file):
            return None
        return file

    def _refresh_confirm_banner(self):
        file = self._confirmable_file()
        if file is None:
            self.confirm_banner.hide()
            return
        recordings = [r for r in file.confirmable_recordings() if r.confirmable]
        reasons = '; '.join(_(REASON_LABELS[reason]) for reason in file.smart_scan_decision.reasons)
        if len(recordings) == 1:
            message = _('MusicBrainz found %(song)s, not verified automatically: %(reasons)s.') % {
                'song': '%s — %s' % (recordings[0].artist, recordings[0].title), 'reasons': reasons,
            }
        else:
            message = _('%(count)d MusicBrainz recordings need your choice: %(reasons)s.') % {
                'count': len(recordings), 'reasons': reasons,
            }
        edits = file.manual_identity_edits()
        if edits:
            names = {'artist': _('Artist'), 'title': _('Title')}
            message += ' ' + _('Your edits to %s are kept.') % ', '.join(names[tag] for tag in edits)
        self.confirm_message.setText(message)
        self.confirm_banner.show()

    def _confirm_results(self):
        file = self._confirmable_file()
        if file is None or not self.commit_pending():
            return
        # Every confirmation uses the same four-field review, including a
        # single plausible recording that the dialog preselects.
        dialog = self._confirm_dialog = ConfirmResultsDialog(file, self)
        dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.finished.connect(self._confirm_finished)
        dialog.open()

    def _confirm_finished(self, *args):
        self._confirm_dialog = None
        self.box.update()

    def _make_panel(self, title):
        panel = Card(self.body, sidebar=True, shadow=True)
        panel.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        panel.setMinimumWidth(0)
        padding = FORM_PANEL_PADDING
        panel.content_layout.setContentsMargins(padding, padding, padding, padding)
        panel.content_layout.setSpacing(0)
        heading = QtWidgets.QLabel(title, panel)
        heading.setProperty('dualPanelHeading', True)
        panel.content_layout.addWidget(heading)
        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        panel.content_layout.addLayout(grid)
        return panel, grid

    def _add_row(self, grid, row, tag, label, *, primary=False):
        notebook_row = NotebookRow(grid.parentWidget())
        name = QtWidgets.QLabel(label, notebook_row)
        name.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        name.setProperty('formLabel', True)
        name.setFixedWidth(FORM_LABEL_WIDTH)
        name.setToolTip(label)
        original = OriginalValue(self.body)
        original.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        original.setProperty('originalValue', True)
        original.setMinimumWidth(0)
        original.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed)
        arrow = QtWidgets.QLabel('', self.body)
        arrow.setProperty('metadataIndicator', True)
        arrow.setFixedWidth(6)
        host = QtWidgets.QWidget(self.body)
        host_layout = QtWidgets.QHBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(0)
        if tag == '~rating':
            control = QtWidgets.QComboBox(host)
            control.addItem('—', '')
            control.addItem(_('Unrated (0)'), '0')
            for value in range(1, RATING_STEPS):
                control.addItem('★' * value, str(value))
            control.activated.connect(lambda unused, key=tag: self._activated(key))
        elif tag == 'genre':
            control = QtWidgets.QComboBox(host)
            control.setEditable(True)
            control.lineEdit().textEdited.connect(lambda unused, key=tag: self._mark_dirty(key))
            control.lineEdit().editingFinished.connect(self.commit_pending)
        elif tag == 'comment':
            control = QtWidgets.QPlainTextEdit(host)
            control.setFixedHeight(FORM_ROW_HEIGHT)
            control.textChanged.connect(lambda key=tag: self._comment_changed(key))
        else:
            control = FormLineEdit(host)
            control.textEdited.connect(lambda unused, key=tag: self._mark_dirty(key))
            control.editingFinished.connect(self.commit_pending)
            if tag == 'bpm':
                validator = QtGui.QDoubleValidator(control)
                validator.setBottom(0)
                validator.setNotation(QtGui.QDoubleValidator.Notation.StandardNotation)
                validator.setLocale(QtCore.QLocale.c())
                control.setValidator(validator)
        if isinstance(control, (QtWidgets.QLineEdit, QtWidgets.QPlainTextEdit)):
            control.setPlaceholderText('—')
        elif isinstance(control, QtWidgets.QComboBox) and control.isEditable():
            control.lineEdit().setPlaceholderText('—')
        control.setMinimumWidth(0)
        if not isinstance(control, QtWidgets.QPlainTextEdit):
            control.setMinimumHeight(FORM_ROW_HEIGHT)
        control.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed)
        control.setAccessibleName(label)
        control.setProperty('metadataField', tag)
        control.installEventFilter(self)
        if isinstance(control, QtWidgets.QPlainTextEdit):
            control.viewport().setProperty('metadataField', tag)
            control.viewport().installEventFilter(self)
        if isinstance(control, QtWidgets.QComboBox) and control.isEditable():
            control.lineEdit().setProperty('metadataField', tag)
            control.lineEdit().installEventFilter(self)
        if primary:
            host_layout.addWidget(arrow)
        host_layout.addWidget(control, 1)
        source_label = QtWidgets.QLabel(host)
        source_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        source_label.setOpenExternalLinks(True)
        source_label.setVisible(False)
        source_label.setMaximumWidth(110)
        source_label.setWordWrap(True)
        font = source_label.font()
        if font.pointSizeF() > 1:
            font.setPointSizeF(font.pointSizeF() - 1)
        source_label.setFont(font)
        host_layout.addWidget(source_label)
        self._source_labels[tag] = source_label
        find_button = QtWidgets.QToolButton(host)
        find_button.setObjectName('dlmtags_find_%s_button' % tag.lstrip('~'))
        # Palette-drawn symbol: follows dark/light themes and HiDPI.
        find_button.setIcon(sidebar_icon(find_button, 'search'))
        find_button.setIconSize(QtCore.QSize(FIND_ICON_SIZE, FIND_ICON_SIZE))
        find_button.setMinimumSize(FIND_BUTTON_SIZE, FIND_BUTTON_SIZE)
        find_button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        if tag == 'album':
            find_button.setAccessibleName(_('Find or change album'))
            find_button.setToolTip(_('Find or change album'))
        else:
            find_button.setAccessibleName(_('Find %s') % label)
            find_button.setToolTip(_('Find online suggestions for %s') % label)
        find_button.setAutoRaise(True)
        find_button.clicked.connect(lambda unused, key=tag: self._find_tag(key))
        host_layout.addWidget(find_button)
        self._find_buttons[tag] = find_button
        if primary:
            original_row = NotebookRow(self.original_panel)
            for aligned_row in (original_row, notebook_row):
                aligned_row.grid.setColumnMinimumWidth(2, 0)
                aligned_row.grid.setColumnStretch(3, 0)
                aligned_row.setMinimumHeight(FORM_ROW_HEIGHT + 1)
            old_label = QtWidgets.QLabel(label, original_row)
            old_label.setProperty('formLabel', True)
            old_label.setFixedWidth(FORM_LABEL_WIDTH)
            old_label.setToolTip(label)
            original_row.grid.addWidget(old_label, 0, 0)
            original_row.grid.addWidget(original, 0, 1)
            notebook_row.grid.addWidget(name, 0, 0)
            notebook_row.grid.addWidget(host, 0, 1)
            self.original_grid.addWidget(original_row, row, 0)
            grid.addWidget(notebook_row, row, 0)
            self._primary_pairs[tag] = (original_row, notebook_row)
        else:
            for column, widget in enumerate((name, original, arrow, host)):
                notebook_row.grid.addWidget(widget, 0, column)
            grid.addWidget(notebook_row, row, 0, 1, 4)
        self._rows[tag] = (original, arrow, control)

    def _sync_files(self):
        files = set(self.box.files)
        if files == self._observed_files:
            return
        for dialog in list(self._find_dialogs.values()):
            dialog.reject()
        for file in self._observed_files - files:
            file.metadata_updated.disconnect(self._queue_metadata_update)
        for file in files - self._observed_files:
            file.metadata_updated.connect(self._queue_metadata_update)
        self._observed_files = files

    def _queue_metadata_update(self):
        # The sidebar's file selection is independent of hidden tree items.
        self._metadata_timer.start(0)

    def _find_tag(self, tag):
        if len(self.box.files) != 1 or not self.box._tag_is_editable(tag) or not self.commit_pending():
            return
        if tag in self._find_dialogs:
            self._find_dialogs[tag].raise_()
            self._find_dialogs[tag].activateWindow()
            return
        file = next(iter(self.box.files))
        dialog = FindTagDialog(file, tag, self)
        self._find_dialogs[tag] = dialog
        dialog.finished.connect(lambda result, key=tag: self._find_dialogs.pop(key, None))
        dialog.show()
        dialog.start()
        self.tag_search_started.emit(file)

    def _find_other_tag(self):
        diff = self.box.tag_diff
        if diff is None:
            return
        menu = QtWidgets.QMenu(self)
        for tag in diff.tag_names:
            if tag not in self._rows and self.box._tag_is_editable(tag):
                action = menu.addAction(display_tag_name(tag))
                action.triggered.connect(lambda unused, key=tag: self._find_tag(key))
        if not menu.isEmpty():
            menu.exec(self.other_search.mapToGlobal(self.other_search.rect().bottomLeft()))

    def _mark_dirty(self, tag):
        if not self._refreshing:
            self._dirty.add(tag)
            self._source_labels[tag].setText(_('Manual'))
            self._source_labels[tag].setStyleSheet('')
            self._source_labels[tag].setToolTip(_('Source: Manual'))
            self._source_labels[tag].setVisible(True)
            self._update_indicator(tag, [self._value(tag)])

    def _activated(self, tag):
        self._mark_dirty(tag)
        self.commit_pending()

    def _comment_changed(self, tag):
        self._mark_dirty(tag)
        control = self._rows[tag][2]
        expanded = '\n' in control.toPlainText()
        diff = self.box.tag_diff
        if diff is not None:
            expanded = expanded or any('\n' in value for value in diff.old[tag])
        height = FORM_ROW_HEIGHT * (2 if expanded else 1)
        control.setFixedHeight(height)
        if tag in self._primary_pairs:
            for row in self._primary_pairs[tag]:
                row.setFixedHeight(height + 1)

    def eventFilter(self, watched, event):
        tag = watched.property('metadataField')
        if (tag and event.type() == QtCore.QEvent.Type.MouseButtonPress
                and event.button() == QtCore.Qt.MouseButton.LeftButton and watched.property('multivalue')):
            self._edit_multiple(tag)
            return True
        if tag and watched.property('multivalue') and event.type() == QtCore.QEvent.Type.Wheel:
            return True
        if tag and event.type() == QtCore.QEvent.Type.KeyPress:
            if (watched.property('multivalue') and isinstance(watched, QtWidgets.QComboBox)
                    and not watched.isEditable() and event.key() not in (
                        QtCore.Qt.Key.Key_Tab, QtCore.Qt.Key.Key_Backtab, QtCore.Qt.Key.Key_Escape,
                    )):
                self._edit_multiple(tag)
                return True
            if event.key() == QtCore.Qt.Key.Key_Escape:
                self._dirty.discard(tag)
                self._refreshing = True
                try:
                    self._refresh_row(tag)
                finally:
                    self._refreshing = False
                return True
            if event.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
                if event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier:
                    self._edit_multiple(tag)
                elif (isinstance(watched, QtWidgets.QPlainTextEdit)
                      and event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier):
                    return False
                else:
                    self.commit_pending()
                return True
            if isinstance(watched, QtWidgets.QPlainTextEdit) and event.key() == QtCore.Qt.Key.Key_Tab:
                self.commit_pending()
                self.focusNextPrevChild(not bool(event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier))
                return True
        if event.type() == QtCore.QEvent.Type.FocusOut and isinstance(watched, QtWidgets.QPlainTextEdit):
            self.commit_pending()
        return super().eventFilter(watched, event)

    def _value(self, tag):
        control = self._rows[tag][2]
        if isinstance(control, QtWidgets.QPlainTextEdit):
            return control.toPlainText()
        if isinstance(control, QtWidgets.QComboBox):
            return control.currentText() if control.isEditable() else control.currentData()
        return control.text()

    def commit_pending(self):
        if self._committing or self._refreshing or not self._dirty:
            return True
        changes = {tag: [self._value(tag)] for tag in self._dirty}
        if not all(self.box._validate_values(tag, values) for tag, values in changes.items()):
            return False
        self._committing = True
        try:
            self._dirty.clear()
            for tag, values in changes.items():
                self.box._set_tag_values(tag, values)
            self.box.update()
        finally:
            self._committing = False
        return True

    def _edit_multiple(self, tag):
        if self.commit_pending():
            self.box._edit_tag(tag)
            self.box.update()

    def _apply_tag_visibility(self):
        visible = set(get_config().setting['dlmtags_visible_tags'])
        for tag, (old_row, new_row) in self._primary_pairs.items():
            enabled = tag in visible
            old_row.setVisible(enabled)
            new_row.setVisible(enabled)

    def refresh(self):
        self._sync_files()
        self._apply_tag_visibility()
        self._refresh_confirm_banner()
        self.other_search.setEnabled(len(self.box.files) == 1)
        self._refreshing = True
        try:
            for tag in self._rows:
                if tag not in self._dirty:
                    self._refresh_row(tag)
        finally:
            self._refreshing = False

    def _refresh_row(self, tag):
        diff = self.box.tag_diff
        original, arrow, control = self._rows[tag]
        active = diff is not None
        row = diff.tag_names.index(tag) if active else None
        original_text = self.box.item(row, 1).text() if active else ''
        original.set_value(display_value(tag, original_text) or '—')
        if original_text and tag == 'date':
            original.setToolTip(original_text)
        new = diff.new[tag] if active else ['']
        grouped = active and diff.new.status(tag).is_grouped
        multi = grouped or len(new) > 1
        writable = active and self.box._tag_is_editable(tag)
        self._find_buttons[tag].setEnabled(writable and len(self.box.files) == 1)
        self._find_buttons[tag].setVisible(not tag.startswith('~') or tag == '~rating')
        shown = self.box.item(row, 2).text() if active else ''
        value = new[0] if new else ''
        if isinstance(control, QtWidgets.QComboBox) and not control.isEditable():
            position = control.findData(value)
            if position < 0:
                control.addItem(value, value)
                position = control.count() - 1
            control.setCurrentIndex(position)
            control.setEnabled(writable)
        elif isinstance(control, QtWidgets.QComboBox):
            control.setEditText(shown if multi else value)
            control.lineEdit().setReadOnly(not writable or multi)
            control.setEnabled(active)
        elif isinstance(control, QtWidgets.QPlainTextEdit):
            control.setPlainText(shown if multi else value)
            control.setReadOnly(not writable or multi)
        else:
            control.setText(shown if multi or tag.startswith('~') else display_value(tag, value))
            control.setReadOnly(not writable or multi)
        # An unverified album is only a preview in the empty field. It never
        # reaches file.metadata or the tag diff until the user chooses it.
        suggestion = None
        if (tag == 'album' and active and not value and not multi and tag not in self._dirty
                and len(self.box.files) == 1):
            suggestion = album_suggestion(next(iter(self.box.files)))
        # Artist/Title of an unverified recording, and Year/Genre/Style found
        # after a confirmation, likewise only previews. Artist/Title are shown
        # instead of Picard's untouched file-name guess, which stays the
        # field's value (and Save's) until the user chooses.
        name_suggestion = None
        if (tag in SUGGESTED_TAGS and active and not multi and tag not in self._dirty
                and len(self.box.files) == 1):
            name_suggestion = tag_suggestion(next(iter(self.box.files)), tag)
        if name_suggestion and name_suggestion.replaces_guess and isinstance(control, QtWidgets.QLineEdit):
            control.setText('')
        placeholder = suggestion.values[0] if suggestion else name_suggestion and name_suggestion.value
        placeholder_edit = control
        if isinstance(control, QtWidgets.QComboBox) and control.isEditable():
            placeholder_edit = control.lineEdit()
        if (tag == 'album' or tag in SUGGESTED_TAGS) and isinstance(placeholder_edit, QtWidgets.QLineEdit):
            palette = placeholder_edit.palette()
            palette.setColor(
                QtGui.QPalette.ColorRole.PlaceholderText,
                QtGui.QColor(SUGGESTED_COLOR) if placeholder else self.palette().color(
                    QtGui.QPalette.ColorRole.PlaceholderText
                ),
            )
            placeholder_edit.setPalette(palette)
            placeholder_edit.setPlaceholderText(placeholder or '—')

        source = ''
        if active and len(self.box.files) == 1:
            file = next(iter(self.box.files))
            source = source_for(file, tag)
        control.setToolTip(shown + ('\n' + _('Source: %s') % source if source else ''))
        source_label = self._source_labels[tag]
        source_label.setStyleSheet('')
        date_suggestion = None
        if tag == 'date' and active and value and not multi and tag not in self._dirty and len(self.box.files) == 1:
            date_suggestion = getattr(file, 'dlmtags_date_suggestion', None)
        if date_suggestion:
            # A different first-release year is offered, never written over the date.
            first, provider, unused = date_suggestion
            tooltip = '\n'.join(filter(None, (
                _('Suggested year, not applied: %s') % display_value('date', first),
                _('Source: %s, first release of the recording (%s)') % (provider, first),
                _('Current value source: %s') % source if source else '',
                _('Use Find to change the year.'),
            )))
            control.setToolTip(tooltip)
            source_label.setText(_('Suggested %s') % display_value('date', first))
            source_label.setToolTip(tooltip)
            source_label.setStyleSheet('color: %s;' % SUGGESTED_COLOR)
            source_label.setVisible(True)
        elif name_suggestion:
            tooltip = '\n'.join(filter(None, (
                _('Suggested, not applied: %s') % name_suggestion.value,
                _('Source: %s') % provenance_text(name_suggestion),
                _('MusicBrainz recording: %s') % name_suggestion.recording_id if name_suggestion.recording_id else '',
                _('The recording was not verified.'),
                _('Current value from the file name, written on Save unless changed: %s') % shown
                if name_suggestion.replaces_guess else '',
                _('Use Find or edit the field to choose a value.'),
            )))
            control.setToolTip(tooltip if name_suggestion.replaces_guess or not shown else shown + '\n' + tooltip)
            source_label.setText(provenance_text(name_suggestion))
            source_label.setToolTip(tooltip)
            source_label.setStyleSheet('color: %s;' % SUGGESTED_COLOR)
            source_label.setVisible(True)
        elif source:
            if source.startswith('Discogs') and active and len(self.box.files) == 1:
                url = getattr(file, 'dlmtags_tag_urls', {}).get(tag, '')
                if url.startswith('https://www.discogs.com/'):
                    source_label.setText('<a href="%s">Discogs</a>' % url.replace('&', '&amp;').replace('"', '&quot;'))
                else:
                    source_label.setText('Discogs')
            else:
                source_label.setText(provider_name(source).replace('&', '&amp;').replace('<', '&lt;'))
            source_label.setToolTip(_('Source: %s') % source)
            source_label.setVisible(True)
        elif suggestion:
            tooltip = _('Suggested album, not applied: %s') % suggestion.values[0] + '\n' + (
                _('Source: %s') % suggestion.source
            ) + '\n' + _('Use Find or change album to choose it.')
            control.setToolTip(tooltip)
            source_label.setText(_('Suggested'))
            source_label.setToolTip(tooltip)
            source_label.setStyleSheet('color: %s;' % SUGGESTED_COLOR)
            source_label.setVisible(True)
        else:
            source_label.setText('')
            source_label.setVisible(False)
        control.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus if writable else QtCore.Qt.FocusPolicy.NoFocus)
        self._update_indicator(tag)
        control.setProperty('multivalue', bool(writable and multi))
        if isinstance(control, QtWidgets.QPlainTextEdit):
            control.viewport().setProperty('multivalue', bool(writable and multi))
        if isinstance(control, QtWidgets.QComboBox) and control.isEditable():
            control.lineEdit().setProperty('multivalue', bool(writable and multi))
        if writable and multi:
            control.setToolTip(shown + '\n' + _('Click or press Ctrl+Enter to edit all values.'))

    @staticmethod
    def _comparison_values(tag, values):
        # Empty and absent are the same visible value; preserve multivalue order.
        if not values or values == ['']:
            return ()
        if tag in ('bpm', '~rating'):
            try:
                numbers = tuple(Decimal(value) for value in values)
                if all(number.is_finite() for number in numbers):
                    return numbers
            except InvalidOperation:
                pass
        return tuple(values)

    def _update_indicator(self, tag, draft=None):
        changed = False
        if not tag.startswith('~') or tag == '~rating':
            for file in self.box.files:
                old = file.orig_metadata.getall(tag)
                new = draft if draft is not None else file.metadata.getall(tag)
                if draft is None and tag in file.metadata.deleted_tags:
                    new = []
                if self._comparison_values(tag, old) != self._comparison_values(tag, new):
                    changed = True
                    break
        indicator = self._rows[tag][1]
        indicator.setText('•' if changed else '')
        indicator.setToolTip(_('Modified value') if changed else '')
        indicator.setAccessibleName(_('Modified value') if changed else '')
