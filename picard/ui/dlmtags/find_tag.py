# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""Find Tag: one field, every configured provider, real returned values only."""

from html import escape
from urllib.parse import urlencode

from PyQt6 import (
    QtCore,
    QtGui,
    QtWidgets,
)

from picard.i18n import (
    gettext as _,
    ngettext,
)
from picard.tags import display_tag_name
from picard.tagsearch import (
    PROVIDERS,
    STATUS_LABELS,
    TagSearchRequest,
    filter_candidates,
)
from picard.util import webbrowser2

from picard.ui.dlmtags.components.card import Card
from picard.ui.dlmtags.components.styles import style_component


CANDIDATE_ROLE = QtCore.Qt.ItemDataRole.UserRole
# Optional (text, QColor | None) status badge drawn at the card's top right.
BADGE_ROLE = QtCore.Qt.ItemDataRole.UserRole + 1
MAX_EVIDENCE_PER_PROVIDER = 12

DIALOG_STYLE = """
QLabel[findTagRole="heading"] {
    font-size: 20px; font-weight: 700; letter-spacing: 1px; color: palette(text);
}
QLabel[findTagRole="song"] { font-size: 13px; color: palette(placeholder-text); }
QLabel[findTagRole="provider"] {
    background: palette(alternate-base); border-radius: 8px;
    padding: 4px 10px; font-size: 12px; color: palette(placeholder-text);
}
QLabel[findTagRole="detailValue"] { font-size: 18px; font-weight: 600; color: palette(text); }
QLabel[findTagRole="detailLine"] { font-size: 12px; color: palette(placeholder-text); }
QLabel[findTagRole="evidence"] { font-size: 12px; color: palette(text); }
QLabel[findTagRole="status"] { font-size: 12px; color: palette(placeholder-text); }
QLabel[findTagRole="empty"] { font-size: 13px; color: palette(placeholder-text); padding: 24px; }
"""


def _font(base, size, weight=QtGui.QFont.Weight.Normal):
    font = QtGui.QFont(base)
    font.setPixelSize(size)
    font.setWeight(weight)
    return font


class CandidateDelegate(QtWidgets.QStyledItemDelegate):
    """Value first and prominent; kind/year and providers in smaller, quieter text."""

    PADDING = 10

    def _fonts(self, option):
        return (
            _font(option.font, 15, QtGui.QFont.Weight.DemiBold),
            _font(option.font, 12),
            _font(option.font, 11),
        )

    def sizeHint(self, option, index):
        heights = sum(QtGui.QFontMetrics(font).height() for font in self._fonts(option))
        return QtCore.QSize(option.rect.width(), heights + 2 * self.PADDING + 6)

    def paint(self, painter, option, index):
        candidate = index.data(CANDIDATE_ROLE)
        if candidate is None:
            return
        palette = option.palette
        selected = bool(option.state & QtWidgets.QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QtWidgets.QStyle.StateFlag.State_MouseOver)
        painter.save()
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        rect = QtCore.QRectF(option.rect).adjusted(4, 3, -4, -3)
        # The dark theme greys out inactive highlights; keep the accent visible.
        accent = palette.color(QtGui.QPalette.ColorGroup.Active, QtGui.QPalette.ColorRole.Highlight)
        if selected:
            fill = QtGui.QColor(accent)
            fill.setAlpha(40)
            painter.setBrush(fill)
            painter.setPen(QtGui.QPen(accent, 1.5))
        elif hovered:
            painter.setBrush(palette.color(QtGui.QPalette.ColorRole.Base))
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
        else:
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 8, 8)
        text_rect = rect.adjusted(self.PADDING + 2, self.PADDING - 2, -self.PADDING, -self.PADDING)
        badge_width = self._paint_badge(painter, option, index.data(BADGE_ROLE), text_rect)
        lines = (
            ('; '.join(candidate.values), QtGui.QPalette.ColorRole.Text),
            (candidate.detail, QtGui.QPalette.ColorRole.PlaceholderText),
            (candidate.source, QtGui.QPalette.ColorRole.PlaceholderText),
        )
        y = text_rect.top()
        for line, ((text, role), font) in enumerate(zip(lines, self._fonts(option), strict=False)):
            metrics = QtGui.QFontMetrics(font)
            width = text_rect.width() - (badge_width if line == 0 else 0)
            painter.setFont(font)
            painter.setPen(palette.color(role))
            elided = metrics.elidedText(text, QtCore.Qt.TextElideMode.ElideRight, int(width))
            painter.drawText(
                QtCore.QRectF(text_rect.left(), y, width, metrics.height()),
                int(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter),
                elided,
            )
            y += metrics.height() + 3
        painter.restore()

    def _paint_badge(self, painter, option, badge, text_rect):
        """Draw a text badge (never colour alone) and return the width it takes."""
        if not badge:
            return 0
        text, color = badge
        font = _font(option.font, 11, QtGui.QFont.Weight.DemiBold)
        metrics = QtGui.QFontMetrics(font)
        if color is None:
            color = option.palette.color(QtGui.QPalette.ColorRole.PlaceholderText)
        width = metrics.horizontalAdvance(text) + 16
        rect = QtCore.QRectF(text_rect.right() - width, text_rect.top(), width, metrics.height() + 4)
        fill = QtGui.QColor(color)
        fill.setAlpha(30)
        painter.setBrush(fill)
        painter.setPen(QtGui.QPen(color, 1))
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        painter.setFont(font)
        painter.drawText(rect, int(QtCore.Qt.AlignmentFlag.AlignCenter), text)
        return width + 8


