# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""WIKIPEDIA — SONG INFO: the recognised song, its verified MusicBrainz
facts and an excerpt of its verified Wikipedia article.

Plain text only, shown inside DLMtags. The browser opens only when the user
clicks Open Wikipedia or the licence link. Nothing here changes metadata,
artwork or Save.
"""

from collections import OrderedDict
from html import escape

from PyQt6 import (
    QtCore,
    QtGui,
    QtWidgets,
)

from picard.file import File
from picard.i18n import (
    N_,
    gettext as _,
)
from picard.smartscan import (
    Outcome,
    candidate_artist_names,
)
from picard.songinfo import (
    LICENSE_NAME,
    LICENSE_URL,
    SongInfoLookup,
)
from picard.util import format_time

from picard.ui.dlmtags.components.card import Card
from picard.ui.dlmtags.components.styles import style_component
from picard.ui.dlmtags.sidebar_style import sidebar_icon


MAX_CACHED_LOOKUPS = 64
NOT_FOUND_TEXT = N_('No verified Wikipedia article found')
# Below this width per column, Song Details uses a single column.
DETAIL_COLUMN_MIN_WIDTH = 190
MUSICBRAINZ = 'MusicBrainz'


def song_identity(file):
    """(recording MBID, title, artist names) confirmed by a matched Smart Scan, or None."""
    decision = getattr(file, 'smart_scan_decision', None)
    if decision is None or decision.outcome != Outcome.MATCHED or not decision.match or not decision.match.track:
        return None
    track = decision.match.track
    full, names = candidate_artist_names(track)
    return track.get('id', ''), track.get('title', ''), full, list(dict.fromkeys([full, *names]))


def song_details(file, lookup):
    """(field, value, source) rows that are documented; unknown fields are left out,
    except Year, which shows "—" when no source states it.

    Year is the first release of the recording (MusicBrainz), else the year in
    the verified article's description. Album only from a release the Scan
    verified, never from a suggestion or a later compilation.
    """
    decision = file.smart_scan_decision
    facts = lookup.facts if lookup is not None else None
    track = decision.match.track
    rows = []
    article = lookup.article if lookup is not None and lookup.state == 'found' else None
    if facts and facts.year:
        rows.append((_('Year'), facts.year, _('MusicBrainz, first release')))
    elif article is not None and article.year:
        rows.append((_('Year'), article.year, _('Wikipedia')))
    else:
        rows.append((_('Year'), '—', ''))
    release = decision.release or {}
    if decision.release_verified and release.get('title'):
        rows.append((_('Album'), release['title'], _('MusicBrainz, verified release')))
    if facts and facts.songwriters:
        rows.append((_('Songwriters'), ', '.join(facts.songwriters), MUSICBRAINZ))
    if facts and facts.producers:
        rows.append((_('Producers'), ', '.join(facts.producers), MUSICBRAINZ))
    if facts and facts.genres:
        rows.append((_('Genre'), ', '.join(facts.genres), MUSICBRAINZ))
    length = (facts.length_ms if facts else 0) or track.get('length')
    if isinstance(length, int) and length > 0:
        rows.append((_('Duration'), format_time(length), MUSICBRAINZ))
    return rows


class DetailsGrid(QtWidgets.QWidget):
    """Compact field/value cells in two columns, one column when narrow."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cells = []
        self.rows = []
        self._columns = 0
        self.grid = QtWidgets.QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(8)

    def set_rows(self, rows):
        if rows == self.rows:
            return
        self.rows = list(rows)
        for cell in self._cells:
            self.grid.removeWidget(cell)
            cell.deleteLater()
        self._cells = []
        for field, value, source in rows:
            cell = QtWidgets.QWidget(self)
            layout = QtWidgets.QVBoxLayout(cell)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(1)
            caption = _label(cell, 'caption', '%s · %s' % (field, source) if source else field)
            caption.setToolTip(_('Source: %s') % source if source else _('Not stated by any source'))
            value_label = _label(cell, 'value', value)
            value_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(caption)
            layout.addWidget(value_label)
            self._cells.append(cell)
        self._columns = 0
        self._arrange()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._arrange()

    def _arrange(self):
        columns = 2 if self.width() >= 2 * DETAIL_COLUMN_MIN_WIDTH else 1
        if columns == self._columns:
            return
        self._columns = columns
        for cell in self._cells:
            self.grid.removeWidget(cell)
        for index, cell in enumerate(self._cells):
            self.grid.addWidget(cell, index // columns, index % columns)
        for column in range(2):
            self.grid.setColumnStretch(column, 1 if column < columns else 0)


def _label(parent, role, text=''):
    label = QtWidgets.QLabel(text, parent)
    label.setProperty('songInfoRole', role)
    label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class SongInfoPanel(Card):
    """Loading / Found / Not Found / Error for the single selected, recognised song."""

    def __init__(self, tagger, recognition, parent=None):
        super().__init__(parent, shadow=True)
        self.tagger = tagger
        self.recognition = recognition
        self._file = None
        self._lookups = OrderedDict()
        self._shown_article = None

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(8)
        heading = QtWidgets.QLabel(_('WIKIPEDIA — SONG INFO'), self)
        font = heading.font()
        font.setBold(True)
        heading.setFont(font)
        # Wraps instead of setting the panel's (and window's) minimum width.
        heading.setWordWrap(True)
        header.addWidget(heading, 1)
        header.addStretch(1)
        # Recognition status stays visible as a small chip; its technical
        # details open only on request.
        header.addWidget(recognition.status_label)
        header.addWidget(recognition.details_toggle)
        self.content_layout.addLayout(header)
        self.content_layout.addWidget(recognition)

        self.scroll = QtWidgets.QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        style_component(self.scroll, 'scrollArea')
        body = QtWidgets.QWidget(self.scroll)
        style_component(body, 'songInfo')
        layout = QtWidgets.QVBoxLayout(body)
        layout.setContentsMargins(0, 4, 8, 4)
        layout.setSpacing(6)
        self.state_label = _label(body, 'muted')
        self.title_label = _label(body, 'title')
        self.artist_label = _label(body, 'artist')
        self.description_label = _label(body, 'muted')
        for label in (self.state_label, self.title_label, self.artist_label, self.description_label):
            layout.addWidget(label)
        self.details_heading = _label(body, 'section', _('SONG DETAILS'))
        layout.addWidget(self.details_heading)
        self.details = DetailsGrid(body)
        layout.addWidget(self.details)
        self.about_heading = _label(body, 'section', _('ABOUT THE SONG'))
        layout.addWidget(self.about_heading)
        self.about = QtWidgets.QWidget(body)
        self.about_layout = QtWidgets.QVBoxLayout(self.about)
        self.about_layout.setContentsMargins(0, 0, 0, 0)
        self.about_layout.setSpacing(8)
        layout.addWidget(self.about)
        retry = QtWidgets.QHBoxLayout()
        self.retry_button = QtWidgets.QPushButton(_('Try Again'), body)
        self.retry_button.clicked.connect(self.retry)
        retry.addWidget(self.retry_button)
        retry.addStretch(1)
        layout.addLayout(retry)
        layout.addStretch(1)
        self.scroll.setWidget(body)
        self.content_layout.addWidget(self.scroll, 1)

        self.footer = QtWidgets.QWidget(self)
        style_component(self.footer, 'songInfo')
        footer = QtWidgets.QHBoxLayout(self.footer)
        footer.setContentsMargins(0, 4, 0, 0)
        footer.setSpacing(8)
        self.source_label = _label(self.footer, 'muted')
        self.source_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        # The licence link opens in the browser only when clicked.
        self.source_label.setOpenExternalLinks(True)
        self.source_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextBrowserInteraction)
        footer.addWidget(self.source_label, 1)
        self.open_button = QtWidgets.QToolButton(self.footer)
        self.open_button.setObjectName('dlmtags_open_wikipedia')
        self.open_button.setText(_('Open Wikipedia'))
        self.open_button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        style_component(self.open_button, 'actionButton')
        self.open_button.setIcon(sidebar_icon(self.open_button, 'external'))
        self.open_button.clicked.connect(self.open_article)
        footer.addWidget(self.open_button, 0, QtCore.Qt.AlignmentFlag.AlignBottom)
        self.content_layout.addWidget(self.footer)
        self.set_file(None)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.Type.PaletteChange and hasattr(self, 'open_button'):
            self.open_button.setIcon(sidebar_icon(self.open_button, 'external'))

    def open_article(self):
        """Open the verified article of the song shown, and only on this click."""
        article = self._shown_article
        if article is not None:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(article.url))

    @property
    def lookup(self):
        identity = song_identity(self._file) if self._file is not None else None
        return self._lookups.get(identity[0]) if identity else None

    def set_file(self, file):
        """Follow the single selected song; look it up once its Scan matched."""
        if file is not None and (not isinstance(file, File) or file.state == File.State.REMOVED):
            file = None
        self._file = file
        identity = song_identity(file) if file is not None else None
        if identity and identity[0] and identity[0] not in self._lookups:
            self._start(identity)
        self.refresh()

    def retry(self):
        identity = song_identity(self._file) if self._file is not None else None
        if identity and self.lookup is not None and self.lookup.state == 'error':
            self._start(identity)
        self.refresh()

    def _start(self, identity):
        recording_id, title, unused, artists = identity
        lookup = SongInfoLookup(self.tagger, recording_id, title, artists, self)
        lookup.changed.connect(self.refresh)
        old = self._lookups.pop(recording_id, None)
        if old is not None:
            old.deleteLater()
        self._lookups[recording_id] = lookup
        while len(self._lookups) > MAX_CACHED_LOOKUPS:
            unused, stale = self._lookups.popitem(last=False)
            stale.cancel()
            stale.deleteLater()
        lookup.start()

    def cancel(self):
        for lookup in self._lookups.values():
            lookup.cancel()

    def refresh(self):
        # Every lookup reports here; only the selected song's result is shown.
        file = self._file
        identity = song_identity(file) if file is not None else None
        lookup = self.lookup
        if file is None:
            state = _('Select one song to see information about it.')
        elif getattr(file, 'smart_scan_stage', None):
            state = _('Scanning…')
            identity = None
        elif identity is None:
            state = _('Scan the song. Song info appears once the recording is recognised.')
        elif lookup is None or lookup.active:
            state = _('Loading Wikipedia…')
        elif lookup.state == 'not_found':
            state = _(NOT_FOUND_TEXT)
        elif lookup.state == 'error':
            state = _('Error: %s') % lookup.message
        else:
            state = ''
        self.state_label.setText(state)
        self.state_label.setVisible(bool(state))
        self.retry_button.setVisible(identity is not None and lookup is not None and lookup.state == 'error')

        article = lookup.article if identity and lookup is not None and lookup.state == 'found' else None
        self.title_label.setText(identity[1] if identity else '')
        self.artist_label.setText(identity[2] if identity else '')
        self.title_label.setVisible(identity is not None)
        self.artist_label.setVisible(identity is not None)
        self.description_label.setText(article.description if article else '')
        self.description_label.setVisible(bool(article and article.description))

        rows = song_details(file, lookup) if identity else []
        self.details.set_rows(rows)
        self.details_heading.setVisible(bool(rows))
        self.details.setVisible(bool(rows))

        if article is not self._shown_article:
            self._show_article(article)
        self.about_heading.setVisible(article is not None)
        self.about.setVisible(article is not None)
        self.footer.setVisible(article is not None)

    def _show_article(self, article):
        self._shown_article = article
        while self.about_layout.count():
            item = self.about_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        if article is None:
            self.source_label.setText('')
            self.open_button.setToolTip('')
            return
        for kind, text in article.blocks:
            label = _label(self.about, 'heading' if kind == 'heading' else 'body', text)
            if kind != 'heading':
                label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
            self.about_layout.addWidget(label)
        how = _('linked from MusicBrainz') if article.method == 'musicbrainz' else _('verified search result')
        changes = _('Excerpt, plain text, shortened') if article.shortened else _('Excerpt, plain text')
        self.source_label.setText('%s · %s · %s · <a href="%s">%s</a>' % (
            escape(_('Source: Wikipedia, “%s”') % article.title), escape(how), escape(changes),
            escape(LICENSE_URL, quote=True), escape(LICENSE_NAME),
        ))
        self.open_button.setToolTip(article.url)
