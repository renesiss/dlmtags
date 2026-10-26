# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""Structured presentation of Picard's real MetadataBox editing pipeline."""

from decimal import (
    Decimal,
    InvalidOperation,
)

from PyQt6 import (
    QtCore,
    QtGui,
    QtWidgets,
)

from picard.const import RATING_STEPS
from picard.i18n import (
    N_,
    gettext as _,
)

from picard.ui.dlmtags.components.styles import (
    EDITOR_FIELD_PADDING,
    EDITOR_ROW_HEIGHT,
)
from picard.ui.metadatabox import (
    DIFF_HTML_ROLE,
    MetadataBox,
    TableTagEditorDelegate,
)
from picard.ui.metadatabox.edittagdialog import EditTagDialog


# Labels are presentation aliases; values remain native Picard metadata keys.
PRIMARY_FIELDS = (
    ('artist', N_('Artist')), ('title', N_('Title')), ('album', N_('Album')),
    ('albumartist', N_('Album Artist')), ('tracknumber', N_('Track')), ('discnumber', N_('Disc')),
    ('date', N_('Year')), ('genre', N_('Genre')), ('style', N_('Style')), ('~rating', N_('Rating')),
    ('bpm', N_('BPM')), ('~length', N_('Duration')), ('subtitle', N_('Version/Mix')), ('comment', N_('Comment')),
)
PRIMARY_TAGS = tuple(tag for tag, label in PRIMARY_FIELDS)


class ValidatedTagDialog(EditTagDialog):
    """Keep Picard's multivalue dialog with the same capability/value guards."""

    def accept(self):
        for tag, values in self.modified_tags.items():
            if not self.metadata_box._validate_values(tag, values):
                return
        super().accept()


class StructuredDelegate(TableTagEditorDelegate):
    def createEditor(self, parent, option, index):
        tag = self.get_tag_name(index)
        if tag == '~rating':
            editor = QtWidgets.QComboBox(parent)
            editor.addItem('', '')
            editor.addItem(_('Unrated (0)'), '0')
            for value in range(1, RATING_STEPS):
                editor.addItem('★' * value, str(value))
            return editor
        editor = super().createEditor(parent, option, index)
        if tag == 'bpm' and isinstance(editor, QtWidgets.QLineEdit):
            validator = QtGui.QDoubleValidator(editor)
            validator.setBottom(0)
            validator.setNotation(QtGui.QDoubleValidator.Notation.StandardNotation)
            validator.setLocale(QtCore.QLocale.c())
            editor.setValidator(validator)
        return editor

    def setEditorData(self, editor, index):
        if isinstance(editor, QtWidgets.QComboBox) and self.get_tag_name(index) == '~rating':
            value = self.parent().tag_diff.new['~rating'][0]
            position = editor.findData(value)
            if position < 0:
                # Keep an unusual existing value until the user chooses a new one.
                editor.addItem(value, value)
                position = editor.count() - 1
            editor.setCurrentIndex(position)
        else:
            super().setEditorData(editor, index)


