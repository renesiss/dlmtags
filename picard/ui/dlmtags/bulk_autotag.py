"""Review bulk Smart Scan results before staging four supported tags."""

from collections import Counter
from dataclasses import (
    dataclass,
    field,
)
import os.path

from PyQt6 import (
    QtCore,
    QtGui,
    QtWidgets,
)

from picard import log
from picard.bulk_autotag import (
    AUTO_TAGS,
    Proposal,
    apply,
    classify,
    propose,
)
from picard.config import get_config
from picard.file import File
from picard.i18n import gettext as _
from picard.metadata import Metadata
from picard.smartgenre import SmartGenreRequest
from picard.util import (
    iter_files_from_objects,
    resolve_fs_path,
)

from picard.ui.colors import interface_colors
from picard.ui.dlmtags.confirm_results import (
    ConfirmResultsDialog,
    can_confirm,
)
from picard.ui.dlmtags.sidebar_style import sidebar_icon
from picard.ui.dlmtags.theme import DLMtagsTheme
from picard.ui.theme import (
    DARK_PALETTE_COLORS,
    apply_dark_palette_colors,
)


@dataclass
class UnavailableFile:
    filename: str
    metadata: Metadata = field(default_factory=Metadata)


@dataclass
class Result:
    file: File | UnavailableFile
    status: str
    reason: str
    proposal: Proposal | None
    original: Metadata
    decision: object | None
    row: QtWidgets.QListWidgetItem
    state: str = 'pending'


def status_text(result):
    if result.status == 'pending':
        return _('Queued')
    if result.state == 'confirmed':
        return _('Matched · Staged')
    if result.state == 'cancelled':
        return _('Skipped · Cancelled')
    if result.state == 'reviewed':
        return _('Needs Review · Reviewed manually')
    if result.state == 'no_changes':
        return _('Already Correct')
    return {
        'matched': _('Matched · Pending confirmation'),
        'ambiguous': _('Needs Review'),
        'skipped': _('Skipped'),
        'failed': _('Failed'),
    }[result.status]


def positive_color():
    """Brighten Picard's configured green for readable text on dark cards."""
    return interface_colors.get_qcolor('tagstatus_added').lighter(170)


