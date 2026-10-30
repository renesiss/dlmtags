# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""Inline artwork suggestions for one recording, chosen into New Tags in place.

Releases come from MusicBrainz and front covers from the Cover Art Archive,
both through Picard's web service (its rate limiting, cache and cancellation).
A chosen image goes through CoverArtSetter like any other loaded cover; only
the normal Save writes it to the file.
"""

from collections import OrderedDict
from dataclasses import dataclass
from functools import partial
import hashlib
from html import escape
import os.path
from pathlib import Path
import re
import weakref

from PyQt6 import (
    QtCore,
    QtGui,
    QtWidgets,
)
from PyQt6.QtNetwork import QNetworkReply

from picard import log
from picard.config import get_config
from picard.const import CAA_URL
from picard.coverart.image import (
    CaaCoverArtImage,
    CoverArtImageError,
    LocalFileCoverArtImage,
)
from picard.coverart.setters import (
    CoverArtSetter,
    CoverArtSetterMode,
)
from picard.file import File
from picard.i18n import (
    gettext as _,
    ngettext,
)
from picard.smartscan import Outcome
from picard.util import imageinfo
from picard.util.thread import run_task
from picard.webservice.api_helpers import build_lucene_query

from picard.ui.dlmtags.components.styles import style_component
from picard.ui.dlmtags.suggestions import (
    SUGGESTED_COLOR,
    Status,
    album_suggestion,
    status_text,
)


MAX_RELEASES = 12
MAX_CACHED_SEARCHES = 16
THUMBNAIL_SIZE = 250
MBID_RE = re.compile(r'[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}')
MB_RELEASE_URL_RE = re.compile(r'https?://musicbrainz\.org/release/(' + MBID_RE.pattern + r')/?')
IMAGE_ROLE = QtCore.Qt.ItemDataRole.UserRole


def _is_default_artwork(data):
    resource = QtCore.QFile(':/images/dlmtags-artwork-default.png')
    if not resource.open(QtCore.QIODevice.OpenModeFlag.ReadOnly):
        return False
    return data == resource.readAll().data()


@dataclass(eq=False)
class ArtworkImage:
    release_id: str
    title: str
    date: str
    official: bool
    data: bytes
    pixmap: QtGui.QPixmap

    @property
    def description(self):
        return ' · '.join(filter(None, (self.title, self.date[:4])))

    def image_url(self):
        """Cover Art Archive front image at the size chosen in the CAA options."""
        size = get_config().setting['caa_image_size']
        suffix = 'front-%d' % size if size in {250, 500, 1200} else 'front'
        return '%s/release/%s/%s' % (CAA_URL, self.release_id, suffix)


def recording_for_artwork(file):
    """Recording MBID of a matched Smart Scan, else a valid tagged one, else ''."""
    decision = getattr(file, 'smart_scan_decision', None)
    if decision is not None and decision.outcome == Outcome.MATCHED and decision.recording_id:
        return decision.recording_id
    recording_id = file.metadata['musicbrainz_recordingid']
    return recording_id if MBID_RE.fullmatch(recording_id) else ''


class ArtworkSearch(QtCore.QObject):
    """Front covers of the releases of one recording, de-duplicated."""

    changed = QtCore.pyqtSignal()

    def __init__(self, tagger, recording_id, parent=None):
        super().__init__(parent)
        self.tagger = tagger
        self.recording_id = recording_id
        self.state = 'new'
        self.message = ''
        self.images = []
        self.releases = 0
        self._tasks = []
        self._pending = 0
        self._failed = 0
        self._digests = set()

    @property
    def active(self):
        return self.state == 'searching'

    def start(self):
        if self.state != 'new':
            return
        self.state = 'searching'
        self._tasks.append(
            self.tagger.mb_api.get_track_by_id(
                self.recording_id,
                self._releases_loaded,
                inc=('releases', 'release-groups'),
            )
        )
        self.changed.emit()

    def cancel(self):
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            if task is not None:
                self.tagger.webservice.abort_task(task)
        if self.active:
            self._finish('error', _('Cancelled.'))

    def _releases_loaded(self, document, http, error):
        if not self.active:
            return
        if error:
            self._finish('error', _('MusicBrainz request failed: %s') % http.errorString())
            return
        releases = [
            release
            for release in (document or {}).get('releases') or []
            if isinstance(release, dict) and MBID_RE.fullmatch(str(release.get('id', '')))
        ]
        # Official releases first, then the oldest; compilations last.
        releases.sort(
            key=lambda release: (
                'Compilation' in ((release.get('release-group') or {}).get('secondary-types') or ()),
                release.get('status') != 'Official',
                release.get('date') or '9999',
            )
        )
        seen = set()
        selected = []
        for release in releases:
            if release['id'] not in seen and len(selected) < MAX_RELEASES:
                seen.add(release['id'])
                selected.append(release)
        self.releases = len(selected)
        if not selected:
            self._finish('empty', _('MusicBrainz lists no releases for this recording.'))
            return
        for release in selected:
            self._pending += 1
            url = QtCore.QUrl('%s/release/%s/front-%d' % (CAA_URL, release['id'], THUMBNAIL_SIZE))
            self._tasks.append(
                self.tagger.webservice.download_url(
                    url=url,
                    handler=partial(self._thumbnail_loaded, release),
                    priority=False,
                    important=False,
                )
            )
        self.changed.emit()

    def _thumbnail_loaded(self, release, data, http, error):
        if not self.active:
            return
        self._pending -= 1
        if error:
            # 404 just means the release has no front cover in the archive.
            if error != QNetworkReply.NetworkError.ContentNotFoundError:
                self._failed += 1
                log.debug('Artwork suggestions: thumbnail for %s failed: %s', release['id'], http.errorString())
        else:
            data = bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            pixmap = QtGui.QPixmap()
            if digest not in self._digests and pixmap.loadFromData(data):
                self._digests.add(digest)
                self.images.append(
                    ArtworkImage(
                        release_id=release['id'],
                        title=str(release.get('title', '')),
                        date=str(release.get('date', '')),
                        official=release.get('status') == 'Official',
                        data=data,
                        pixmap=pixmap,
                    )
                )
        if self._pending:
            self.changed.emit()
        elif self.images:
            self._finish('ready', '')
        elif self._failed:
            self._finish('error', _('Cover Art Archive requests failed.'))
        else:
            self._finish('empty', _('No artwork in the Cover Art Archive for these releases.'))

    def _finish(self, state, message):
        self.state = state
        self.message = message
        self._tasks = []
        self.changed.emit()


class ManualArtworkSearch(ArtworkSearch):
    """Find releases by the entered song fields using Picard's MusicBrainz client."""

    def __init__(self, tagger, artist, title, album, parent=None):
        super().__init__(tagger, '', parent)
        self.artist = artist
        self.title = title
        self.album = album
        self._timed_out = False

    def start(self):
        if self.state != 'new':
            return
        self.state = 'searching'
        if self.title:
            query = build_lucene_query({'recording': self.title, 'artist': self.artist, 'release': self.album})
            request = self.tagger.mb_api.find_tracks(
                self._results_loaded, query=query, search=True, advanced_search=True, limit=25
            )
        else:
            query = build_lucene_query({'artist': self.artist, 'release': self.album})
            request = self.tagger.mb_api.find_releases(
                self._results_loaded, query=query, search=True, advanced_search=True, limit=25
            )
        self._tasks.append(request)
        self.changed.emit()

    def _results_loaded(self, document, http, error):
        if not self.active:
            return
        if error:
            message = (
                _('Artwork search timed out. Try again.')
                if error == QNetworkReply.NetworkError.TimeoutError
                else (_('MusicBrainz search failed: %s') % http.errorString())
            )
            self._finish('error', message)
            return
        if self.title:
            releases = [
                release
                for recording in (document or {}).get('recordings') or []
                for release in recording.get('releases') or []
            ]
        else:
            releases = (document or {}).get('releases') or []
        if not releases:
            self._finish('empty', _('No matching releases found.'))
            return
        self._releases_loaded({'releases': releases}, http, None)

    def _thumbnail_loaded(self, release, data, http, error):
        if error == QNetworkReply.NetworkError.TimeoutError:
            self._timed_out = True
        super()._thumbnail_loaded(release, data, http, error)

    def _finish(self, state, message):
        if state == 'error' and self._timed_out:
            message = _('Artwork search timed out. Try again.')
        super()._finish(state, message)