class StructuredMetadataBox(MetadataBox):
    """One metadata store, fixed primary fields and collapsible remaining rows."""

    presentation_changed = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('dlmtagsTagEditor')
        self.advanced_action = QtGui.QAction(_('Advanced Details'), self)
        self.advanced_action.setCheckable(True)
        self.advanced_action.toggled.connect(self._show_advanced)
        self.setItemDelegate(StructuredDelegate(self))
        self.setHorizontalHeaderLabels([_('Field'), _('Original Value'), _('New Value')])
        self.setShowGrid(False)
        self.setTextElideMode(QtCore.Qt.TextElideMode.ElideRight)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._configure_columns()
        self._update_items()

    def _configure_columns(self):
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Fixed)
        width = max(self.fontMetrics().horizontalAdvance(_(label)) for tag, label in PRIMARY_FIELDS) + EDITOR_FIELD_PADDING
        self.setColumnWidth(0, width)
        for column in (1, 2):
            header.setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeMode.Stretch)

    def restore_state(self):
        super().restore_state()
        self._configure_columns()

    def _add_files_to_tag_diff(self, files, tag_diff, config, top_tags):
        super()._add_files_to_tag_diff(files, tag_diff, config, top_tags)
        for file in files:
            old, new = file.orig_metadata, file.metadata
            actual = set(old) | set(new)
            for tag in actual | set(PRIMARY_TAGS):
                # The superclass already collected supported writable fields.
                if tag in {'~length', '~filepath'} or (tag in actual and not tag.startswith('~')
                                                       and file.supports_tag(tag)):
                    continue
                readonly = (tag != '~rating' and tag.startswith('~')) or not file.supports_tag(tag)
                tag_diff.add(tag, old=old.getall(tag), new=new.getall(tag),
                             removed=tag in new.deleted_tags, readonly=readonly, removable=not readonly,
                             top_tags=set(PRIMARY_TAGS))

    def _writable(self, tag):
        if tag.startswith('~') and tag != '~rating':
            return False
        return bool(self.files) and all(file.can_edit_tags and file.supports_tag(tag) for file in self.files)

    def _tag_is_editable(self, tag):
        return self._writable(tag) and super()._tag_is_editable(tag)

    def _tag_is_removable(self, tag):
        return self._writable(tag) and super()._tag_is_removable(tag)

    def _update_items(self, result=None, error=None):
        if self.editing:
            return
        if result is not None:
            # Reorder only the presentation; never inject values into metadata.
            result.tag_names = list(PRIMARY_TAGS) + [tag for tag in result.tag_names if tag not in PRIMARY_TAGS]
        super()._update_items(result, error)
        active = self.tag_diff is not None
        self.setEnabled(active)
        if not active:
            self.setRowCount(len(PRIMARY_FIELDS))
            for row, (tag, label) in enumerate(PRIMARY_FIELDS):
                for column, value in enumerate((_(label), '', '')):
                    item = QtWidgets.QTableWidgetItem(value)
                    item.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
                    self.setItem(row, column, item)
            self.advanced_action.setEnabled(False)
            self.presentation_changed.emit()
            return
        self.advanced_action.setEnabled(True)
        for row, tag in enumerate(self.tag_diff.tag_names):
            item = self.item(row, self.COLUMN_NEW)
            if not self._writable(tag):
                item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
            for column in (self.COLUMN_ORIG, self.COLUMN_NEW):
                self.item(row, column).setToolTip(self.item(row, column).text())
            self.setRowHeight(row, max(EDITOR_ROW_HEIGHT, self.fontMetrics().height() + 8))
        for row, (tag, label) in enumerate(PRIMARY_FIELDS):
            self.item(row, self.COLUMN_TAG).setText(_(label))
        self._display_rating()
        self._display_duration()
        self._show_advanced(self.advanced_action.isChecked())
        self.presentation_changed.emit()

    def _display_rating(self):
        row = PRIMARY_TAGS.index('~rating')
        for column, values in ((self.COLUMN_ORIG, self.tag_diff.old), (self.COLUMN_NEW, self.tag_diff.new)):
            if not values.status('~rating').is_grouped:
                raw = values['~rating']
                if len(raw) == 1 and raw[0].isdigit() and 0 <= int(raw[0]) < RATING_STEPS:
                    item = self.item(row, column)
                    item.setText('★' * int(raw[0]) + '☆' * (RATING_STEPS - 1 - int(raw[0])))
                    item.setData(DIFF_HTML_ROLE, None)

    def _display_duration(self):
        row = PRIMARY_TAGS.index('~length')
        lengths = {file.orig_metadata.length for file in self.files}
        if len(lengths) != 1:
            return
        seconds = max(0, int(next(iter(lengths))) // 1000)
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        text = f'{hours}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes:02d}:{seconds:02d}'
        for column in (self.COLUMN_ORIG, self.COLUMN_NEW):
            item = self.item(row, column)
            item.setText(text)
            item.setToolTip(text)
            item.setData(DIFF_HTML_ROLE, None)

    def _show_advanced(self, checked):
        self.advanced_action.setText(_('Advanced Details') + (' ▾' if checked else ' ▸'))
        for row in range(len(PRIMARY_FIELDS), self.rowCount()):
            self.setRowHidden(row, not checked)

    @staticmethod
    def _get_editor_value(editor):
        if isinstance(editor, QtWidgets.QComboBox):
            return editor.currentData()
        return MetadataBox._get_editor_value(editor)

    def _validate_values(self, tag, values):
        if not self._writable(tag):
            QtWidgets.QMessageBox.information(self, _('Read-only field'), _('This field cannot be written for the selected files.'))
            return False
        try:
            for value in values:
                if value and tag == 'bpm' and (not Decimal(value).is_finite() or Decimal(value) < 0):
                    raise ValueError
                if value and tag == '~rating' and (not value.isdigit() or not 0 <= int(value) < RATING_STEPS):
                    raise ValueError
        except (InvalidOperation, ValueError):
            QtWidgets.QMessageBox.information(self, _('Invalid value'), _('Enter a valid non-negative BPM or a rating from 0 to 5.'))
            return False
        return True

    def _set_tag_values_delayed_updates(self, tag, values, objects=None):
        if not self._validate_values(tag, values):
            return
        yield from super()._set_tag_values_delayed_updates(tag, values, objects)

    def _edit_tag(self, tag):
        if self.tag_diff is not None and (tag is None or self._writable(tag)):
            ValidatedTagDialog(self, tag).exec()

    def closeEditor(self, editor, hint):
        if hint == QtWidgets.QAbstractItemDelegate.EndEditHint.RevertModelCache:
            QtWidgets.QTableWidget.closeEditor(self, editor, hint)
            self.editing = None
            self.update()
            return
        super().closeEditor(editor, hint)