class TrackCardDelegate(QtWidgets.QStyledItemDelegate):
    action_requested = QtCore.pyqtSignal(object, str)

    def __init__(self, view, results):
        super().__init__(view)
        self.view = view
        self.results = results
        self.confirm_icon = sidebar_icon(view, 'check-circle', positive_color())
        self.cancel_icon = sidebar_icon(view, 'x-circle', interface_colors.get_qcolor('tagstatus_removed'))

    def _result(self, index):
        if not index.isValid():
            return None
        result_index = index.data(QtCore.Qt.ItemDataRole.UserRole)
        if not isinstance(result_index, int) or not 0 <= result_index < len(self.results):
            return None
        result = self.results[result_index]
        return result if self.view.itemFromIndex(index) is result.row else None

    @staticmethod
    def _values(result, tag):
        before = result.original.getall(tag)
        after = result.proposal.changes[tag][1] if result.proposal and tag in result.proposal.changes else before
        return before, after

    def _headline(self, result):
        _old_title, title = self._values(result, 'title')
        _old_artist, artist = self._values(result, 'artist')
        candidate = result.decision.candidates[0] if result.decision and result.decision.candidates else None
        return (
            '; '.join(title)
            or (candidate.title if candidate else '')
            or os.path.splitext(os.path.basename(result.file.filename))[0],
            '; '.join(artist) or (candidate.artist if candidate else '') or _('Unknown artist'),
        )

    def _metrics(self, option, result):
        width = max(220, self.view.viewport().width() - 28)
        text_width = max(120, width - 152)
        title, artist = self._headline(result)
        title_font = QtGui.QFont(option.font)
        title_font.setBold(True)
        title_font.setPointSizeF(max(title_font.pointSizeF(), 12))
        title_height = (
            QtGui.QFontMetrics(title_font)
            .boundingRect(QtCore.QRect(0, 0, text_width, 1000), QtCore.Qt.TextFlag.TextWordWrap, title)
            .height()
        )
        artist_height = (
            QtGui.QFontMetrics(option.font)
            .boundingRect(QtCore.QRect(0, 0, text_width, 1000), QtCore.Qt.TextFlag.TextWordWrap, artist)
            .height()
        )
        return width, title_font, title_height, artist_height

    def sizeHint(self, option, index):
        result = self._result(index)
        if result is None:
            return QtCore.QSize(0, 190)
        _, _, title_height, artist_height = self._metrics(option, result)
        return QtCore.QSize(0, max(190, 184 + title_height + artist_height))

    @staticmethod
    def action_rects(rect, result):
        if result is None or result.status == 'pending':
            return {}
        top = rect.top() + 12
        right = rect.right() - 14
        actions = {
            'cancel': QtCore.QRect(right - 25, top, 25, 25),
            'confirm': QtCore.QRect(right - 56, top, 25, 25),
        }
        if result.status == 'ambiguous' and result.state == 'pending':
            actions['review'] = QtCore.QRect(right - 139, top, 76, 25)
        return actions

    def paint(self, painter, option, index):
        result = self._result(index)
        if result is None:
            return
        painter.save()
        card = option.rect.adjusted(7, 4, -7, -4)
        palette = self.view.window().palette()
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setPen(QtGui.QPen(palette.color(QtGui.QPalette.ColorRole.Mid), 1))
        painter.setBrush(QtGui.QColor(DARK_PALETTE_COLORS[QtGui.QPalette.ColorRole.Base]))
        painter.drawRoundedRect(card, 10, 10)
        left = card.left() + 14
        content_width = card.width() - 28
        _width, title_font, title_height, artist_height = self._metrics(option, result)
        title, artist = self._headline(result)
        text_width = max(120, content_width - 140)
        y = card.top() + 11
        painter.setPen(palette.color(QtGui.QPalette.ColorRole.Text))
        painter.setFont(title_font)
        painter.drawText(QtCore.QRect(left, y, text_width, title_height), QtCore.Qt.TextFlag.TextWordWrap, title)
        y += title_height + 2
        painter.setFont(option.font)
        painter.drawText(QtCore.QRect(left, y, text_width, artist_height), QtCore.Qt.TextFlag.TextWordWrap, artist)
        y += artist_height + 5
        small = QtGui.QFont(option.font)
        small.setPointSizeF(max(8, small.pointSizeF() - 1))
        painter.setFont(small)
        secondary = palette.color(QtGui.QPalette.ColorRole.PlaceholderText)
        painter.setPen(secondary)
        status = '%s · %s' % (status_text(result), result.reason)
        painter.drawText(
            left,
            y + 13,
            painter.fontMetrics().elidedText(status, QtCore.Qt.TextElideMode.ElideRight, content_width - 12),
        )
        y += 29
        score = result.proposal.score if result.proposal else BulkAutoTagDialog._score(result.decision)
        if score >= 0:
            painter.setPen(palette.color(QtGui.QPalette.ColorRole.Highlight))
            painter.drawText(
                QtCore.QRect(card.right() - 130, card.top() + 44, 112, 20),
                QtCore.Qt.AlignmentFlag.AlignRight,
                f'{score:.1%}',
            )
        actions = self.action_rects(card, result)
        confirm_mode = (
            QtGui.QIcon.Mode.Normal
            if result.status == 'matched' and result.state == 'pending' and result.proposal and result.proposal.changes
            else QtGui.QIcon.Mode.Disabled
        )
        cancel_mode = (
            QtGui.QIcon.Mode.Normal
            if result.state == 'pending' and result.status in {'matched', 'ambiguous'}
            else QtGui.QIcon.Mode.Disabled
        )
        if actions:
            self.confirm_icon.paint(painter, actions['confirm'], QtCore.Qt.AlignmentFlag.AlignCenter, confirm_mode)
            self.cancel_icon.paint(painter, actions['cancel'], QtCore.Qt.AlignmentFlag.AlignCenter, cancel_mode)
        if 'review' in actions:
            review_rect = actions['review']
            painter.setPen(QtGui.QPen(palette.color(QtGui.QPalette.ColorRole.Highlight), 1))
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(review_rect, 5, 5)
            painter.drawText(review_rect, QtCore.Qt.AlignmentFlag.AlignCenter, _('Review'))
        label_width = 68
        gap = 10
        column_width = max(80, (content_width - label_width - gap * 2) // 2)
        original_x = left + label_width + gap
        proposed_x = original_x + column_width + gap
        painter.setPen(secondary)
        painter.drawText(original_x, y + 12, _('Original'))
        painter.drawText(proposed_x, y + 12, _('Proposed'))
        y += 21
        labels = {'title': _('Title'), 'artist': _('Artist'), 'date': _('Year'), 'genre': _('Genre')}
        for tag in AUTO_TAGS:
            before, after = self._values(result, tag)
            painter.setPen(secondary)
            painter.drawText(left, y + 15, labels[tag])
            painter.setPen(palette.color(QtGui.QPalette.ColorRole.Text))
            original = '; '.join(before)
            proposed = '; '.join(after)
            painter.drawText(
                original_x,
                y + 15,
                painter.fontMetrics().elidedText(original, QtCore.Qt.TextElideMode.ElideRight, column_width - 7),
            )
            if before != after:
                highlight = QtGui.QColor(positive_color())
                highlight.setAlpha(32)
                painter.setPen(QtCore.Qt.PenStyle.NoPen)
                painter.setBrush(highlight)
                painter.drawRoundedRect(QtCore.QRect(proposed_x - 4, y, column_width, 21), 4, 4)
                painter.setPen(positive_color())
            painter.drawText(
                proposed_x,
                y + 15,
                painter.fontMetrics().elidedText(proposed, QtCore.Qt.TextElideMode.ElideRight, column_width - 7),
            )
            y += 24
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if event.type() != QtCore.QEvent.Type.MouseButtonRelease:
            return False
        result = self._result(index)
        if result is None or result.status == 'pending':
            return False
        card = option.rect.adjusted(7, 4, -7, -4)
        for action, rect in self.action_rects(card, result).items():
            if rect.contains(event.position().toPoint()):
                if action == 'confirm' and not (
                    result.status == 'matched'
                    and result.state == 'pending'
                    and result.proposal
                    and result.proposal.changes
                ):
                    return False
                if action == 'cancel' and not (result.state == 'pending' and result.status in {'matched', 'ambiguous'}):
                    return False
                self.action_requested.emit(result, action)
                return True
        return False

    def helpEvent(self, event, view, option, index):
        result = self._result(index)
        if result is None:
            return False
        card = option.rect.adjusted(7, 4, -7, -4)
        for action, rect in self.action_rects(card, result).items():
            if rect.contains(event.pos()):
                QtWidgets.QToolTip.showText(
                    event.globalPos(),
                    {
                        'confirm': _('Confirm'),
                        'cancel': _('Cancel'),
                        'review': _('Review'),
                    }[action],
                    view,
                )
                return True
        QtWidgets.QToolTip.showText(event.globalPos(), result.file.filename, view)
        return True


class BulkAutoTagDialog(QtWidgets.QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.main_window = window
        self.setWindowTitle(_('Bulk Auto-Tag'))
        self.resize(980, 640)
        self.setMinimumWidth(620)
        dark_palette = QtGui.QPalette(self.palette())
        apply_dark_palette_colors(dark_palette)
        self.setPalette(dark_palette)
        self._theme = DLMtagsTheme(self)
        self.files: list[File | str] = []
        self.results: list[Result] = []
        self._pending_results: dict[str, Result] = {}
        self.processed_count = 0
        self.active = None
        self.cancelled = False
        self.total = 0
        self.selected_count = 0
        self.resolved_count = 0
        self._loading_paths = set()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        background = dark_palette.color(QtGui.QPalette.ColorRole.Window).name()
        foreground = dark_palette.color(QtGui.QPalette.ColorRole.Text).name()
        self.setStyleSheet(
            f'QDialog {{ background: {background}; color: {foreground}; }} '
            f'QLabel {{ color: {foreground}; }} '
            'QListWidget { background: transparent; border: none; }'
        )
        top = QtWidgets.QHBoxLayout()
        self.progress = QtWidgets.QProgressBar(self)
        self.progress.setMaximumHeight(12)
        top.addWidget(self.progress, 1)
        self.stop_button = QtWidgets.QPushButton(_('Stop scan'), self)
        self.stop_button.clicked.connect(self.cancel_scan)
        top.addWidget(self.stop_button)
        layout.addLayout(top)
        self.summary = QtWidgets.QLabel(self)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.preview = QtWidgets.QListWidget(self)
        self.preview.setObjectName('bulk_track_cards')
        self.preview.setSpacing(3)
        self.preview.setResizeMode(QtWidgets.QListView.ResizeMode.Adjust)
        self.preview.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.preview.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.cards = TrackCardDelegate(self.preview, self.results)
        self.cards.action_requested.connect(self._card_action)
        self.preview.setItemDelegate(self.cards)
        layout.addWidget(self.preview, 1)
        actions = QtWidgets.QHBoxLayout()
        actions.addStretch(1)
        self.confirm_all_button = self._icon_button('double-check', _('Confirm All'), 'bulk_confirm_all')
        self.confirm_all_button.clicked.connect(self.confirm_all)
        actions.addWidget(self.confirm_all_button)
        self.cancel_all_button = self._icon_button('x', _('Cancel All'), 'bulk_cancel_all')
        self.cancel_all_button.clicked.connect(self.cancel_all)
        actions.addWidget(self.cancel_all_button)
        close = QtWidgets.QPushButton(_('Close'), self)
        close.clicked.connect(self.reject)
        actions.addWidget(close)
        layout.addLayout(actions)
        self._summary()
        self._update_buttons()
        QtCore.QTimer.singleShot(0, self.start)

    def _icon_button(self, symbol, label, name, parent=None):
        button = QtWidgets.QToolButton(parent or self)
        button.setObjectName(name)
        button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setText('')
        button.setToolTip(label)
        button.setAccessibleName(label)
        tint = (
            positive_color()
            if symbol in {'check-circle', 'double-check'}
            else interface_colors.get_qcolor('tagstatus_removed')
        )
        button.setIcon(sidebar_icon(button, symbol, tint))
        button.setIconSize(QtCore.QSize(22, 22))
        return button

    def start(self):
        self.files.clear()
        self._loading_paths.clear()
        sidebar = getattr(self.main_window, 'file_sidebar', None)
        if sidebar is not None:
            paths = list(dict.fromkeys(os.path.normpath(resolve_fs_path(path)) for path in sidebar._selected_paths()))
            self.selected_count = len(paths)
            self.files.extend(paths)
            self._loading_paths = {path for path in paths if path not in self.main_window.tagger.files}
            if paths:
                self.main_window.tagger.add_paths(paths, metadata_only=True, on_loaded=self._file_loaded)
            self._loading_paths.intersection_update(self.main_window.tagger.files)
            self.resolved_count = sum(isinstance(self.main_window.tagger.files.get(path), File) for path in paths)
        else:
            selected_objects = getattr(self.main_window, 'selected_objects', ())
            selected_files = list(iter_files_from_objects(selected_objects))
            self.selected_count = len(selected_objects)
            source = selected_files if selected_objects else self.main_window.tagger.files.values()
            self.files = list(dict.fromkeys(f for f in source if isinstance(f, File)))
            self.resolved_count = len(self.files)
        self.total = len(self.files)
        log.debug(
            'Bulk Auto-Tag selection: UI objects=%d, resolved files=%d, queued=%d',
            self.selected_count,
            self.resolved_count,
            self.total,
        )
        self.progress.setRange(0, self.total)
        self.progress.setValue(0)
        for entry in self.files:
            self._add_pending(entry)
        self.stop_button.setEnabled(bool(self.files))
        self._summary()
        QtCore.QTimer.singleShot(0, self._next)

    def _file_loaded(self, file):
        if file is not None:
            self._loading_paths.discard(os.path.normpath(resolve_fs_path(file.filename)))

    def _file_for_entry(self, entry):
        if isinstance(entry, str):
            return self.main_window.tagger.files.get(entry) or UnavailableFile(entry)
        return entry

    @staticmethod
    def _result_key(file):
        return os.path.normpath(resolve_fs_path(file if isinstance(file, str) else file.filename))

    def _add_pending(self, entry):
        file = self._file_for_entry(entry)
        row = QtWidgets.QListWidgetItem()
        result = Result(file, 'pending', _('Waiting to scan'), None, Metadata(file.metadata), None, row)
        self.results.append(result)
        row.setData(QtCore.Qt.ItemDataRole.UserRole, len(self.results) - 1)
        row.setData(
            QtCore.Qt.ItemDataRole.AccessibleTextRole,
            '%s — %s' % (self.cards._headline(result)[0], status_text(result)),
        )
        row.setToolTip(file.filename)
        self.preview.addItem(row)
        self._pending_results[self._result_key(entry)] = result

    def _next(self):
        if self.cancelled or not self.files:
            self.active = None
            self.stop_button.setEnabled(False)
            self._update_buttons()
            return
        entry = self.files.pop(0)
        if isinstance(entry, str) and entry in self._loading_paths:
            file = self.main_window.tagger.files.get(entry)
            if file is None or getattr(file, 'has_error', lambda: False)():
                self._loading_paths.discard(entry)
            else:
                self.files.insert(0, entry)
                QtCore.QTimer.singleShot(50, self._next)
                return
        file = self._file_for_entry(entry)
        original = Metadata(file.metadata)
        if isinstance(file, UnavailableFile):
            self._add(
                file, 'skipped', _('File was not loaded or was excluded by Picard settings'), None, original, None
            )
            QtCore.QTimer.singleShot(0, self._next)
            return
        if getattr(file, 'has_error', lambda: False)():
            self._add(file, 'failed', _('Could not load audio metadata'), None, original, None)
            QtCore.QTimer.singleShot(0, self._next)
            return
        self.active = file
        if not file.start_smart_scan(bulk_callback=lambda f, decision: self._finished(f, decision, original)):
            self._add(file, 'skipped', _('File is busy'), None, original, None)
            self.active = None
            QtCore.QTimer.singleShot(0, self._next)

    def _finished(self, file, decision, original):
        if self.cancelled or file is not self.active:
            return
        status, reason = classify(decision)
        settings = get_config().setting
        if (
            status == 'matched'
            and not any(value.strip() for value in original.getall('genre'))
            and 'genre' not in original.deleted_tags
            and settings['use_genres']
            and settings['max_genres'] > 0
            and isinstance(file, QtCore.QObject)
        ):
            # The same bounded provider chain used by single Smart Scan.
            # Keep this file active until its async result (including timeout).
            request = SmartGenreRequest(
                file,
                decision,
                on_result=lambda values, source, result_status: self._genre_finished(
                    file, decision, original, status, reason, values, result_status
                ),
            )
            file._smart_genre_request = request
            request.start()
            return
        self._complete_result(file, decision, original, status, reason)

    def _genre_finished(self, file, decision, original, status, reason, values, result_status):
        if self.cancelled or file is not self.active:
            return
        self._complete_result(
            file,
            decision,
            original,
            status,
            reason,
            genre_values=values if result_status == 'ready' else (),
        )

    def _complete_result(self, file, decision, original, status, reason, *, genre_values=()):
        proposal = None
        if status == 'matched':
            try:
                proposal = (
                    propose(original, decision, genre_values=genre_values)
                    if genre_values
                    else propose(original, decision)
                )
            except (KeyError, TypeError, ValueError):
                log.error('Bulk Auto-Tag: cannot prepare preview for %s', file.filename, exc_info=True)
                status, reason = 'failed', _('Could not prepare metadata preview')
        self._add(file, status, reason, proposal, original, decision)
        self.active = None
        QtCore.QTimer.singleShot(0, self._next)

    @staticmethod
    def _score(decision):
        if decision is None:
            return -1.0
        if decision.match is not None:
            return decision.match.similarity
        return decision.candidates[0].similarity if decision.candidates else -1.0

    def _insert_sorted(self, item, score):
        index = 0
        while index < self.preview.count():
            existing_item = self.preview.item(index)
            if existing_item is None:
                break
            existing_index = existing_item.data(QtCore.Qt.ItemDataRole.UserRole)
            existing = self.results[existing_index]
            if self._score(existing.decision) < score:
                break
            index += 1
        self.preview.insertItem(index, item)

    def _add(self, file, status, reason, proposal, original, decision):
        score = self._score(decision)
        result = self._pending_results.pop(self._result_key(file), None)
        if result is None:
            row = QtWidgets.QListWidgetItem()
            result = Result(file, status, reason, proposal, original, decision, row)
            self.results.append(result)
            row.setData(QtCore.Qt.ItemDataRole.UserRole, len(self.results) - 1)
        else:
            row = result.row
            self.preview.takeItem(self.preview.row(row))
            result.file = file
            result.status = status
            result.reason = reason
            result.proposal = proposal
            result.original = original
            result.decision = decision
        if status == 'matched' and proposal is not None and not proposal.changes:
            result.state = 'no_changes'
        row.setData(
            QtCore.Qt.ItemDataRole.AccessibleTextRole,
            '%s — %s — %s'
            % (
                self.cards._headline(result)[0],
                self.cards._headline(result)[1],
                status_text(result),
            ),
        )
        row.setToolTip(file.filename)
        self._insert_sorted(row, score)
        self.processed_count += 1
        self.progress.setValue(self.processed_count)
        self._summary()
        self._update_buttons()

    def _refresh_result(self, result):
        result.row.setData(
            QtCore.Qt.ItemDataRole.AccessibleTextRole,
            '%s — %s'
            % (
                self.cards._headline(result)[0],
                status_text(result),
            ),
        )
        viewport = self.preview.viewport()
        if viewport is not None:
            viewport.update(self.preview.visualItemRect(result.row))
        self._summary()
        self._update_buttons()

    def confirm(self, result):
        if result.state != 'pending' or result.status != 'matched' or not result.proposal:
            return False
        if not apply(result.file, result.proposal, result.original):
            result.status = 'failed'
            result.reason = _('Track changed since preview; no tags were staged')
            self._refresh_result(result)
            return False
        result.state = 'confirmed'
        self._refresh_result(result)
        return True

    def cancel(self, result):
        if result.state != 'pending' or result.status not in {'matched', 'ambiguous'}:
            return False
        result.state = 'cancelled'
        self._refresh_result(result)
        return True

    def confirm_all(self):
        for result in self.results:
            if result.status == 'matched' and result.state == 'pending' and result.proposal and result.proposal.changes:
                self.confirm(result)

    def cancel_all(self):
        for result in self.results:
            self.cancel(result)

    def _summary(self):
        counts = Counter(
            'skipped'
            if result.state == 'cancelled'
            else 'already_correct'
            if result.state == 'no_changes'
            else result.status
            for result in self.results
            if result.status != 'pending'
        )
        staged = sum(result.state == 'confirmed' for result in self.results)
        self.summary.setText(
            _(
                'Selected UI objects: %(selected)d · Resolved unique files: %(resolved)d · Queued: %(total)d · '
                'Processed: %(done)d · Displayed: %(displayed)d\n'
                'Matched: %(matched)d, Already Correct: %(already_correct)d, Needs Review: %(ambiguous)d, '
                'Skipped: %(skipped)d, Failed: %(failed)d, Staged: %(staged)d'
            )
            % {
                'selected': self.selected_count,
                'resolved': self.resolved_count,
                'done': self.processed_count,
                'total': self.total,
                'displayed': self.preview.count(),
                'staged': staged,
                **{key: counts[key] for key in ('matched', 'already_correct', 'ambiguous', 'skipped', 'failed')},
            }
        )

    def cancel_scan(self):
        self.cancelled = True
        if self.active is not None:
            active = self.active
            active.cancel_smart_scan()
            if isinstance(active, QtCore.QObject):
                active.cancel_smart_genre()
            self._add(active, 'skipped', _('Scan stopped'), None, Metadata(active.metadata), None)
            self.active = None
        for file in self.files:
            file = self._file_for_entry(file)
            self._add(file, 'skipped', _('Not scanned'), None, Metadata(file.metadata), None)
        self.files.clear()
        self._next()

    def _update_buttons(self):
        finished = self.active is None and not self.files
        self.confirm_all_button.setEnabled(
            finished
            and any(
                r.status == 'matched' and r.state == 'pending' and r.proposal and r.proposal.changes
                for r in self.results
            )
        )
        self.cancel_all_button.setEnabled(
            finished and any(r.state == 'pending' and r.status in {'matched', 'ambiguous'} for r in self.results)
        )

    def _card_action(self, result, action):
        if action == 'confirm':
            self.confirm(result)
        elif action == 'cancel':
            self.cancel(result)
        elif action == 'review':
            self.review(result)

    def review(self, result):
        if result.status != 'ambiguous' or result.state != 'pending' or self.active is not None or self.files:
            return
        file = result.file
        if file.smart_scan_decision is not result.decision:
            QtWidgets.QMessageBox.information(
                self, _('Needs Review'), _('This result is no longer current. Scan the track again to review it.')
            )
            return
        if not can_confirm(file):
            self.main_window.panel.select_object(file)
            QtWidgets.QMessageBox.information(
                self,
                _('Needs Review'),
                _(
                    'No recording can be confirmed from this result. The track is selected in the editor; '
                    'close this popup, then use Scan or Lookup to identify it manually.'
                ),
            )
            return
        dialog = ConfirmResultsDialog(file, self, allowed_tags={'title', 'artist', 'date', 'genre'})
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            result.state = 'reviewed'
            self._refresh_result(result)

    def reject(self):
        if self.active is not None or self.files:
            self.cancel_scan()
        super().reject()