class ManualArtworkDialog(QtWidgets.QDialog):
    """Review remote or local artwork without changing the selected file."""

    def __init__(self, tagger, file, parent=None):
        super().__init__(parent)
        self.tagger = tagger
        self.file = file
        self.cover = None
        self._search = None
        self._preview_task = None
        self._preview_token = None
        self.setWindowTitle(_('Search Artwork'))
        self.setMinimumWidth(500)
        layout = QtWidgets.QVBoxLayout(self)
        fields = QtWidgets.QFormLayout()
        metadata = file.metadata
        self.artist = QtWidgets.QLineEdit(metadata.get('albumartist') or metadata.get('artist'), self)
        self.title = QtWidgets.QLineEdit(metadata.get('title'), self)
        self.album = QtWidgets.QLineEdit(metadata.get('album'), self)
        fields.addRow(_('Artist'), self.artist)
        fields.addRow(_('Title'), self.title)
        fields.addRow(_('Album'), self.album)
        layout.addLayout(fields)

        controls = QtWidgets.QHBoxLayout()
        self.search_button = QtWidgets.QPushButton(_('Search'), self)
        self.search_button.clicked.connect(self.search)
        controls.addWidget(self.search_button)
        self.import_button = QtWidgets.QPushButton(_('Import Image…'), self)
        self.import_button.clicked.connect(self.import_file)
        controls.addWidget(self.import_button)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.status = QtWidgets.QLabel(_('Search by artist, title or album, or import an image.'), self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.results = QtWidgets.QListWidget(self)
        self.results.setObjectName('dlmtags_manual_artwork_results')
        self.results.setViewMode(QtWidgets.QListView.ViewMode.IconMode)
        self.results.setResizeMode(QtWidgets.QListView.ResizeMode.Adjust)
        self.results.setMovement(QtWidgets.QListView.Movement.Static)
        self.results.setIconSize(QtCore.QSize(84, 84))
        self.results.setGridSize(QtCore.QSize(108, 120))
        self.results.setMinimumHeight(145)
        self.results.currentItemChanged.connect(self.select_result)
        layout.addWidget(self.results)
        self.preview = QtWidgets.QLabel(self)
        self.preview.setObjectName('dlmtags_manual_artwork_preview')
        self.preview.setMinimumHeight(220)
        self.preview.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.preview)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel, parent=self
        )
        confirm_button = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        assert confirm_button is not None
        self.confirm_button = confirm_button
        self.confirm_button.setText(_('Confirm'))
        self.confirm_button.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _cancel_requests(self):
        search, self._search = self._search, None
        if search is not None:
            search.cancel()
        task, self._preview_task = self._preview_task, None
        self._preview_token = None
        if task is not None:
            self.tagger.webservice.abort_task(task)

    def done(self, a0: int):
        self._cancel_requests()
        super().done(a0)

    def search(self):
        self._cancel_requests()
        self.cover = None
        self.confirm_button.setEnabled(False)
        self.preview.clear()
        self.results.clear()
        artist, title, album = (field.text().strip() for field in (self.artist, self.title, self.album))
        if not any((artist, title, album)):
            self.status.setText(_('Enter an artist, title or album to search.'))
            return
        search = self._search = ManualArtworkSearch(self.tagger, artist, title, album, self)
        search.changed.connect(self._search_changed)
        search.start()
        self._search_changed()

    def _search_changed(self):
        search = self._search
        if search is None:
            return
        if search.active:
            self.status.setText(_('Searching MusicBrainz and the Cover Art Archive…'))
            return
        if search.state != 'ready':
            self.status.setText(search.message)
            return
        with QtCore.QSignalBlocker(self.results):
            self.results.clear()
            for image in search.images:
                item = QtWidgets.QListWidgetItem(QtGui.QIcon(image.pixmap), image.title)
                item.setData(IMAGE_ROLE, image)
                item.setToolTip(image.description)
                self.results.addItem(item)
        self.status.setText(_('Select an image to preview it before confirming.'))

    def select_result(self, item, _previous=None):
        task, self._preview_task = self._preview_task, None
        if task is not None:
            self.tagger.webservice.abort_task(task)
        self._preview_token = None
        self.cover = None
        self.confirm_button.setEnabled(False)
        if item is None:
            self.preview.clear()
            return
        image = item.data(IMAGE_ROLE)
        self._show_preview(image.pixmap)
        self.status.setText(_('Loading full-size artwork…'))
        token = self._preview_token = object()
        self._preview_task = self.tagger.webservice.download_url(
            url=QtCore.QUrl(image.image_url()),
            handler=partial(self._image_loaded, token, image),
            priority=True,
            important=True,
        )

    def _show_preview(self, pixmap):
        self.preview.setPixmap(
            pixmap.scaled(
                QtCore.QSize(240, 220),
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _image_loaded(self, token, image, data, http, error):
        if token is not self._preview_token:
            return
        self._preview_task = None
        if error:
            self.status.setText(
                _('Artwork download timed out. Try again.')
                if error == QNetworkReply.NetworkError.TimeoutError
                else _('Could not download artwork: %s') % http.errorString()
            )
            return
        data = bytes(data)
        if _is_default_artwork(data):
            self.status.setText(_('The default placeholder cannot be used as cover art.'))
            return
        pixmap = QtGui.QPixmap()
        if not pixmap.loadFromData(data):
            self.status.setText(_('Could not read the downloaded artwork.'))
            return
        try:
            self.cover = CaaCoverArtImage(url=image.image_url(), types=['front'], is_front=True, data=data)
        except CoverArtImageError as error:
            self.status.setText(_('Could not read the downloaded artwork: %s') % error)
            return
        self._show_preview(pixmap)
        self.confirm_button.setEnabled(True)
        self.status.setText(_('Ready to confirm. Nothing is saved until Save.'))

    def import_file(self):
        extensions = sorted('*' + ext for ext in imageinfo.get_supported_extensions())
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            _('Import Artwork — DLMtags'),
            '',
            _('Image files') + ' (' + ' '.join(extensions) + ')',
        )
        if path:
            self.load_local_path(path)

    def load_local_path(self, path):
        self._cancel_requests()
        self.results.clear()
        self.cover = None
        self.confirm_button.setEnabled(False)
        self.preview.clear()
        self.status.setText(_('Loading local artwork…'))
        token = self._preview_token = object()
        run_task(Path(path).read_bytes, partial(self._local_loaded, token, path), traceback=False)

    def _local_loaded(self, token, path, result=None, error=None):
        if token is not self._preview_token:
            return
        if error is not None:
            self.status.setText(_('Could not open image: %s') % error)
            return
        if not isinstance(result, bytes):
            self.status.setText(_('Could not open the selected image.'))
            return
        if _is_default_artwork(result):
            self.status.setText(_('The default placeholder cannot be used as cover art.'))
            return
        pixmap = QtGui.QPixmap()
        if not pixmap.loadFromData(result):
            self.status.setText(_('The selected file is not a readable image.'))
            return
        try:
            cover = LocalFileCoverArtImage(path, types=['front'])
            cover.set_data(result)
        except CoverArtImageError as error:
            self.status.setText(_('Could not read the image: %s') % error)
            return
        self.cover = cover
        self._show_preview(pixmap)
        self.confirm_button.setEnabled(True)
        self.status.setText(_('Ready to confirm. Nothing is saved until Save.'))


class ArtworkSuggestions(QtWidgets.QWidget):
    """Suggested artwork first, other artwork as thumbnails; one click chooses."""

    def __init__(self, tagger, parent=None):
        super().__init__(parent)
        self.tagger = tagger
        self._file = None
        self._searches = OrderedDict()
        # Image this panel put into each file's New Tags, so choosing again
        # replaces it instead of piling up front covers.
        self._chosen = weakref.WeakKeyDictionary()
        self._loading = None
        self._loading_image = None
        self._suggested = None

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.status = QtWidgets.QLabel(self)
        self.status.setObjectName('dlmtags_uncertain_artwork_status')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.suggested_heading = self._heading(_('SUGGESTED ARTWORK'))
        layout.addWidget(self.suggested_heading)
        suggested = QtWidgets.QHBoxLayout()
        suggested.setSpacing(10)
        self.suggested_button = QtWidgets.QToolButton(self)
        self.suggested_button.setObjectName('dlmtags_suggested_artwork')
        self.suggested_button.setIconSize(QtCore.QSize(120, 120))
        self.suggested_button.setFixedSize(132, 132)
        self.suggested_button.clicked.connect(lambda: self.choose(self._suggested))
        suggested.addWidget(self.suggested_button, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        self.suggested_label = QtWidgets.QLabel(self)
        self.suggested_label.setWordWrap(True)
        self.suggested_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop)
        suggested.addWidget(self.suggested_label, 1)
        layout.addLayout(suggested)

        self.other_heading = self._heading(_('OTHER ARTWORK'))
        layout.addWidget(self.other_heading)
        self.others = QtWidgets.QListWidget(self)
        self.others.setObjectName('dlmtags_other_artwork')
        self.others.setAccessibleName(_('Other artwork'))
        self.others.setViewMode(QtWidgets.QListView.ViewMode.IconMode)
        self.others.setResizeMode(QtWidgets.QListView.ResizeMode.Adjust)
        self.others.setMovement(QtWidgets.QListView.Movement.Static)
        self.others.setIconSize(QtCore.QSize(84, 84))
        self.others.setGridSize(QtCore.QSize(100, 118))
        self.others.setWordWrap(True)
        self.others.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.others.setMinimumHeight(124)
        self.others.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.others.itemClicked.connect(lambda item: self.choose(item.data(IMAGE_ROLE)))
        self.others.itemActivated.connect(lambda item: self.choose(item.data(IMAGE_ROLE)))
        layout.addWidget(self.others, 1)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        self.manual_button = QtWidgets.QToolButton(self)
        self.manual_button.setObjectName('dlmtags_search_artwork')
        self.manual_button.setText(_('Search Artwork'))
        self.manual_button.setToolTip(_('Search MusicBrainz artwork or import a local image'))
        self.manual_button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.manual_button.setIcon(QtGui.QIcon(os.path.join(os.path.dirname(__file__), 'icons', 'search-artwork.svg')))
        self.manual_button.setIconSize(QtCore.QSize(26, 26))
        style_component(self.manual_button, 'actionButton')
        self.manual_button.clicked.connect(self.open_manual_search)
        buttons.addWidget(self.manual_button)
        self.find_button = QtWidgets.QPushButton(_('Find Artwork'), self)
        self.find_button.setToolTip(_('Search the Cover Art Archive for the releases of this recording'))
        self.find_button.clicked.connect(self.search)
        buttons.addWidget(self.find_button)
        layout.addLayout(buttons)
        self.set_file(None)

    def _heading(self, text):
        label = QtWidgets.QLabel(text, self)
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        return label

    @property
    def file(self):
        return self._file

    def set_file(self, file):
        """Follow the single selected song; searches run only after its Scan matched."""
        if file is not None and (not isinstance(file, File) or file.state == File.State.REMOVED):
            file = None
        if file is not self._file:
            self._loading = self._loading_image = None
        self._file = file
        decision = getattr(file, 'smart_scan_decision', None)
        if decision is not None and decision.outcome == Outcome.MATCHED:
            self.search()
        else:
            self.refresh()

    def _current_search(self):
        recording_id = recording_for_artwork(self._file) if self._file is not None else ''
        return self._searches.get(recording_id) if recording_id else None

    def search(self):
        file = self._file
        recording_id = recording_for_artwork(file) if file is not None else ''
        if not recording_id:
            self.refresh()
            return
        search = self._searches.get(recording_id)
        if search is None or search.state == 'error':
            if search is not None:
                search.deleteLater()
            search = ArtworkSearch(self.tagger, recording_id, self)
            search.changed.connect(self._search_changed)
            self._searches[recording_id] = search
            while len(self._searches) > MAX_CACHED_SEARCHES:
                unused, old = self._searches.popitem(last=False)
                old.cancel()
                old.deleteLater()
            search.start()
        self._searches.move_to_end(recording_id)
        self.refresh()

    def cancel(self):
        for search in self._searches.values():
            search.cancel()

    def _search_changed(self):
        # Another song's search keeps filling its cache; refresh shows only
        # the selected song's results.
        self.refresh()

    def _rank(self, search):
        """(image, Status) for the one suggested image, and the others in order."""
        file = self._file
        images = list(search.images)
        chosen = self._chosen_release(file)
        decision = file.smart_scan_decision
        verified = file.metadata['musicbrainz_albumid'] if decision is not None and decision.release_verified else ''
        match = MB_RELEASE_URL_RE.fullmatch(file.dlmtags_tag_urls.get('album', ''))
        album_release = match.group(1) if match else ''
        top = album_suggestion(file)
        album = (file.metadata['album'] or (top.values[0] if top else '')).casefold()
        if not album and decision is not None and decision.match and decision.match.track:
            # Without any album, the single named after the song is the best guess.
            album = str(decision.match.track.get('title', '')).casefold()
        rules = (
            (Status.VERIFIED, lambda image: verified and image.release_id == verified),
            (Status.SUGGESTED, lambda image: album_release and image.release_id == album_release),
            (Status.SUGGESTED, lambda image: album and image.title.casefold() == album),
        )
        suggested = next(
            ((image, status) for status, rule in rules for image in images if rule(image)),
            None,
        )
        if suggested is not None:
            images.remove(suggested[0])
        return suggested, images, chosen

    def _chosen_release(self, file):
        chosen = self._chosen.get(file)
        if chosen is None:
            return ''
        release_id, image = chosen
        return release_id if any(i is image for i in file.metadata.images) else ''

    def refresh(self):
        file = self._file
        self.manual_button.setEnabled(file is not None and file.can_show_coverart)
        search = self._current_search()
        suggested, others, chosen = self._rank(search) if search is not None else (None, [], '')
        recording_id = recording_for_artwork(file) if file is not None else ''
        searchable = bool(recording_id) and (search is None or search.state == 'error')
        self.find_button.setEnabled(searchable)
        self.find_button.setVisible(searchable)

        if file is None:
            self._set_status(None, _('Select one song to see artwork suggestions.'))
        elif not recording_id:
            self._set_status(None, _('Scan the song to find artwork suggestions.'))
        elif search is None:
            self._set_status(None, _('Press Find Artwork to search the Cover Art Archive.'))
        elif self._loading is not None:
            self._set_status(Status.SEARCHING, _('Loading the chosen artwork…'))
        elif search.active:
            found = len(search.images)
            self._set_status(
                Status.SEARCHING,
                ngettext(
                    'Cover Art Archive: %d image found so far',
                    'Cover Art Archive: %d images found so far',
                    found,
                )
                % found,
            )
        elif search.state == 'error':
            self._set_status(Status.ERROR, search.message)
        elif search.state == 'empty':
            self._set_status(Status.NOT_FOUND, search.message)
        elif chosen:
            self._set_status(Status.CHOSEN, _('Chosen artwork is in New Tags. Press Save to write it.'))
        else:
            self._set_status(None, _('Click an image to put it in New Tags. Nothing is saved until Save.'))

        self._suggested = suggested[0] if suggested else None
        self.suggested_heading.setVisible(suggested is not None)
        self.suggested_button.setVisible(suggested is not None)
        self.suggested_label.setVisible(suggested is not None)
        if suggested is not None:
            image, status = suggested
            if image.release_id == chosen:
                status = Status.CHOSEN
            self.suggested_button.setIcon(QtGui.QIcon(image.pixmap))
            self.suggested_button.setToolTip(_('Click to put this artwork in New Tags'))
            border = SUGGESTED_COLOR if status == Status.SUGGESTED else 'palette(highlight)'
            self.suggested_button.setStyleSheet(
                'QToolButton { border: 2px solid %s; border-radius: 8px; padding: 4px; }' % border
            )
            self.suggested_label.setText(
                '<b>%s</b><br>%s<br>%s'
                % (
                    escape(status_text(status)),
                    escape(image.description),
                    _('Cover Art Archive'),
                )
            )
            self.suggested_label.setStyleSheet('color: %s;' % SUGGESTED_COLOR if status == Status.SUGGESTED else '')

        self.other_heading.setVisible(bool(others))
        self.others.setVisible(bool(others))
        with QtCore.QSignalBlocker(self.others):
            self.others.clear()
            for image in others:
                text = image.title
                if image.release_id == chosen:
                    text = status_text(Status.CHOSEN) + '\n' + text
                item = QtWidgets.QListWidgetItem(QtGui.QIcon(image.pixmap), text)
                item.setData(IMAGE_ROLE, image)
                item.setToolTip(
                    '\n'.join(
                        (
                            image.description,
                            _('Cover Art Archive'),
                            _('Click to put it in New Tags'),
                        )
                    )
                )
                self.others.addItem(item)
                if image.release_id == chosen:
                    self.others.setCurrentItem(item)

    def _set_status(self, status, message):
        text = message if status is None else '%s — %s' % (status_text(status), message)
        self.status.setText(text)
        self.status.setStyleSheet('color: %s;' % SUGGESTED_COLOR if status == Status.SUGGESTED else '')

    def open_manual_search(self):
        file = self._file
        if file is None or not file.can_show_coverart:
            return
        dialog = ManualArtworkDialog(self.tagger, file, self)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted and dialog.cover is not None and file is self._file:
            mode = (
                CoverArtSetterMode.REPLACE
                if get_config().setting['load_image_behavior'] == 'replace'
                else (CoverArtSetterMode.APPEND)
            )
            CoverArtSetter(mode, dialog.cover, file).set_coverart()
            self.refresh()
        dialog.deleteLater()

    def choose(self, image):
        """Load the full image and put it in the file's New Tags. Never saves."""
        file = self._file
        if image is None or file is None or not file.can_show_coverart or image is self._loading_image:
            return
        token = self._loading = object()
        self._loading_image = image
        self.tagger.webservice.download_url(
            url=QtCore.QUrl(image.image_url()),
            handler=partial(self._image_loaded, token, file, image),
            priority=True,
            important=True,
        )
        self.refresh()

    def _image_loaded(self, token, file, image, data, http, error):
        # A newer choice or another song replaced this request: drop it.
        if token is not self._loading or file is not self._file:
            return
        self._loading = self._loading_image = None
        if file.state == File.State.REMOVED:
            self.refresh()
            return
        if error:
            self._set_status(Status.ERROR, _('Could not load the artwork: %s') % http.errorString())
            return
        try:
            cover = CaaCoverArtImage(url=image.image_url(), types=['front'], is_front=True, data=bytes(data))
        except CoverArtImageError as e:
            self._set_status(Status.ERROR, _('Could not read the artwork: %s') % e)
            return
        previous = self._chosen.get(file)
        if previous is not None:
            images = file.metadata.images
            for index in range(len(images) - 1, -1, -1):
                if images[index] is previous[1]:
                    del images[index]
        if get_config().setting['load_image_behavior'] == 'replace':
            mode = CoverArtSetterMode.REPLACE
        else:
            mode = CoverArtSetterMode.APPEND
        CoverArtSetter(mode, cover, file).set_coverart()
        self._chosen[file] = (image.release_id, cover)
        self.refresh()