class FindTagDialog(QtWidgets.QDialog):
    def __init__(self, file, tag, parent=None):
        super().__init__(parent)
        self.tag = tag
        name = display_tag_name(tag)
        self.setWindowTitle(_('Find Tag — %s') % name)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setSizeGripEnabled(True)
        self.setMinimumSize(750, 500)
        self.resize(900, 620)
        self.setStyleSheet(DIALOG_STYLE)
        self.request = TagSearchRequest(file, tag)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        self.heading = self._label((_('Find %s') % name).upper(), 'heading')
        layout.addWidget(self.heading)
        self.song = self._label(_('Song: %s') % (self.request.song or _('Unknown')), 'song')
        self.song.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.song)

        providers = QtWidgets.QHBoxLayout()
        providers.setSpacing(8)
        self.provider_labels = {}
        for provider in PROVIDERS:
            label = self._label('', 'provider')
            label.setTextFormat(QtCore.Qt.TextFormat.RichText)
            self.provider_labels[provider] = label
            providers.addWidget(label)
        providers.addStretch(1)
        layout.addLayout(providers)

        tools = QtWidgets.QHBoxLayout()
        self.filter_edit = QtWidgets.QLineEdit(self)
        self.filter_edit.setPlaceholderText(_('Search within results'))
        self.filter_edit.setClearButtonEnabled(True)
        style_component(self.filter_edit, 'search')
        self.filter_edit.textChanged.connect(self._refresh_list)
        tools.addWidget(self.filter_edit, 1)
        self.source_filter = QtWidgets.QComboBox(self)
        self.source_filter.addItem(_('All sources'), '')
        for provider in PROVIDERS:
            self.source_filter.addItem(provider, provider)
        self.source_filter.setAccessibleName(_('Source filter'))
        self.source_filter.currentIndexChanged.connect(self._refresh_list)
        tools.addWidget(self.source_filter)
        layout.addLayout(tools)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal, self)
        splitter.setChildrenCollapsible(False)
        self.list_stack = QtWidgets.QStackedWidget(splitter)
        self.values = QtWidgets.QListWidget(self.list_stack)
        self.values.setItemDelegate(CandidateDelegate(self.values))
        self.values.setMouseTracking(True)
        self.values.setUniformItemSizes(True)
        self.values.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.values.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.MultiSelection
            if tag == 'genre'
            else QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )
        style_component(self.values, 'scrollArea')
        self.values.itemSelectionChanged.connect(self._selection_changed)
        self.values.itemDoubleClicked.connect(lambda item: self.tag != 'genre' and self._apply())
        self.empty_label = self._label('', 'empty')
        self.empty_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.list_stack.addWidget(self.values)
        self.list_stack.addWidget(self.empty_label)

        self.detail_card = Card(splitter)
        self.detail_value = self._label('', 'detailValue')
        self.detail_value.setWordWrap(True)
        self.detail_value.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail_line = self._label('', 'detailLine')
        self.detail_line.setWordWrap(True)
        self.evidence = self._label('', 'evidence')
        self.evidence.setWordWrap(True)
        self.evidence.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.evidence.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        # Links open only when the user explicitly clicks one.
        self.evidence.setOpenExternalLinks(True)
        self.evidence.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.LinksAccessibleByMouse | QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        evidence_scroll = QtWidgets.QScrollArea(self.detail_card)
        evidence_scroll.setWidgetResizable(True)
        evidence_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        evidence_scroll.setWidget(self.evidence)
        evidence_scroll.viewport().setAutoFillBackground(False)
        self.evidence.setAutoFillBackground(False)
        self.detail_card.content_layout.setContentsMargins(16, 14, 16, 14)
        self.detail_card.content_layout.addWidget(self.detail_value)
        self.detail_card.content_layout.addWidget(self.detail_line)
        self.detail_card.content_layout.addSpacing(6)
        self.detail_card.content_layout.addWidget(evidence_scroll, 1)
        splitter.addWidget(self.list_stack)
        splitter.addWidget(self.detail_card)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        self.progress = QtWidgets.QProgressBar(self)
        self.progress.setObjectName('dlmtagsSearchProgress')
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        style_component(self.progress, 'progress')
        layout.addWidget(self.progress)

        footer = QtWidgets.QHBoxLayout()
        self.load_more_button = QtWidgets.QPushButton(_('Load more results'), self)
        self.load_more_button.clicked.connect(self._load_more)
        footer.addWidget(self.load_more_button)
        self.internet_button = QtWidgets.QPushButton(_('Search Internet'), self)
        self.internet_button.setAutoDefault(False)
        self.internet_button.setToolTip(
            _('Open a browser search. Copy what you find, close this dialog, and paste into New Tags. Save manually.')
        )
        self.internet_button.hide()
        self.internet_button.clicked.connect(self._search_internet)
        footer.addWidget(self.internet_button)
        self.status = self._label(_('Searching…'), 'status')
        self.status.setWordWrap(True)
        footer.addWidget(self.status, 1)
        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Apply | QtWidgets.QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.apply_button = self.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Apply)
        self.apply_button.setEnabled(False)
        self.apply_button.setDefault(True)
        self.apply_button.clicked.connect(self._apply)
        self.buttons.rejected.connect(self.reject)
        footer.addWidget(self.buttons)
        layout.addLayout(footer)

        self.request.updated.connect(self._request_updated)
        self._elapsed = QtCore.QElapsedTimer()
        self._clock = QtCore.QTimer(self)
        self._clock.setInterval(250)
        self._clock.timeout.connect(self._update_status)
        self.finished.connect(self._closed)
        self._refresh_providers()
        self._refresh_list()

    def _label(self, text, role):
        label = QtWidgets.QLabel(text, self)
        label.setProperty('findTagRole', role)
        return label

    def start(self):
        self._elapsed.start()
        self._clock.start()
        self.request.start()

    # Presentation

    def _request_updated(self):
        self._refresh_providers()
        self._refresh_list()

    def _refresh_providers(self):
        for name, label in self.provider_labels.items():
            provider = self.request.providers.get(name)
            state = provider.state if provider is not None else 'new'
            if state == 'ready':
                count = sum(name in c.providers for c in self.request.candidates)
                text = ngettext('%d result', '%d results', count) % count
                if provider.has_more:
                    text += ' · ' + _('more available')
            else:
                text = _(STATUS_LABELS[state])
            label.setText('<b>%s</b> · %s' % (escape(name), escape(text)))
            label.setToolTip(provider.reason if provider is not None and provider.reason else text)
        self._update_status()

    def _visible_candidates(self):
        return filter_candidates(
            self.request.candidates,
            self.filter_edit.text(),
            self.source_filter.currentData() or '',
        )

    def _selected(self):
        return [item.data(CANDIDATE_ROLE) for item in self.values.selectedItems()]

    def _refresh_list(self):
        selected = self._selected()
        visible = self._visible_candidates()
        with QtCore.QSignalBlocker(self.values):
            self.values.clear()
            for candidate in visible:
                item = QtWidgets.QListWidgetItem()
                item.setData(CANDIDATE_ROLE, candidate)
                item.setData(
                    QtCore.Qt.ItemDataRole.AccessibleTextRole,
                    '%s, %s, %s'
                    % (
                        '; '.join(candidate.values),
                        candidate.detail,
                        candidate.source,
                    ),
                )
                item.setToolTip('; '.join(candidate.values))
                self.values.addItem(item)
                if any(candidate is c for c in selected):
                    item.setSelected(True)
        if visible:
            self.list_stack.setCurrentWidget(self.values)
        else:
            if self.request.candidates:
                text = _('No results match the current search or source filter.')
            elif self.request.active or self.request.state == 'new':
                text = _('Searching all available providers…')
            else:
                text = self.request.message
            self.empty_label.setText(text)
            self.list_stack.setCurrentWidget(self.empty_label)
        self._selection_changed()

    def _update_status(self):
        active = self.request.active
        self.progress.setVisible(active)
        if active:
            seconds = self._elapsed.elapsed() // 1000 if self._elapsed.isValid() else 0
            minutes, seconds = divmod(seconds, 60)
            self.status.setText(_('Searching… %s') % f'{minutes:02d}:{seconds:02d}')
        else:
            self._clock.stop()
            message = self.request.message
            if self.request.candidates and not self.request.has_more and self.request.state == 'ready':
                message += ' ' + _('No further pages are available from these providers.')
            self.status.setText(message)
        self.load_more_button.setVisible(self.request.has_more and not active)
        self.internet_button.setVisible(self._internet_available())

    def _internet_available(self):
        return (
            not self.request.active
            and self.request.state in {'empty', 'error', 'filtered', 'not_configured'}
            and bool(self.request.providers)
            and not any(provider.busy for provider in self.request.providers.values())
            and not self.request.candidates
        )

    def _selection_changed(self):
        selected = self._selected()
        self.apply_button.setEnabled(bool(selected) and self.request.state not in {'new', 'cancelled', 'applied'})
        self._show_details(selected)

    def _show_details(self, candidates):
        if not candidates:
            self.detail_value.setText(_('Select a result'))
            self.detail_line.setText(_('Its full value and sources appear here. Apply changes only this field.'))
            self.evidence.setText('')
            return
        values = list(dict.fromkeys(value for c in candidates for value in c.values))
        self.detail_value.setText('; '.join(values))
        if len(candidates) == 1:
            self.detail_line.setText(candidates[0].detail)
        else:
            count = len(candidates)
            self.detail_line.setText(ngettext('%d value selected', '%d values selected', count) % count)
        blocks = []
        for provider in PROVIDERS:
            evidence = list(dict.fromkeys(e for c in candidates for e in c.evidence if e.provider == provider))
            if not evidence:
                continue
            rows = []
            for item in evidence[:MAX_EVIDENCE_PER_PROVIDER]:
                row = escape(item.description)
                if item.url.startswith('https://'):
                    row += ' &nbsp;<a href="%s">%s</a>' % (escape(item.url, quote=True), escape(_('Open')))
                rows.append('<li>%s</li>' % row)
            if len(evidence) > MAX_EVIDENCE_PER_PROVIDER:
                rows.append('<li>%s</li>' % escape(_('and %d more') % (len(evidence) - MAX_EVIDENCE_PER_PROVIDER)))
            blocks.append(
                '<p style="margin:8px 0 2px 0"><b>%s</b></p><ul style="margin:0 0 0 -20px">%s</ul>'
                % (
                    escape(provider),
                    ''.join(rows),
                )
            )
        self.evidence.setText('<p style="margin:0"><b>%s</b></p>%s' % (escape(_('Sources')), ''.join(blocks)))

    # Actions

    def _search_internet(self):
        if not self._internet_available():
            return
        field = {
            'genre': 'music genre',
            'style': 'music style',
            'date': 'release year',
        }.get(self.tag, self.tag)
        identity = ' '.join(f'"{value}"' for value in (self.request.artist, self.request.title) if value)
        query = ' '.join(filter(None, [identity, field]))
        webbrowser2.open('https://www.google.com/search?' + urlencode({'q': query}))

    def _load_more(self):
        if self.request.load_more():
            self._elapsed.start()
            self._clock.start()
        self._refresh_providers()

    def _apply(self):
        if not self.apply_button.isEnabled():
            return
        if self.request.apply(self._selected()):
            self.accept()
        else:
            self.status.setText(_('The field or recording changed. Close this window and search again.'))
            self.apply_button.setEnabled(False)

    def _closed(self, result):
        self._clock.stop()
        if self.request.state != 'applied':
            self.request.cancel()
        self.request.deleteLater()
