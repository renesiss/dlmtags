# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""Confirm Results: the user confirms one recording of a Needs Review scan.

Only the scan's own recordings are offered; nothing is searched again. The
confirmation is user-confirmed, never automatic: File.confirm_recording()
applies Artist/Title to New Tags and starts the remaining-tag suggestions.
"""

from PyQt6 import (
    QtGui,
    QtWidgets,
)

from picard.i18n import gettext as _
from picard.smartscan import (
    REASON_LABELS,
    preselected_recording,
)

from picard.ui.dlmtags.components.table import CompactTable


class ConfirmResultsDialog(QtWidgets.QDialog):
    """Choose and confirm the recording of the file's current review."""

    def __init__(self, file, parent=None, *, allowed_tags=None):
        super().__init__(parent)
        self.setObjectName('dlmtags_confirm_results')
        self.setWindowTitle(_('Confirm Results'))
        self.resize(820, 360)
        self.setMinimumSize(680, 360)
        self.file = file
        self.allowed_tags = frozenset(allowed_tags) if allowed_tags is not None else None
        self.decision = file.smart_scan_decision
        self.recordings = file.confirmable_recordings()
        layout = QtWidgets.QVBoxLayout(self)

        reasons = '; '.join(_(REASON_LABELS[reason]) for reason in getattr(self.decision, 'reasons', ()))
        self.reason_label = QtWidgets.QLabel(
            _('Smart Scan could not verify the recording automatically: %s') % reasons,
            self,
        )
        self.reason_label.setWordWrap(True)
        layout.addWidget(self.reason_label)

        self.table = CompactTable(self)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        model = QtGui.QStandardItemModel(self)
        model.setHorizontalHeaderLabels(
            [
                _('Artist'),
                _('Title / Version'),
                _('Duration'),
                _('AcoustID score'),
                _('Release'),
                _('Review reasons'),
            ]
        )
        for recording in self.recordings:
            candidate = next(
                (item for item in self.decision.candidates if item.recording_id == recording.recording_id), None
            )
            version = ', '.join(candidate.versions) if candidate else ''
            title = recording.title + (f' ({version})' if version else '')
            release = (
                '; '.join(f'{item.title} ({item.date})' if item.date else item.title for item in candidate.releases)
                if candidate
                else ''
            )
            values = (
                recording.artist or _('Artist credit not available'),
                title,
                f'{candidate.length / 1000:.1f} s' if candidate and candidate.length is not None else '',
                f'{candidate.fingerprint_score:.1%}' if candidate and candidate.fingerprint_score is not None else '',
                release,
                '; '.join(_(REASON_LABELS[reason]) for reason in recording.reasons),
            )
            row = []
            for value in values:
                item = QtGui.QStandardItem(value)
                item.setEditable(False)
                item.setToolTip(_('Recording ID: %s') % recording.recording_id if value == title else value)
                if not recording.confirmable:
                    item.setEnabled(False)
                    item.setToolTip(_('The MusicBrainz artist credit is not available. Scan again to retry.'))
                row.append(item)
            model.appendRow(row)
        self.table.setModel(model)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.selectionModel().selectionChanged.connect(self._update_buttons)
        layout.addWidget(self.table, 1)

        edits = self.file.manual_identity_edits()
        names = {'artist': _('Artist'), 'title': _('Title')}
        self.replace_check = QtWidgets.QCheckBox(
            _('Replace my edits of %s') % ', '.join(names[tag] for tag in edits),
            self,
        )
        self.replace_check.setVisible(bool(edits))
        layout.addWidget(self.replace_check)

        note = QtWidgets.QLabel(
            _(
                'Confirming applies Artist and Title to New Tags, then looks for album, year, genre, '
                'style and artwork suggestions to review. Nothing is saved to the file.'
            ),
            self,
        )
        note.setWordWrap(True)
        note.setProperty('dlmtagsHint', True)
        layout.addWidget(note)
        self.error_label = QtWidgets.QLabel(self)
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        buttons = QtWidgets.QDialogButtonBox(self)
        self.confirm_button = buttons.addButton(_('Confirm'), QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        self.confirm_button.setDefault(True)
        buttons.addButton(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Only one plausible recording is preselected; several need a choice.
        preselected = preselected_recording(self.recordings)
        for row, recording in enumerate(self.recordings):
            if recording.recording_id == preselected:
                self.table.selectRow(row)
        self._update_buttons()

    def selected_recording(self):
        rows = self.table.selectionModel().selectedRows()
        if len(rows) != 1:
            return None
        recording = self.recordings[rows[0].row()]
        return recording if recording.confirmable else None

    def _update_buttons(self, *args):
        self.confirm_button.setEnabled(self.selected_recording() is not None)

    def accept(self):
        recording = self.selected_recording()
        if recording is None:
            return
        if not self.file.confirm_recording(
            self.decision,
            recording.recording_id,
            replace_manual=self.replace_check.isChecked(),
        ):
            self.error_label.setText(_('The song changed since this window opened. Close it and scan again.'))
            self.error_label.show()
            self.confirm_button.setEnabled(False)
            return
        if self.allowed_tags is not None:
            self.file.dlmtags_tag_suggestions = {
                tag: suggestion
                for tag, suggestion in self.file.dlmtags_tag_suggestions.items()
                if tag in self.allowed_tags
            }
            if 'date' not in self.allowed_tags:
                self.file.dlmtags_date_suggestion = None
        super().accept()


def can_confirm(file):
    """Whether Confirm Results applies to the file's current review."""
    return any(recording.confirmable for recording in file.confirmable_recordings())


__all__ = ['ConfirmResultsDialog', 'can_confirm']
