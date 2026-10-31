# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""Review a Needs Review recording and explicitly stage its four primary tags."""

from PyQt6 import (
    QtCore,
    QtGui,
    QtWidgets,
)

from picard.i18n import gettext as _
from picard.smartscan import (
    REASON_LABELS,
    decide_year,
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
        self.allowed_tags = frozenset(allowed_tags) if allowed_tags is not None else frozenset(
            {'title', 'artist', 'date', 'genre'}
        )
        self.decision = file.smart_scan_decision
        self.recordings = file.confirmable_recordings()
        self._confirmed = False
        self._confirmed_decision = None
        self._original_values = {
            tag: tuple(file.metadata.getall(tag)) for tag in ('title', 'artist', 'date', 'genre')
        }
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

        review = QtWidgets.QWidget(self)
        grid = QtWidgets.QGridLayout(review)
        for column, label in enumerate((_('Field'), _('Current'), _('Proposed'), _('Approve'))):
            grid.addWidget(QtWidgets.QLabel(label, review), 0, column)
        names = {'title': _('Title'), 'artist': _('Artist'), 'date': _('Year'), 'genre': _('Genre')}
        self.current_labels = {}
        self.proposed_labels = {}
        self.approve_checks = {}
        for row, tag in enumerate(('title', 'artist', 'date', 'genre'), 1):
            grid.addWidget(QtWidgets.QLabel(names[tag], review), row, 0)
            current = self.current_labels[tag] = QtWidgets.QLabel('; '.join(self._original_values[tag]) or _('Empty'), review)
            proposed = self.proposed_labels[tag] = QtWidgets.QLabel(_('Unavailable'), review)
            check = self.approve_checks[tag] = QtWidgets.QCheckBox(review)
            check.setAccessibleName(_('Approve %s') % names[tag])
            current.setWordWrap(True)
            proposed.setWordWrap(True)
            grid.addWidget(current, row, 1)
            grid.addWidget(proposed, row, 2)
            grid.addWidget(check, row, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        layout.addWidget(review)

        self.note = QtWidgets.QLabel(
            _(
                'Review each current and proposed value. Confirming stages only approved Artist and Title; '
                'then Year and Genre can be approved separately. Nothing is saved to the file.'
            ),
            self,
        )
        self.note.setWordWrap(True)
        self.note.setProperty('dlmtagsHint', True)
        layout.addWidget(self.note)
        self.error_label = QtWidgets.QLabel(self)
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        buttons = QtWidgets.QDialogButtonBox(self)
        self.confirm_button = buttons.addButton(_('Confirm'), QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        self.confirm_button.setDefault(True)
        self.cancel_button = buttons.addButton(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setInterval(100)
        self._refresh_timer.timeout.connect(self._refresh_suggestions)

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
        recording = self.selected_recording()
        candidate = next(
            (item for item in self.decision.candidates if recording and item.recording_id == recording.recording_id),
            None,
        )
        year = ''
        if candidate and candidate.node:
            year_choice = decide_year(candidate.node, self._original_values['date'])
            year = year_choice.automatic or year_choice.suggestion
        proposals = {
            'title': recording.title if recording else '',
            'artist': recording.artist if recording else '',
            'date': year,
        }
        for tag in ('title', 'artist', 'date'):
            value = proposals[tag]
            self.proposed_labels[tag].setText(value or _('Unavailable'))
            available = bool(value and tag in self.allowed_tags and (value,) != self._original_values[tag])
            self.approve_checks[tag].setEnabled(available)
            self.approve_checks[tag].setChecked(available and tag in {'title', 'artist'} and not self._original_values[tag])
        self.proposed_labels['genre'].setText(_('After confirmation') if recording else _('Unavailable'))
        self.approve_checks['genre'].setEnabled(False)
        self.approve_checks['genre'].setChecked(False)

    def _refresh_suggestions(self):
        if not self._confirmed:
            return
        if self.file.smart_scan_decision is not self._confirmed_decision:
            self.error_label.setText(_('The confirmed song changed. Close this window and review again.'))
            self.error_label.show()
            self.confirm_button.setEnabled(False)
            self._refresh_timer.stop()
            return
        suggestion = self.file.dlmtags_tag_suggestions.get('genre')
        if suggestion is not None and suggestion.recording_id == self._confirmed_decision.recording_id:
            self.proposed_labels['genre'].setText(suggestion.value)
            self.approve_checks['genre'].setEnabled('genre' in self.allowed_tags)
            self._refresh_timer.stop()
        elif self.file.smart_genre_pending:
            self.proposed_labels['genre'].setText(_('Looking up Genre…'))
        else:
            self.proposed_labels['genre'].setText(_('Unavailable'))
            self._refresh_timer.stop()

    def accept(self):
        if self._confirmed:
            approved = {tag for tag in ('date', 'genre') if self.approve_checks[tag].isChecked()}
            if approved and not self.file.apply_confirmed_suggestions(
                self._confirmed_decision,
                approved,
                {tag: self._original_values[tag] for tag in approved},
            ):
                self.error_label.setText(_('A value changed since review. Close this window and review again.'))
                self.error_label.show()
                return
            self._refresh_timer.stop()
            super().accept()
            return
        recording = self.selected_recording()
        if recording is None:
            return
        approved_identity = {
            tag for tag in ('artist', 'title') if self.approve_checks[tag].isChecked()
        }
        if (
            approved_identity & set(self.file.manual_identity_edits())
            and not self.replace_check.isChecked()
        ):
            self.error_label.setText(_('Select “Replace my edits” to approve replacing a manual value.'))
            self.error_label.show()
            return
        if not self.file.confirm_recording(
            self.decision,
            recording.recording_id,
            replace_manual=self.replace_check.isChecked(),
            approved_identity_tags=approved_identity,
        ):
            self.error_label.setText(_('The song changed since this window opened. Close it and scan again.'))
            self.error_label.show()
            self.confirm_button.setEnabled(False)
            return
        self._confirmed = True
        self._confirmed_decision = self.file.smart_scan_decision
        self.table.setEnabled(False)
        self.replace_check.setEnabled(False)
        for tag in ('artist', 'title'):
            self.approve_checks[tag].setEnabled(False)
        self.confirm_button.setText(_('Apply reviewed Year/Genre and close'))
        self.cancel_button.setText(_('Close without Year/Genre'))
        self.note.setText(_('Year and Genre are staged only if you approve them here. Save the file separately.'))
        self._refresh_suggestions()
        if self.file.smart_genre_pending:
            self._refresh_timer.start()

    def reject(self):
        self._refresh_timer.stop()
        if self._confirmed:
            # Artist/Title were explicitly confirmed in the first step.
            super().accept()
        else:
            super().reject()


def can_confirm(file):
    """Whether Confirm Results applies to the file's current review."""
    return any(recording.confirmable for recording in file.confirmable_recordings())


__all__ = ['ConfirmResultsDialog', 'can_confirm']
