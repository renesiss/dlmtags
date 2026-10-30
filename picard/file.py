# Picard, the next-generation MusicBrainz tagger
#
# Copyright (C) 2004 Robert Kaye
# Copyright (C) 2006-2009, 2011-2013, 2017 Lukáš Lalinský
# Copyright (C) 2007-2011, 2015, 2018-2026 Philipp Wolfer
# Copyright (C) 2008 Gary van der Merwe
# Copyright (C) 2008-2009 Nikolai Prokoschenko
# Copyright (C) 2009 Carlin Mangar
# Copyright (C) 2009 David Hilton
# Copyright (C) 2011-2014 Michael Wiencek
# Copyright (C) 2012 Erik Wasser
# Copyright (C) 2012 Johannes Weißl
# Copyright (C) 2012 noobie
# Copyright (C) 2012-2014 Wieland Hoffmann
# Copyright (C) 2013 Calvin Walton
# Copyright (C) 2013-2014 Ionuț Ciocîrlan
# Copyright (C) 2013-2014, 2017, 2021 Sophist-UK
# Copyright (C) 2013-2014, 2017-2026 Laurent Monin
# Copyright (C) 2016 Rahul Raturi
# Copyright (C) 2016 Ville Skyttä
# Copyright (C) 2016-2018 Sambhav Kothari
# Copyright (C) 2017-2018 Antonio Larrosa
# Copyright (C) 2019 Joel Lintunen
# Copyright (C) 2020 Ray Bouchard
# Copyright (C) 2020-2021 Gabriel Ferreira
# Copyright (C) 2021 Petit Minion
# Copyright (C) 2021, 2023, 2025 Bob Swift
# Copyright (C) 2024 Giorgio Fontanive
# Copyright (C) 2024 Suryansh Shakya
# Copyright (C) 2025 Khoa Nguyen
# Copyright (C) 2025 frcooper
# Copyright (C) 2026 Bryan Roessler
# Copyright (C) 2026 Deepak Tiwari
# Copyright (C) 2026 Goldmaster
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <https://www.gnu.org/licenses/>.


from collections import Counter
from enum import (
    Enum,
    auto,
)
import fnmatch
from functools import partial
import hashlib
import os
import os.path
from pathlib import Path
import re
import shutil
import time
from typing import (
    IO,
    TYPE_CHECKING,
)
import weakref

from mutagen import (
    FileType,
    MutagenError,
)

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtNetwork import (
    QNetworkReply,
    QNetworkRequest,
)

from picard import (
    PICARD_APP_NAME,
    log,
)
from picard.config import get_config
from picard.const.defaults import DEFAULT_TIME_FORMAT
from picard.const.sys import (
    IS_MACOS,
    IS_WIN,
)
from picard.debug_opts import DebugOpt
from picard.discogsgenre import DiscogsResponses
from picard.i18n import (
    N_,
    gettext as _,
)
from picard.item import MetadataItem
from picard.matching import (
    SimMatchTrack,
    _format_sim_breakdown,
    compare_to_track,
    find_best_match_with_margin,
)
from picard.mbjson import recording_to_metadata
from picard.metadata import Metadata
from picard.plugin import PluginFunctions
from picard.script import get_file_naming_script
from picard.smartgenre import SmartGenreRequest
from picard.smartscan import (
    RELEASE_SPECIFIC_TAGS,
    Decision,
    Outcome,
    SmartScanThresholds,
    Source,
    TagSuggestion,
    combine as combine_smart_scan_decisions,
    confirmable_recordings,
    confirmed_decision,
    decide as decide_smart_scan,
    file_hints,
    first_release_date,
    metadata_lookup_useful,
    normalize_release_date,
    protected_snapshot,
    restore_protected,
    tag_suggestions,
)
from picard.tagprovenance import set_source
from picard.tags import (
    ALL_TAGS,
    calculated_tag_names,
    file_info_tag_names,
    preserved_tag_names,
)
from picard.tags.preserved import UserPreservedTags
from picard.tagsearch import TagSearchRequest
from picard.util import (
    any_exception_isinstance,
    bytes2human,
    emptydir,
    format_time,
    is_absolute_path,
    normpath,
    resolve_fs_path,
    samefile,
    thread,
    tracknum_and_title_from_filename,
)
from picard.util.filenaming import (
    get_available_filename,
    make_save_path,
    make_short_filename,
    move_ensure_casing,
)
from picard.util.imagelist import ImageList
from picard.util.scripttofilename import script_to_filename_with_metadata

from picard.ui.filter import Filter


if TYPE_CHECKING:
    from collections.abc import Callable

    from picard.cluster import Cluster
    from picard.track import Track


FILE_COMPARISON_WEIGHTS = {
    'identifiers': {
        'barcode': 28,
        'catno': 22,
        'isrc': 28,
    },
    'similarity': {
        'album': 5,
        'artist': 4,
        'date': 4,
        'length': 10,
        'title': 13,
        'totaltracks': 4,
        'tracknumber': 4,
    },
    'preferences': {
        'format': 2,
        'isvideo': 2,
        'releasecountry': 2,
        'releasetype': 14,
    },
}


class FileIdentityError(Exception):
    pass


class FileIdentity:
    _READ_SIZE = 8192

    def __init__(self, filename):
        self._filepath = Path(filename)
        try:
            stat = os.stat(self._filepath)
            self._inode = stat.st_ino
            self._size = stat.st_size
            self._mtime = stat.st_mtime
            self._exists = True

            try:
                self._hash = self._fast_hash()
            except FileIdentityError:
                self._hash = None
        except OSError:
            self._inode = self._size = self._mtime = self._hash = None
            self._exists = False

    def __eq__(self, other):
        if other is None or not self._exists == other._exists:
            return False

        # If both don't exist, they're equal
        if not self._exists:
            return True

        # Different inode or size means different file
        if self._inode != other._inode or self._size != other._size:
            return False

        # Different mtime means file changed
        if self._mtime != other._mtime:
            return False
        # Compute missing hash values, may error if unreadable
        try:
            _other_current_hash = other._fast_hash()
        except Exception as e:
            raise FileIdentityError from e
        try:
            if self._hash is None:
                self._hash = self._fast_hash()
        except Exception as e:
            raise FileIdentityError from e
        # Now both hashes exist, compare them
        return self._hash == other._hash

    def __bool__(self):
        return self._exists

    def _fast_hash(self):
        try:
            with open(self._filepath, "rb") as fh:
                data = fh.read(self._READ_SIZE)
                h = hashlib.blake2b()
                h.update(data)
                return h.hexdigest()
        except Exception as e:
            raise FileIdentityError(f"Failed to hash file {self._filepath}") from e


class File(MetadataItem):
    metadata_updated = pyqtSignal()
    save_finished = pyqtSignal(bool)  # Emitted on the UI thread after the save callback finishes.

    NAME: str | None = None
    # Logical tag format key and description for the family of this handler.
    # Subclasses should override for format families (e.g. ID3, Vorbis, APEv2).
    FORMAT_KEY: str | None = None
    FORMAT_DESCRIPTION: str | None = None
    # Whether date sanitization can be toggled for this format family via settings
    DATE_SANITIZATION_TOGGLEABLE: bool = False

    EXTENSIONS: tuple[str, ...] = tuple()

    class State(Enum):
        UNDEFINED = -1
        PENDING = 0
        NORMAL = 1
        CHANGED = 2
        ERROR = 3
        REMOVED = 4

    class LookupType(Enum):
        METADATA = auto()
        ACOUSTID = auto()

    class ErrorType(Enum):
        UNKNOWN = auto()
        NOTFOUND = auto()
        NOACCESS = auto()
        PARSER = auto()

    class PreserveTimesStatError(Exception):
        pass

    class PreserveTimesUtimeError(Exception):
        pass

    # in order to significantly speed up performance, the number of pending
    # files is cached, set @state.setter
    num_pending_files = 0

    def __init__(self, filename: str):
        super().__init__()
        self.filename: str = filename
        self.base_filename: str = os.path.basename(filename)
        self._file_identity: FileIdentity | None = None
        self._state = File.State.UNDEFINED
        self.state = File.State.PENDING
        self.error_type: File.ErrorType = File.ErrorType.UNKNOWN

        self.similarity = 1.0
        self.parent_item = None

        self._lookup_task = None
        self._smart_scan_token = None
        self._bulk_scan_callback = None
        self.smart_scan_stage = None
        # Last Smart Scan result (picard.smartscan.Decision), for review/UI.
        self.smart_scan_decision: Decision | None = None
        self._smart_scan_acoustid_decision: Decision | None = None
        # Pre-match values that a Smart Scan match must not drop (see
        # picard.smartscan.is_protected_tag), and artwork to keep when the
        # release could not be verified. Only set for Smart Scan matches.
        self._smart_scan_protected: dict[str, list[str]] | None = None
        self._smart_scan_images: ImageList | None = None
        self._smart_scan_unverified_recording_id: str | None = None
        self._smart_scan_expected: Metadata | None = None
        self._manually_edited_tags: set[str] = set()
        self._tag_search_requests = {}
        # Discogs responses shared by Smart Genre and Find Tag for this file.
        self.dlmtags_discogs_responses = DiscogsResponses()
        # Album values offered after a Smart Scan whose release was not
        # verified (picard.tagsearch.TagCandidate). Never applied unless chosen.
        self.dlmtags_album_search: TagSearchRequest | None = None
        self.dlmtags_album_suggestions = []
        # Year ('date') a Smart Scan match keeps in New Tags: (recording MBID,
        # values). A different first-release date is only suggested, as
        # (date, source, url), never applied over a date already present.
        self._smart_scan_date: tuple[str, list[str]] | None = None
        self.dlmtags_date_suggestion: tuple[str, str, str] | None = None
        # Unverified Artist/Title when Smart Scan could not verify the
        # recording (picard.smartscan.TagSuggestion). Shown, never applied.
        self.dlmtags_tag_suggestions = {}
        self._credit_lookup_token = None
        self._credit_lookup_task = None
        # MusicBrainz recordings fetched for their artist credit, by MBID.
        self._dlmtags_credit_nodes = {}
        self._smart_genre_request: SmartGenreRequest | None = None
        self._smart_genre_recording_id: str | None = None
        self._smart_genre_applying = False
        self.dlmtags_tag_sources: dict[str, str] = {}
        self._dlmtags_source_values = {}
        self._dlmtags_source_recordings = {}
        self.dlmtags_tag_urls: dict[str, str] = {}
        self.smart_genre_source: str | None = None
        self.smart_genre_values: list[str] = []
        self.smart_genre_diagnostics: list[dict[str, str]] = []

        self.acoustid_fingerprint = None
        self.acoustid_length = 0
        self.match_recordingid = None

    @property
    def parent_item(self) -> 'Cluster | Track | None':
        if self._parent_item is None:
            return None
        return self._parent_item()

    @parent_item.setter
    def parent_item(self, value: 'Cluster | Track | None'):
        self._parent_item = weakref.ref(value) if value is not None else None

    def __repr__(self):
        return '<%s %r>' % (type(self).__name__, self.base_filename)

    # pylint: disable=no-self-use
    def format_specific_metadata(self, metadata, tag, settings=None):
        """Can be overridden to customize how a tag is displayed in the UI.
        This is useful if a tag saved to the underlying format will differ from
        the internal representation in a way that would cause data loss. This is e.g.
        the case for some ID3v2.3 tags.

        Args:
            metadata: The metadata object to read the tag from
            tag: Name of the tag
            settings: Dictionary of settings. If not set, config.setting should be used

        Returns:
            An array of values for the tag
        """
        return metadata.getall(tag)

    def _format_specific_copy(self, metadata, settings=None):
        """Creates a copy of metadata, but applies format_specific_metadata() to the values."""
        copy = Metadata(
            deleted_tags=metadata.deleted_tags,
            images=metadata.images,
            length=metadata.length,
        )
        for name in metadata:
            copy[name] = self.format_specific_metadata(metadata, name, settings)
        return copy

    def _set_error(self, error):
        """Set the file state to ERROR and record an appropriate message.

        This updates the internal error state and logs a human-readable message.
        It does not interrupt execution or provide UI feedback directly; callers
        are responsible for acting on the error state.
        """
        self.state = File.State.ERROR
        if any_exception_isinstance(error, FileNotFoundError):
            self.error_type = File.ErrorType.NOTFOUND
            self.error_append(_("Cannot save file: the file no longer exists."))

        elif any_exception_isinstance(error, PermissionError):
            self.error_type = File.ErrorType.NOACCESS
            self.error_append(_("Cannot save file: permission denied."))

        elif any_exception_isinstance(error, MutagenError):
            self.error_type = File.ErrorType.PARSER
            self.error_append(
                _("The file failed to parse, either the file is damaged or has an unsupported file format.")
            )
        else:
            self.error_type = File.ErrorType.UNKNOWN
            self.error_append(str(error))

    def load(self, callback):
        thread.run_task(
            partial(self._load_check, self.filename),
            partial(self._loading_finished, callback),
            priority=1,
        )

    def _load_check(self, filename):
        # Check that file has not been removed since thread was queued
        # Don't load if we are stopping.
        if self.state != File.State.PENDING:
            log.debug("File not loaded because it was removed: %r", self.filename)
            return None
        if self.tagger.stopping:
            log.debug("File not loaded because %s is stopping: %r", PICARD_APP_NAME, self.filename)
            return None
        return self._load(filename)

    def _load(self, filename: str) -> Metadata:
        """Load metadata from the file."""
        raise NotImplementedError

    def _loading_finished(self, callback, result=None, error=None):
        if self.state != File.State.PENDING or self.tagger.stopping:
            return
        config = get_config()
        if error is not None:
            self._set_error(error)

            # If loading failed, force format guessing and try loading again
            try:
                alternative_file = self.tagger.format_registry.guess_format(self.filename)
            except (FileNotFoundError, OSError):
                log.error("Guessing format of %s failed", self.filename, exc_info=True)
                alternative_file = None

            if alternative_file:
                # Do not retry reloading exactly the same file format
                if type(alternative_file) != type(self):  # pylint: disable=unidiomatic-typecheck # noqa: E721
                    log.debug("Loading %r failed, retrying as %r", self, alternative_file)
                    self.remove()
                    alternative_file.load(callback)
                    return
                else:
                    alternative_file.remove()  # cleanup unused File object

            file_name, file_extension = os.path.splitext(self.base_filename)
            if file_extension not in self.tagger.format_registry.supported_extensions():
                log.error("Unsupported media file %r wrongly loaded. Removing …", self)
                callback(self, remove_file=True)
                return
        else:
            self.clear_errors()
            self.state = self.State.NORMAL
            postprocessors = []
            if config.setting['guess_tracknumber_and_title']:
                postprocessors.append(self._guess_tracknumber_and_title)
            self._copy_loaded_metadata(result, postprocessors)

        self._file_identity = FileIdentity(self.filename)
        # use cached fingerprint from file metadata
        if not config.setting['ignore_existing_acoustid_fingerprints']:
            fingerprints = self.metadata.getall('acoustid_fingerprint')
            if fingerprints:
                self.set_acoustid_fingerprint(fingerprints[0])
        run_file_post_load_processors(self)
        callback(self)
        Filter.apply_filters()

    def _copy_loaded_metadata(self, metadata, postprocessors=None):
        metadata['~length'] = format_time(metadata.length)
        if postprocessors:
            for processor in postprocessors:
                processor(metadata)
        self.orig_metadata = metadata
        self.metadata.copy(metadata)

    def _guess_tracknumber_and_title(self, metadata):
        missing = {'tracknumber', 'title'} - set(metadata)
        if missing:
            guessed = tracknum_and_title_from_filename(self.base_filename)
            for m in missing:
                metadata[m] = getattr(guessed, m)

    def _copy_file_info_tags(self, to_metadata, from_metadata):
        for tag in file_info_tag_names():
            to_metadata[tag] = from_metadata[tag]

    def copy_metadata(self, metadata, preserve_deleted=True):
        if metadata is self.orig_metadata and self._smart_genre_applying:
            # Detaching the old Track is part of applying the new match.
            # It must not roll back suggestions already shown in New Tags.
            metadata = Metadata(self.metadata)
        reverting = metadata is self.orig_metadata
        manual_values = {}
        missing_values = {}
        if self._smart_scan_expected is not None and not reverting:
            self._remember_manual_changes()
            manual_values = {tag: list(self.metadata.getall(tag)) for tag in self._manually_edited_tags}
            missing_values = {
                tag: list(values)
                for tag, values in self.metadata.rawitems()
                if not tag.startswith('~') and not metadata.getall(tag)
            }
        elif reverting:
            self._smart_scan_expected = None
            self._manually_edited_tags.clear()
            self.dlmtags_tag_sources.clear()
            self._dlmtags_source_values.clear()
            self._dlmtags_source_recordings.clear()
            self.dlmtags_tag_urls.clear()
            self._smart_scan_date = None
            self.dlmtags_date_suggestion = None
        if self._smart_scan_date is not None and (
            metadata['musicbrainz_recordingid'] and metadata['musicbrainz_recordingid'] != self._smart_scan_date[0]
        ):
            # Another recording: its own date applies, not this match's year.
            self._smart_scan_date = None
            self.dlmtags_date_suggestion = None
        unverified = self._smart_scan_unverified_recording_id
        if (
            unverified
            and not self._smart_genre_applying
            and (
                metadata is self.orig_metadata
                or (metadata['musicbrainz_recordingid'] and metadata['musicbrainz_recordingid'] != unverified)
            )
        ):
            self._smart_scan_unverified_recording_id = None
            unverified = None
        release_values = {tag: list(self.metadata.getall(tag)) for tag in RELEASE_SPECIFIC_TAGS} if unverified else {}
        keep_genre = self._smart_genre_recording_id is not None
        if (
            keep_genre
            and not self._smart_genre_applying
            and (
                metadata is self.orig_metadata
                or (
                    metadata['musicbrainz_recordingid']
                    and metadata['musicbrainz_recordingid'] != self._smart_genre_recording_id
                )
            )
        ):
            self.cancel_smart_genre()
            self._smart_genre_recording_id = None
            keep_genre = False
        genre = list(self.metadata.getall('genre')) if keep_genre else None
        if self._smart_scan_images is not None and (metadata is not self.orig_metadata or self._smart_genre_applying):
            # Keep artwork edits made after the match as well as the snapshot.
            self._smart_scan_images = self.metadata.images.copy()
        saved_metadata = {}

        # Keep current value for special tags that got calculated from audio content
        for tag in calculated_tag_names():
            if tag not in metadata.deleted_tags and self.metadata[tag]:
                saved_metadata[tag] = self.metadata[tag]

        # Keep original values of preserved tags
        preserved_tags = UserPreservedTags()
        default_preserved_tags = set(preserved_tag_names())
        for tag, values in self.orig_metadata.rawitems():
            if tag in preserved_tags or tag in default_preserved_tags:
                saved_metadata[tag] = values
        deleted_tags = self.metadata.deleted_tags
        previous_images = self.metadata.images
        images_changed = previous_images != metadata.images
        self.metadata.copy(metadata)
        self._copy_file_info_tags(metadata, self.orig_metadata)
        if preserve_deleted:
            for tag in deleted_tags:
                del self.metadata[tag]
        self.metadata.update(saved_metadata)

        if metadata is self.orig_metadata and not self._smart_genre_applying:
            # Reverting to the file's own tags (file left its track): the
            # protection of a previous Smart Scan match no longer applies.
            self._smart_scan_protected = None
            self._smart_scan_images = None
        else:
            if self._smart_scan_protected:
                restore_protected(self.metadata, self._smart_scan_protected)
            if self._smart_scan_images is not None:
                self.metadata.images = self._smart_scan_images.copy()
                images_changed = previous_images != self.metadata.images

        for tag, values in release_values.items():
            self.metadata[tag] = values
            if tag not in deleted_tags:
                self.metadata.deleted_tags.discard(tag)

        if genre is not None:
            self.metadata['genre'] = genre
            # Suppressing an automatic album genre is not a user deletion.
            if 'genre' not in deleted_tags:
                self.metadata.deleted_tags.discard('genre')

        for tag, values in missing_values.items():
            if tag not in self.metadata.deleted_tags:
                self.metadata[tag] = values
        if self._smart_scan_date is not None and not reverting:
            # Neither a verified release (possibly a compilation) nor an
            # unverified one decides the Year; the first release does. A
            # loaded release may carry the recording's MusicBrainz first-release date.
            recording_first = normalize_release_date(metadata['~recording_firstreleasedate'])
            if not self._smart_scan_date[1] and recording_first and 'date' not in self._manually_edited_tags:
                self._smart_scan_date = (self._smart_scan_date[0], [recording_first])
                self.metadata['date'] = recording_first
                set_source(self, 'date', 'MusicBrainz', 'https://musicbrainz.org/recording/' + self._smart_scan_date[0])
            if self._smart_scan_date[1]:
                self.metadata['date'] = self._smart_scan_date[1]
            else:
                self.metadata.unset('date')
            if 'date' not in deleted_tags:
                self.metadata.deleted_tags.discard('date')
        for tag, values in manual_values.items():
            self.metadata[tag] = values
        if self._smart_scan_expected is not None:
            self._smart_scan_expected = Metadata(self.metadata)
        if images_changed:
            self.metadata_images_changed.emit()
        self.metadata_updated.emit()

    def _remember_manual_changes(self):
        if self._smart_scan_expected is None:
            return
        for tag in set(self.metadata) | set(self._smart_scan_expected):
            if not tag.startswith('~') and self.metadata.getall(tag) != self._smart_scan_expected.getall(tag):
                if self._dlmtags_source_values.get(tag) == tuple(self.metadata.getall(tag)):
                    continue
                self._manually_edited_tags.add(tag)
                set_source(self, tag, 'Manual')
        self._manually_edited_tags.update(self.metadata.deleted_tags)

    @property
    def tag_search_pending(self):
        return any(request.active for request in self._tag_search_requests.values())

    def cancel_tag_searches(self):
        for request in list(self._tag_search_requests.values()):
            request.cancel()

    def keep_original_images(self):
        if self.metadata.images != self.orig_metadata.images:
            self.metadata.images = self.orig_metadata.images.copy()
            self.update(signal=False)
            self.metadata_images_changed.emit()

    def has_error(self):
        return self.state == File.State.ERROR

    def save(self):
        self.set_pending()
        run_file_pre_save_processors(self)
        # On Windows, ask the internal player to release this file before
        # writing or moving it (PICARD-3290).
        if IS_WIN:
            self._release_file_from_player(self.filename)
        metadata = Metadata()
        metadata.copy(self.metadata)
        # Track in-flight saves so _saving_finished can coalesce the expensive
        # selection/metadata-box refresh to once per batch (see PICARD
        # batch-save memory profiling).
        self.tagger._saving_files_count += 1
        thread.run_task(
            partial(self._save_and_rename, self.filename, metadata),
            self._saving_finished,
            thread_pool=self.tagger.save_thread_pool,
        )

    def _preserve_times(self, filename, func):
        """Save filename times before calling func, and set them again"""
        try:
            # https://docs.python.org/3/library/os.html#os.utime
            # Since Python 3.3, ns parameter is available
            # The best way to preserve exact times is to use the st_atime_ns and st_mtime_ns
            # fields from the os.stat() result object with the ns parameter to utime.
            st = os.stat(filename)
        except OSError as why:
            errmsg = "Couldn't read timestamps from %r: %s" % (filename, why)
            raise self.PreserveTimesStatError(errmsg) from None
            # if we can't read original times, don't call func and let caller handle this
        func()
        try:
            os.utime(filename, ns=(st.st_atime_ns, st.st_mtime_ns))
        except OSError as why:
            errmsg = "Couldn't preserve timestamps for %r: %s" % (filename, why)
            raise self.PreserveTimesUtimeError(errmsg) from None
        return (st.st_atime_ns, st.st_mtime_ns)

    _PERMISSION_ERROR_RETRIES = 3
    _PERMISSION_ERROR_RETRY_DELAY = 1.0  # seconds

    def _retry_on_permission_error(self, func: 'Callable') -> None:
        """Call func, retrying on PermissionError (Windows file locking).

        This is safe to call from a worker thread.  On non-Windows platforms
        or when retries are exhausted, the exception propagates normally.
        """
        if not IS_WIN:
            return func()
        for attempt in range(1, self._PERMISSION_ERROR_RETRIES + 1):
            try:
                return func()
            except PermissionError:
                if attempt >= self._PERMISSION_ERROR_RETRIES:
                    raise
                log.warning(
                    "Permission denied on %r, retrying (%d/%d)…",
                    self.filename,
                    attempt,
                    self._PERMISSION_ERROR_RETRIES,
                )
                time.sleep(self._PERMISSION_ERROR_RETRY_DELAY)

    def _release_file_from_player(self, filename: str) -> None:
        """Ask the internal player to release the file handle if it holds it.

        Must be called from the main thread (e.g. from save()).
        """
        player = self.tagger.window.player
        if player:
            player.release_file(filename)

    def _save_and_rename(self, old_filename, metadata):
        """Save the metadata."""
        config = get_config()
        # Check that file has not been removed since thread was queued
        # Also don't save if we are stopping.
        if self.state == File.State.REMOVED:
            log.debug("File not saved because it was removed: %r", self.filename)
            return None
        if self.tagger.stopping:
            log.debug("File not saved because %s is stopping: %r", PICARD_APP_NAME, self.filename)
            return None
        new_filename = old_filename
        if config.setting['enable_tag_saving']:
            # Detect source changes before saving (debug only)
            current = FileIdentity(old_filename)
            try:
                if current and current != self._file_identity:
                    log.warning("File externally modified: %r", old_filename)
                elif not current:
                    log.warning("File missing: %r", old_filename)
            except FileIdentityError:
                # Comparing identities may fail if the file became unreadable.
                # This is only a diagnostic check and must never abort the save.
                log.warning("Could not verify whether file was externally modified: %r", old_filename, exc_info=True)
            save = partial(self._save, old_filename, metadata)
            if config.setting['preserve_timestamps']:
                try:
                    self._retry_on_permission_error(partial(self._preserve_times, old_filename, save))
                except self.PreserveTimesUtimeError as why:
                    log.warning(why)
            else:
                self._retry_on_permission_error(save)
        # Rename files
        if config.setting['rename_files'] or config.setting['move_files']:
            new_filename = self._rename(old_filename, metadata, config.setting)
        # Move extra files (images, playlists, etc.)
        self._move_additional_files(old_filename, new_filename, config)
        # Delete empty directories
        if config.setting['delete_empty_dirs']:
            dirname = os.path.dirname(old_filename)
            try:
                emptydir.rm_empty_dir(dirname)
                head, tail = os.path.split(dirname)
                if not tail:
                    head, tail = os.path.split(head)
                while head and tail:
                    emptydir.rm_empty_dir(head)
                    head, tail = os.path.split(head)
            except OSError as why:
                log.warning("Error removing directory: %s", why)
            except emptydir.SkipRemoveDir as why:
                log.debug("Not removing empty directory: %s", why)
        # Save cover art images
        if config.setting['save_images_to_files']:
            self._save_images(os.path.dirname(new_filename), metadata)
        return new_filename

    def _expected_embedded_images(self) -> 'ImageList':
        """Images that should end up embedded in tags for the current metadata.

        Mirrors the rules applied when actually saving (see ImageList): per-file
        "never replace" filters may reject a new image for tags, or
        remove_images_from_tags may clear tags entirely while the image is kept
        only as an external file (and, for the CoverArtBox, in self.metadata).
        """
        images_to_save = list(self.metadata.images.to_be_saved_to_tags(previous_images=self.orig_metadata.images))
        if images_to_save:
            return ImageList(images_to_save)
        elif self.metadata.images.should_remove_images_from_tags(previous_images=self.orig_metadata.images):
            return ImageList()
        else:
            return self.orig_metadata.images.copy()

    def _saving_finished(self, result=None, error=None):
        # This save task has finished; decrement the in-flight counter so the
        # selection/metadata-box refresh can be coalesced to run only once,
        # when the last file of a batch save completes.
        if self.tagger._saving_files_count > 0:
            self.tagger._saving_files_count -= 1
        batch_done = self.tagger._saving_files_count == 0
        # Handle file removed before save
        # Result is None if save was skipped
        if (self.state == File.State.REMOVED or self.tagger.stopping) and result is None:
            # Still flush any deferred image aggregation if this was the last
            # file of the batch, so earlier files' parents are not left stale.
            if batch_done:
                self.tagger.flush_saving_image_parents()
            self.save_finished.emit(False)
            return
        old_filename = new_filename = self.filename
        if error is not None:
            self._set_error(error)
        else:
            self.filename = new_filename = resolve_fs_path(result)
            self.base_filename = os.path.basename(new_filename)
            length = self.orig_metadata.length
            temp_info = {}
            self._copy_file_info_tags(temp_info, self.orig_metadata)
            embedded_images = self._expected_embedded_images()
            images_changed = self.orig_metadata.images != embedded_images
            # Copy new metadata to original metadata, applying format specific
            # conversions (e.g. for ID3v2.3)
            config = get_config()
            new_metadata = self._format_specific_copy(self.metadata, config.setting)
            if config.setting['clear_existing_tags']:
                self.orig_metadata = new_metadata
            else:
                self.orig_metadata.update(new_metadata)
            # Metadata.update() (and the plain replace above) leave images
            # untouched when the new list is empty, but an empty result here is
            # exactly what "removed from tags" means, so set it explicitly.
            self.orig_metadata.images = embedded_images
            # After saving deleted tags should no longer be marked deleted
            self.metadata.clear_deleted()
            self.orig_metadata.clear_deleted()
            self.orig_metadata.length = length
            self.orig_metadata['~length'] = format_time(length)
            self.orig_metadata.update(temp_info)
            self.clear_errors()
            self.clear_pending(signal=False)
            self._update_filesystem_metadata(self.orig_metadata)
            if images_changed:
                if batch_done:
                    self.metadata_images_changed.emit()
                else:
                    # During a batch save, defer cover-art re-aggregation:
                    # emitting here would make each parent container rebuild
                    # its image list over all children once per saved file
                    # (O(N^2)). Record the parents as dirty and rebuild each
                    # once when the batch finishes (see _flush_saving_image_parents).
                    self._defer_metadata_images_update()
            # run post save hook
            run_file_post_save_processors(self)

        self._file_identity = FileIdentity(self.filename)
        # Force update to ensure file status icon changes immediately after
        # save. During a batch save only refresh the (expensive)
        # selection-wide tag diff once, when the last file finishes; the
        # per-file item icon/text is always updated.
        self.update(update_selection=batch_done)

        if self.state != File.State.REMOVED:
            del self.tagger.files[old_filename]
            self.tagger.files[new_filename] = self

        if batch_done:
            self.tagger.flush_saving_image_parents()

        self.save_finished.emit(error is None and result is not None)

        if self.tagger.stopping:
            log.debug("Save of %r completed before stopping Picard", self.filename)

    def _defer_metadata_images_update(self):
        """Record this file's parent container(s) for a deferred, coalesced
        cover-art image re-aggregation at the end of a batch save.

        Instead of emitting metadata_images_changed now (which would make each
        parent rebuild its image list over all children), the parent chain is
        collected on the tagger and refreshed once when the batch finishes.
        """
        parent = self.parent_item
        while parent is not None:
            self.tagger._saving_dirty_image_parents.add(parent)
            # Both Cluster and Track expose the containing album via .album
            # (None when there is none), so the chain ends at the album.
            parent = getattr(parent, 'album', None)

    def _save(self, filename: str, metadata: Metadata) -> None:
        """Save the metadata."""
        raise NotImplementedError

    def _script_to_filename(self, naming_format, file_metadata, file_extension, settings=None):
        if settings is None:
            config = get_config()
            settings = config.setting
        metadata = Metadata()
        if settings['clear_existing_tags']:
            # script_to_filename_with_metadata guarantees this is not modified
            metadata = file_metadata
        else:
            metadata.copy(self.orig_metadata)
            metadata.update(file_metadata)
        (filename, new_metadata) = script_to_filename_with_metadata(
            naming_format, metadata, file=self, settings=settings
        )
        basename = os.path.basename(filename)
        if not basename:
            old_name = os.path.splitext(os.path.basename(self.filename))[0]
            filename = os.path.join(os.path.dirname(filename), old_name)
        # NOTE: the filename generated by the naming script does not have a file extension
        ext = new_metadata.get('~extension', file_extension)
        return filename + '.' + ext.lstrip('.')

    def _fixed_splitext(self, filename):
        # In case the filename is blank and only has the extension
        # the real extension is in new_filename and ext is blank
        new_filename, ext = os.path.splitext(filename)
        if ext == '' and new_filename.lower() in self.EXTENSIONS:
            ext = new_filename
            new_filename = ''
        return new_filename, ext

    def _clean_file_extension(self, filename):
        """Takes a filename and converts the extension to lowercase.

        If the file has no extension a default extension for the format is used.

        Args:
            filename: The filename

        Returns: A tuple containing the filename with fixed extension and the extension itself.
        """
        filename, ext = self._fixed_splitext(filename)
        if not ext and self.EXTENSIONS:
            ext = self.EXTENSIONS[0]
        ext = ext.lower()
        return (filename + ext, ext)

    def _format_filename(self, new_dirname, new_filename, metadata, settings, naming_format):
        old_filename = new_filename
        new_filename, ext = self._clean_file_extension(new_filename)

        if naming_format:
            new_filename = self._script_to_filename(naming_format, metadata, ext, settings)
            if not settings['rename_files']:
                new_filename = os.path.join(os.path.dirname(new_filename), old_filename)
            if not settings['move_files']:
                new_filename = os.path.basename(new_filename)
            win_compat = IS_WIN or settings['windows_compatibility']
            win_shorten_path = win_compat and not settings['windows_long_paths']
            new_filename = make_short_filename(new_dirname, new_filename, win_shorten_path=win_shorten_path)
            new_filename = make_save_path(new_filename, win_compat=win_compat, mac_compat=IS_MACOS)
        return new_filename

    def make_filename(self, filename, metadata, settings=None, naming_format=None):
        """Constructs file name based on metadata and file naming formats."""
        if settings is None:
            config = get_config()
            settings = config.setting
        if naming_format is None:
            naming_format = get_file_naming_script(settings)
        if settings['move_files']:
            new_dirname = settings['move_files_to']
            if not is_absolute_path(new_dirname):
                new_dirname = os.path.join(os.path.dirname(filename), new_dirname)
        else:
            new_dirname = os.path.dirname(filename)
        new_filename = os.path.basename(filename)

        if settings['rename_files'] or settings['move_files']:
            new_filename = self._format_filename(new_dirname, new_filename, metadata, settings, naming_format)

        new_path = os.path.join(new_dirname, new_filename)
        return normpath(new_path, realpath=False)

    def _rename(self, old_filename, metadata, settings):
        new_filename = self.make_filename(old_filename, metadata, settings)
        if old_filename == new_filename:
            return old_filename

        new_dirname = os.path.dirname(new_filename)
        os.makedirs(new_dirname, exist_ok=True)
        if not settings['move_overwrite_existing_files']:
            new_filename = get_available_filename(new_filename, old_filename)
        log.debug("Moving file %r => %r", old_filename, new_filename)
        self._move_with_retry(old_filename, new_filename)
        return new_filename

    def _move_with_retry(self, old_filename: str, new_filename: str) -> None:
        """Move a file, retrying on PermissionError (Windows).

        On cross-drive moves, shutil.move copies then deletes. If the delete
        fails (source locked), a copy is left at the destination. Clean it up
        before retrying.
        """
        if not IS_WIN:
            move_ensure_casing(old_filename, new_filename)
            return
        for attempt in range(1, self._PERMISSION_ERROR_RETRIES + 1):
            try:
                move_ensure_casing(old_filename, new_filename)
                return
            except PermissionError:
                if attempt >= self._PERMISSION_ERROR_RETRIES:
                    raise
                # Clean up partial copy at destination from failed cross-drive move
                if (
                    os.path.exists(new_filename)
                    and os.path.exists(old_filename)
                    and not samefile(old_filename, new_filename)
                ):
                    os.remove(new_filename)
                log.warning(
                    "Permission denied on %r, retrying (%d/%d)…",
                    old_filename,
                    attempt,
                    self._PERMISSION_ERROR_RETRIES,
                )
                time.sleep(self._PERMISSION_ERROR_RETRY_DELAY)

    def _save_images(self, dirname, metadata):
        """Save the cover images to disk."""
        counters = Counter()
        for image in metadata.images.to_be_saved_to_files(previous_images=self.orig_metadata.images):
            image.save(dirname, metadata, counters)

    def _move_additional_files(self, old_filename, new_filename, config):
        """Move extra files, like images, playlists…"""
        if config.setting['move_files'] and config.setting['move_additional_files']:
            new_path = os.path.dirname(new_filename)
            old_path = os.path.dirname(old_filename)
            if new_path != old_path:
                patterns_string = config.setting['move_additional_files_pattern']
                patterns = self._compile_move_additional_files_pattern(patterns_string)
                try:
                    moves = self._get_additional_files_moves(old_path, new_path, patterns)
                    self._apply_additional_files_moves(moves, config.setting['move_overwrite_existing_files'])
                except OSError as why:
                    log.error("Failed to scan %r: %s", old_path, why)

    @staticmethod
    def _compile_move_additional_files_pattern(patterns_string):
        return {
            (re.compile(fnmatch.translate(pattern), re.IGNORECASE), pattern.startswith('.'))
            for pattern in set(patterns_string.lower().split())
        }

    def _get_additional_files_moves(self, old_path, new_path, patterns):
        if patterns:
            with os.scandir(old_path) as scan:
                for entry in scan:
                    is_hidden = entry.name.startswith('.')
                    for pattern_regex, match_hidden in patterns:
                        if is_hidden and not match_hidden:
                            continue
                        if pattern_regex.match(entry.name):
                            new_file_path = os.path.join(new_path, entry.name)
                            yield (entry.path, new_file_path)
                            break  # we are done with this file

    def _apply_additional_files_moves(self, moves, overwrite_existing_files=False):
        for old_file_path, new_file_path in moves:
            # FIXME we shouldn't do this from a thread!
            if self.tagger.files.get(old_file_path):
                log.debug("File loaded in the tagger, not moving %r", old_file_path)
                continue
            if not overwrite_existing_files and os.path.exists(new_file_path):
                log.warning("File %r already exists, not moving %r", new_file_path, old_file_path)
                continue
            log.debug("Moving %r to %r", old_file_path, new_file_path)
            try:
                shutil.move(old_file_path, new_file_path)
            except OSError as why:
                log.error("Failed to move %r to %r: %s", old_file_path, new_file_path, why)

    def remove(self, from_parent_item=True):
        if from_parent_item and self.parent_item:
            log.debug("Removing %r from %r", self, self.parent_item)
            self.parent_item.remove_file(self)
        self.tagger.acoustidmanager.remove(self)
        self.tagger.isrc_submit_manager.remove(self)
        self.cancel_smart_genre()
        self.cancel_tag_searches()
        self.clear_lookup_task()
        self.state = File.State.REMOVED

    def move(self, to_parent_item):
        # To be able to move a file the target must implement add_file(file)
        if hasattr(to_parent_item, 'add_file') and to_parent_item != self.parent_item:
            log.debug("Moving %r from %r to %r", self, self.parent_item, to_parent_item)
            self.clear_lookup_task()
            self.tagger._acoustid.stop_analyze(self)
            new_album = True
            if self.parent_item:
                new_album = self.parent_item.album != to_parent_item.album
                self.clear_pending()
                self.parent_item.remove_file(self, new_album=new_album)
            self.parent_item = to_parent_item
            if self.parent_item:
                self.parent_item.add_file(self, new_album=new_album)
            self.acoustid_update()
            self.isrc_update()
            return True
        else:
            return False

    def _move(self, to_parent_item):
        if to_parent_item != self.parent_item:
            log.debug("Moving %r from %r to %r", self, self.parent_item, to_parent_item)
            if self.parent_item:
                self.parent_item.remove_file(self)
            self.parent_item = to_parent_item
            self.acoustid_update()
            self.isrc_update()

    def set_acoustid_fingerprint(self, fingerprint, length=None):
        if not fingerprint:
            self.acoustid_fingerprint = None
            self.acoustid_length = 0
            self.tagger.acoustidmanager.remove(self)
        elif fingerprint != self.acoustid_fingerprint:
            self.acoustid_fingerprint = fingerprint
            self.acoustid_length = length or self.metadata.length // 1000
            self.tagger.acoustidmanager.add(self, None)
            self.acoustid_update()
        config = get_config()
        if config.setting['save_acoustid_fingerprints']:
            self.metadata['acoustid_fingerprint'] = fingerprint

    def acoustid_update(self):
        recording_id = None
        if self.parent_item and self.parent_item.can_link_fingerprint:
            recording_id = self.parent_item.orig_metadata['musicbrainz_recordingid']
            if not recording_id:
                recording_id = self.metadata['musicbrainz_recordingid']
        self.tagger.acoustidmanager.update(self, recording_id)
        self.update_item()

    def isrc_update(self):
        """Update ISRC submission state based on current file-to-track match."""
        if not self.parent_item or not self.parent_item.can_link_fingerprint:
            self.tagger.isrc_submit_manager.remove(self)
            return
        recording_id = self.parent_item.orig_metadata['musicbrainz_recordingid']
        if not recording_id:
            return
        file_isrcs = self.orig_metadata.getall('isrc')
        if not file_isrcs:
            return
        mb_isrcs = self.parent_item.orig_metadata.getall('isrc')
        if len(file_isrcs) > 1:
            log.debug(
                "ISRC submission: %r has %d ISRCs, checking for new ones against MB",
                self,
                len(file_isrcs),
            )
        log.debug_if(
            DebugOpt.ISRC,
            "ISRC isrc_update: %r, recording=%s, file_isrcs=%r, mb_isrcs=%r",
            self,
            recording_id,
            file_isrcs,
            mb_isrcs,
        )
        self.tagger.isrc_submit_manager.add(self, recording_id, file_isrcs, mb_isrcs)

    @classmethod
    def supports_tag(cls, name: str) -> bool:
        """Returns whether tag ``name`` can be saved to the file."""
        return True

    def is_saved(self) -> bool:
        return self.similarity == 1.0 and self.state == File.State.NORMAL

    def _tags_to_update(self, ignored_tags):
        for name in set(self.metadata) | set(self.orig_metadata):
            if name.startswith('~'):
                continue
            if name in ignored_tags:
                continue
            if not self.supports_tag(name):
                continue
            yield name

    def update(self, signal=True, update_selection=True):
        if not (self.state == File.State.ERROR and self.errors):
            config = get_config()
            clear_existing_tags = config.setting['clear_existing_tags']
            ignored_tags = set(config.setting['compare_ignore_tags'])

            for name in self._tags_to_update(ignored_tags):
                new_values = self.format_specific_metadata(self.metadata, name, config.setting)
                if not (new_values or clear_existing_tags or name in self.metadata.deleted_tags):
                    continue
                orig_values = self.orig_metadata.getall(name)
                if orig_values != new_values:
                    self.similarity = self.orig_metadata.compare(self.metadata, ignored_tags)
                    if self.state == File.State.NORMAL:
                        self.state = File.State.CHANGED
                    break
            else:
                self.similarity = 1.0
                if self.state in {File.State.CHANGED, File.State.NORMAL}:
                    if self.metadata.images and self.orig_metadata.images != self._expected_embedded_images():
                        self.state = File.State.CHANGED
                    else:
                        self.state = File.State.NORMAL
        if signal:
            log.debug("Updating file %r", self)
            self.update_item(update_selection=update_selection)

    @property
    def can_save(self) -> bool:
        """Return if this object can be saved."""
        return True

    @property
    def can_remove(self) -> bool:
        """Return if this object can be removed."""
        return True

    @property
    def can_edit_tags(self) -> bool:
        """Return if this object supports tag editing."""
        return True

    @property
    def can_analyze(self) -> bool:
        """Return if this object can be fingerprinted."""
        return True

    @property
    def can_autotag(self) -> bool:
        return True

    @property
    def can_refresh(self) -> bool:
        return False

    @property
    def can_view_info(self) -> bool:
        return True

    def _info(self, metadata: Metadata, file: FileType):
        if (length := getattr(file.info, 'length', None)) is not None:
            metadata.length = int(length * 1000)
        if bitrate := getattr(file.info, 'bitrate', None):
            metadata['~bitrate'] = bitrate / 1000.0
        if sample_rate := getattr(file.info, 'sample_rate', None):
            metadata['~sample_rate'] = sample_rate
        if channels := getattr(file.info, 'channels', None):
            metadata['~channels'] = channels
        if bits_per_sample := getattr(file.info, 'bits_per_sample', None):
            metadata['~bits_per_sample'] = bits_per_sample
        if self.NAME:
            metadata['~format'] = self.NAME
        else:
            metadata['~format'] = self.__class__.__name__.replace('File', '')
        self._update_filesystem_metadata(metadata)

    @classmethod
    def score(cls, filename: str, fileobj: IO[bytes], header_data: bytes) -> int:
        """Used to guess if the provided file matches this file format.

        If Picard needs to choose between multiple file formats it uses the returned
        score to select one. The score is an integer, and typically implementations
        check if the file extension matches and whether there are some expected
        magic bytes in the header data.

        When implementing a new file format by sub-classing `File`:

        1. If the new format is based on the mutagen library, set the class `_File`
           attribute to the mutagen file type class being used. You don't need to
           override this score method then. The default implementation will use the
           `score` method in the mutagen file type class.
        2. If you are implementing a file format without using mutagen, the default
           implementation of `score` will only check whether the file extension of the
           passed `filename` matches any extension in the class `EXTENSION` attribute.
           For better matching you can override `score`. A typical implementation will
           check for matching file extension and some magic bytes in the provided
           header_data.

        Example:

            @classmethod
            def score(cls, filename: str, fileobj: FileIO, header_data: bytes) -> int:
                filename = filename.lower()
                return return header.startswith(b"qoaf") + filename.lower().endswith(".qoa")

        Args:
            filename: Path to the file to check.
            fileobj: An open file handle for reading from the file.
            header_data: This will be passed some bytes form the file header. By
              default Picard passed in the first 128 bytes of data.
        """
        mutagen_file = getattr(cls, '_File', None)
        if mutagen_file and hasattr(mutagen_file, 'score'):
            return mutagen_file.score(str(filename), fileobj, header_data)
        else:
            _name, ext = os.path.splitext(filename)
            return ext.lower() in cls.EXTENSIONS

    def _update_filesystem_metadata(self, metadata):
        metadata['~filepath'] = self.filename
        metadata['~dirname'] = os.path.dirname(self.filename)
        filename_no_ext, extension = os.path.splitext(os.path.basename(self.filename))
        metadata['~filename'] = filename_no_ext
        metadata['~extension'] = extension.lower()[1:]

        filename_encoded = self.filename
        try:
            metadata['~filesize'] = os.path.getsize(filename_encoded)

            created = os.path.getctime(filename_encoded)
            created_timestamp = time.strftime(DEFAULT_TIME_FORMAT, time.localtime(created))
            metadata['~file_created_timestamp'] = created_timestamp

            modified = os.path.getmtime(filename_encoded)
            modified_timestamp = time.strftime(DEFAULT_TIME_FORMAT, time.localtime(modified))
            metadata['~file_modified_timestamp'] = modified_timestamp
        except OSError as ex:
            log.error(f"File access error: {ex}")

    @property
    def state(self) -> 'File.State':
        """Current state of the File object"""
        return self._state

    @state.setter
    def state(self, state: 'File.State') -> None:
        if state == self._state:
            return
        if state == File.State.PENDING:
            File.num_pending_files += 1
            self.tagger.tagger_stats_changed.emit()
        elif self._state == File.State.PENDING:
            File.num_pending_files -= 1
            self.tagger.tagger_stats_changed.emit()
        self._state = state

    def column(self, column: str) -> str:
        value = super().column(column)
        if column == 'title' and not value:
            return self.base_filename
        if not value and not get_config().setting['clear_existing_tags']:
            value = self.orig_metadata[column]
        elif tagvar := ALL_TAGS.tagvar_from_name(column):
            if tagvar and tagvar.is_file_info:
                value = self.orig_metadata[column]
        if column == '~filesize':
            try:
                value = bytes2human.binary(value)
            except ValueError:
                pass
        elif column == '~bitrate':
            try:
                if value:
                    value = f"{float(value):.0f} kbps"
            except (ValueError, TypeError):
                pass
        return value

    # --- Smart Scan (DLMtags) ---------------------------------------------
    #
    # Scan = AcoustID fingerprint lookup, then at most one metadata Lookup if
    # the fingerprint result is not usable. Each stage result is judged by
    # picard.smartscan.decide(); only a MATCHED decision changes New Tags.
    # Nothing is ever written to disk here.

    def start_smart_scan(self, *, bulk_callback=None):
        """Run AcoustID once, followed by metadata lookup only if unmatched."""
        if (
            self._smart_scan_token is not None
            or self._lookup_task
            or self.state
            in {
                File.State.PENDING,
                File.State.REMOVED,
            }
        ):
            return False
        self._bulk_scan_callback = bulk_callback
        if bulk_callback is None:
            self.cancel_smart_genre()
            self.cancel_tag_searches()
            if self._smart_scan_expected is None:
                self._manually_edited_tags.update(
                    tag
                    for tag in set(self.metadata) | set(self.orig_metadata)
                    if not tag.startswith('~') and self.metadata.getall(tag) != self.orig_metadata.getall(tag)
                )
            else:
                self._remember_manual_changes()
            self._manually_edited_tags.update(self.metadata.deleted_tags)
            self._smart_scan_expected = Metadata(self.metadata)
            self._smart_scan_date = None
            self.dlmtags_date_suggestion = None
            self.dlmtags_album_search = None
            self.dlmtags_album_suggestions = []
            self.dlmtags_tag_suggestions = {}
            self.cancel_credit_lookup()
            self._dlmtags_credit_nodes = {}
        token = self._smart_scan_token = object()
        self.smart_scan_stage = 'scan'
        if bulk_callback is None:
            self.smart_scan_decision = None
        self._smart_scan_acoustid_decision = None
        self.set_pending()
        self.tagger._acoustid.analyze(
            self,
            partial(self._smart_scan_finished, token, File.LookupType.ACOUSTID),
            set_unmatched_acoustid=False,
        )
        return True

    def _smart_scan_finished(self, token, lookuptype, document, http, error):
        # Ignore callbacks from cancelled or superseded operations.
        expected_stage = 'scan' if lookuptype == File.LookupType.ACOUSTID else 'lookup'
        if token is not self._smart_scan_token or self.smart_scan_stage != expected_stage:
            return
        self._lookup_task = None
        if self.state == File.State.REMOVED:
            return
        http_status = http.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) if http is not None else None
        if http_status == 503 and error != QNetworkReply.NetworkError.OperationCanceledError:
            error = QNetworkReply.NetworkError.ServiceUnavailableError
            log.warning(
                'Smart Scan: temporary HTTP 503 during %s for %r; recording conflicts were not evaluated',
                lookuptype.name.lower(),
                self.filename,
            )
        if error:
            log.error(
                "Smart Scan: network error during the %s lookup for %s. Error code: %s",
                lookuptype.name.lower(),
                self.filename,
                error,
            )

        source = Source.ACOUSTID if lookuptype == File.LookupType.ACOUSTID else Source.METADATA
        config = get_config()
        thresholds = SmartScanThresholds.from_config(config)
        decision = decide_smart_scan(
            source,
            document,
            error,
            self.metadata,
            self.base_filename,
            FILE_COMPARISON_WEIGHTS,
            thresholds=thresholds,
            config=config,
            cancelled=error == QNetworkReply.NetworkError.OperationCanceledError,
        )
        if http_status == 503 and decision.outcome == Outcome.ERROR:
            decision.details.update(http_status=503, temporary=True)
        self._log_smart_scan_decision(decision)

        if source == Source.ACOUSTID:
            if metadata_lookup_useful(decision, file_hints(self.metadata, self.base_filename)):
                # The fingerprint gave no usable result, or its recording
                # disagrees with the file's artist/title: one metadata Lookup.
                # A weak fingerprint candidate or the review is kept on the
                # decision and weighed again in combine().
                self._smart_scan_acoustid_decision = decision
                self.smart_scan_stage = 'lookup'
                self._smart_scan_lookup_metadata()
                return
        else:
            decision = combine_smart_scan_decisions(self._smart_scan_acoustid_decision, decision, thresholds)
            if decision.outcome != Outcome.CANCELLED:
                self._log_smart_scan_decision(decision, final=True)
        self._finish_smart_scan(decision)

    def _smart_scan_lookup_metadata(self):
        """The single metadata Lookup of a Smart Scan.

        Uses filename-derived artist/title only where those tags are missing.
        """
        hints = file_hints(self.metadata, self.base_filename)
        metadata = self.metadata
        config = get_config()
        self.tagger.window.set_statusbar_message(
            N_("Looking up the metadata for file %(filename)s …"),
            {'filename': self.filename},
        )
        if hints.filename_text:
            # There is no trustworthy artist/title boundary yet. Use Picard's
            # plain text search; verify each result against its returned credits.
            self._lookup_task = self.tagger.mb_api.find_tracks(
                partial(self._smart_scan_finished, self._smart_scan_token, File.LookupType.METADATA),
                search=True,
                advanced_search=False,
                query=hints.filename_text,
                limit=config.setting['query_limit'],
            )
            return
        self._lookup_task = self.tagger.mb_api.find_tracks(
            partial(self._smart_scan_finished, self._smart_scan_token, File.LookupType.METADATA),
            track=hints.title or metadata['title'],
            artist=hints.artist or metadata['artist'],
            # Release tags can be stale; they must not restrict recording search.
            release='',
            tnum='',
            tracks='',
            qdur=str(metadata.length // 2000),
            isrc=metadata['isrc'],
            limit=config.setting['query_limit'],
        )

    def _finish_smart_scan(self, decision):
        self._smart_scan_token = None
        self.smart_scan_stage = None
        self._smart_scan_acoustid_decision = None
        bulk_callback = self._bulk_scan_callback
        self._bulk_scan_callback = None
        if bulk_callback is not None:
            if decision.outcome == Outcome.NEEDS_REVIEW:
                # Keep the scan's real candidates for the existing individual
                # Confirm Results workflow. This does not alter New Tags.
                self.cancel_credit_lookup()
                self._dlmtags_credit_nodes = {}
                self._smart_scan_expected = Metadata(self.metadata)
                self._manually_edited_tags.update(
                    tag for tag in {'title', 'artist'} if self.metadata.getall(tag) != self.orig_metadata.getall(tag)
                )
                self._manually_edited_tags.update(self.metadata.deleted_tags)
                self.smart_scan_decision = decision
                recording_id = self._set_tag_suggestions(decision)
                if recording_id:
                    self._start_credit_lookup(decision, recording_id)
            self.clear_pending()
            bulk_callback(self, decision)
            return
        self.smart_scan_decision = decision
        if decision.outcome not in {Outcome.MATCHED, Outcome.CANCELLED}:
            # Unverified recording: Artist/Title are offered, not applied.
            recording_id = self._set_tag_suggestions(decision)
            if recording_id:
                self._start_credit_lookup(decision, recording_id)
        if decision.outcome == Outcome.MATCHED:
            self._smart_genre_recording_id = decision.recording_id
            self._smart_genre_applying = True
            try:
                self._apply_smart_scan_match(decision)
            finally:
                self._smart_genre_applying = False
            self._start_smart_genre(decision)
        # Needs Review / No Match / Error: New Tags are left exactly as they are.
        self.tagger.window.set_statusbar_message(
            N_('Smart Scan "%(filename)s": %(decision)s'),
            {'filename': self.filename, 'decision': decision.describe()},
            timeout=3000,
        )
        self.clear_pending()
        self._start_smart_album_search(decision)

    def _start_smart_album_search(self, decision):
        # Second pass is suggestions-only, using the existing providers.
        if decision.outcome != Outcome.MATCHED or decision.release_verified or self.state == File.State.REMOVED:
            return

        request = TagSearchRequest(self, 'album')
        self.dlmtags_album_search = request

        def completed(candidates, message):
            if (
                self.state == File.State.REMOVED
                or self.smart_scan_decision is not decision
                or self.dlmtags_album_search is not request
                or not request.current()
            ):
                return
            # Keep the real candidates: choosing one applies it through the
            # same request, with its provenance and stale-result checks.
            self.dlmtags_album_suggestions = [candidate for candidate in candidates if candidate.values]
            self.tagger.tagger_stats_changed.emit()

        request.completed.connect(completed)
        request.start()

    # --- Confirm Results (DLMtags) ----------------------------------------
    #
    # After Needs Review the user may confirm one of the scan's recordings.
    # It becomes a Matched decision marked user_confirmed: Artist/Title are
    # applied to New Tags as for a match, the remaining tags are looked up
    # with the existing album, genre and artwork mechanisms. Nothing is saved.

    def confirmable_recordings(self):
        if self._smart_scan_token is not None:
            return []
        return confirmable_recordings(self.smart_scan_decision, self._dlmtags_credit_nodes)

    def manual_identity_edits(self):
        """Artist/Title the user edited since the scan; a confirmation keeps them."""
        self._remember_manual_changes()
        return [tag for tag in ('artist', 'title') if tag in self._manually_edited_tags]

    def confirm_recording(self, decision, recording_id, *, replace_manual=False):
        """Confirm a recording of this file's current review; False if stale."""
        if (
            decision is not self.smart_scan_decision
            or self._smart_scan_token is not None
            or self._smart_scan_expected is None
            or self.state in {File.State.PENDING, File.State.REMOVED}
        ):
            return False
        confirmed = confirmed_decision(
            decision,
            recording_id,
            self.metadata,
            self.base_filename,
            FILE_COMPARISON_WEIGHTS,
            self._dlmtags_credit_nodes,
        )
        if confirmed is None:
            return False
        self.cancel_credit_lookup()
        self._remember_manual_changes()
        if replace_manual:
            # Approved by the user: Artist/Title are no longer manual edits.
            for tag in ('artist', 'title'):
                self._manually_edited_tags.discard(tag)
                self._smart_scan_expected[tag] = self.metadata.getall(tag)
        self.dlmtags_tag_suggestions = {}
        self.dlmtags_date_suggestion = None
        self._finish_smart_scan(confirmed)
        return True

    def _suggest_confirmed_date(self, track):
        """The first-release Year of a user-confirmed recording, as a suggestion."""
        self._smart_scan_date = None
        first = first_release_date(track)
        if not first or 'date' in self.metadata.deleted_tags or 'date' in self._manually_edited_tags:
            return
        current = [value for value in self.metadata.getall('date') if value.strip()]
        url = 'https://musicbrainz.org/recording/' + track['id']
        if not current:
            self.dlmtags_tag_suggestions['date'] = TagSuggestion(
                first,
                'MusicBrainz',
                tuple(self.metadata.getall('date')),
                recording_id=track['id'],
            )
        elif first[:4] != current[0][:4]:
            self.dlmtags_date_suggestion = (first, 'MusicBrainz', url)

    def _genre_suggested(self, decision, request, values, source, status):
        if self.smart_scan_decision is not decision or self.state == File.State.REMOVED or not values:
            return
        styles = request.styles if source.startswith('Discogs') else []
        for tag, tag_values in (('genre', values), ('style', styles)):
            current = self.metadata.getall(tag)
            if (
                tag_values
                and not any(value.strip() for value in current)
                and tag not in self._manually_edited_tags
                and tag not in self.metadata.deleted_tags
            ):
                self.dlmtags_tag_suggestions[tag] = TagSuggestion(
                    '; '.join(tag_values),
                    source,
                    tuple(current),
                    recording_id=decision.recording_id,
                )
        self.update()

    def _set_tag_suggestions(self, decision):
        suggestions, lookup = tag_suggestions(
            decision,
            self.metadata,
            self.base_filename,
            self._manually_edited_tags,
            self._dlmtags_credit_nodes,
        )
        self.dlmtags_tag_suggestions = suggestions
        return lookup

    def _start_credit_lookup(self, decision, recording_id):
        """One MusicBrainz recording lookup for the artist credit of an
        unverified suggestion: AcoustID results carry no credit join phrases."""
        token = self._credit_lookup_token = object()
        self._credit_lookup_task = self.tagger.mb_api.get_track_by_id(
            recording_id,
            partial(self._credit_lookup_finished, token, decision, recording_id),
            inc=('artist-credits',),
        )

    def _credit_lookup_finished(self, token, decision, recording_id, document, http, error):
        if token is not self._credit_lookup_token:
            return
        self._credit_lookup_token = None
        self._credit_lookup_task = None
        if (
            error
            or self.state == File.State.REMOVED
            or self.smart_scan_decision is not decision
            or not isinstance(document, dict)
            or document.get('id') != recording_id
        ):
            return
        self._dlmtags_credit_nodes[recording_id] = document
        self._set_tag_suggestions(decision)
        self.update()

    def cancel_credit_lookup(self):
        task, self._credit_lookup_task = self._credit_lookup_task, None
        self._credit_lookup_token = None
        if task is not None:
            self.tagger.webservice.abort_task(task)

    @property
    def smart_genre_pending(self):
        return self._smart_genre_request is not None and self._smart_genre_request.active

    def cancel_smart_genre(self):
        if self._smart_genre_request is not None:
            self._smart_genre_request.cancel()

    def _start_smart_genre(self, decision):
        if (
            decision.outcome != Outcome.MATCHED
            or self.smart_genre_pending
            or not get_config().setting['use_genres']
            or get_config().setting['max_genres'] < 1
            or any(value.strip() for value in self.metadata.getall('genre'))
            or 'genre' in self.metadata.deleted_tags
            or self.state == File.State.REMOVED
        ):
            return
        self.smart_genre_source = None
        self.smart_genre_values = []
        self.smart_genre_diagnostics = []
        if decision.user_confirmed:
            # Same providers; the values only become suggestions to review.
            request = SmartGenreRequest(
                self,
                decision,
                on_result=lambda values, source, status: self._genre_suggested(
                    decision,
                    request,
                    values,
                    source,
                    status,
                ),
            )
        else:
            request = SmartGenreRequest(self, decision)
        self._smart_genre_request = request
        request.start()

    def _apply_smart_scan_match(self, decision):
        track = decision.match.track
        recording_id = track['id']
        self._smart_scan_unverified_recording_id = None if decision.release_verified else recording_id
        if decision.user_confirmed:
            first_release = None
            self._suggest_confirmed_date(track)
        else:
            first_release = self._plan_smart_scan_date(track)
        if self._smart_scan_expected is not None:
            # The recording is confirmed even if no release can be verified.
            suggestions = Metadata()
            recording_to_metadata(track, suggestions)
            proposed = Metadata(self.metadata)
            proposed.update(suggestions)
            self.copy_metadata(proposed)
            for tag, values in suggestions.rawitems():
                if values and self.metadata.getall(tag) == list(values) and tag not in self._manually_edited_tags:
                    set_source(self, tag, 'MusicBrainz', 'https://musicbrainz.org/recording/' + recording_id)
            if first_release and self.metadata.getall('date') == [first_release]:
                set_source(self, 'date', 'MusicBrainz', 'https://musicbrainz.org/recording/' + recording_id)
            self.update()
        if decision.source == Source.ACOUSTID:
            acoustid = track.get('acoustid')
            if acoustid:
                self.metadata['acoustid_id'] = acoustid
                set_source(self, 'acoustid_id', 'AcoustID')
            self.tagger.acoustidmanager.add(self, recording_id)
        # Snapshot after acoustid_id is set, so the new AcoustID is kept.
        self._smart_scan_protected = protected_snapshot(self.metadata) or None
        if decision.release_verified or not self.metadata.images:
            self._smart_scan_images = None
        else:
            # Artwork of an unverified release must not replace existing art.
            self._smart_scan_images = self.metadata.images.copy()
        release = decision.release
        if decision.release_verified and release and release.get('release-group', {}).get('id'):
            releasegroup = self.tagger.get_release_group_by_id(release['release-group']['id'])
            releasegroup.loaded_albums.add(release['id'])
            self.tagger.move_file_to_track(self, release['id'], recording_id)
        else:
            self.tagger.move_file_to_nat(self, recording_id)

    def _plan_smart_scan_date(self, track):
        """Decide the Year of a match before any copy; return the first-release date.

        A date already in New Tags stays (a different year becomes a suggestion);
        an empty one takes the recording's first release; a user deletion stays.
        """
        recording_id = track['id']
        first = first_release_date(track)
        current = [value for value in self.metadata.getall('date') if value.strip()]
        self.dlmtags_date_suggestion = None
        if 'date' in self.metadata.deleted_tags:
            self._smart_scan_date = None
        elif current:
            self._smart_scan_date = (recording_id, current)
            if first and first[:4] != current[0][:4]:
                self.dlmtags_date_suggestion = (
                    first,
                    'MusicBrainz',
                    'https://musicbrainz.org/recording/' + recording_id,
                )
        else:
            self._smart_scan_date = (recording_id, [first] if first else [])
        return first

    def _log_smart_scan_decision(self, decision, final=False):
        log.debug(
            "Smart Scan%s: %r %s -> %s reasons=%s notes=%s recording=%s release=%s verified=%s",
            ' (final)' if final else '',
            self.filename,
            decision.source,
            decision.outcome,
            [str(r) for r in decision.reasons],
            [str(r) for r in decision.notes],
            decision.recording_id,
            decision.release_id,
            decision.release_verified,
        )
        if dbg := log.debug_if(DebugOpt.MATCHING):
            for i, c in enumerate(decision.candidates[:5]):
                dbg(
                    "  #%d %s sim=%.4f fp=%s %r by %r (%s) release=%r reasons=%s",
                    i + 1,
                    c.source,
                    c.similarity,
                    'n/a' if c.fingerprint_score is None else '%.4f' % c.fingerprint_score,
                    c.title,
                    c.artist,
                    c.recording_id,
                    c.release_title,
                    [str(r) for r in c.reasons],
                )

    def cancel_smart_scan(self):
        if self._smart_scan_token is not None:
            bulk_callback = self._bulk_scan_callback
            self._bulk_scan_callback = None
            self._smart_scan_token = None
            self.smart_scan_stage = None
            self._smart_scan_acoustid_decision = None
            decision = Decision(Outcome.CANCELLED)
            if bulk_callback is None:
                self.smart_scan_decision = decision
            self.tagger._acoustid.stop_analyze(self)
            if self._lookup_task is not None:
                self.tagger.webservice.abort_task(self._lookup_task)
                self._lookup_task = None
            self.clear_pending()
            if bulk_callback is not None:
                bulk_callback(self, decision)

    def _lookup_finished(self, lookuptype, document, http, error):
        self._lookup_task = None

        if self.state == File.State.REMOVED:
            return
        if error:
            log.error(
                "Network error encountered during the lookup for %s. Error code: %s",
                self.filename,
                error,
            )
        try:
            tracks = document['recordings']
        except (KeyError, TypeError):
            tracks = None

        def statusbar(message):
            self.tagger.window.set_statusbar_message(
                message,
                {'filename': self.filename},
                timeout=3000,
            )

        if tracks:
            if lookuptype == File.LookupType.ACOUSTID:
                min_similarity = 0
                min_margin = 0
            else:
                config = get_config()
                min_similarity = config.setting['match_min_similarity']
                min_margin = config.setting['match_min_margin']

            trackmatch, reason = self._match_to_track(tracks, min_similarity=min_similarity, min_margin=min_margin)
            if trackmatch is None:
                statusbar(N_('No matching tracks for file "%(filename)s"'))
            else:
                if reason == 'ambiguous':
                    statusbar(N_('Best match for file "%(filename)s" is ambiguous'))
                else:
                    statusbar(N_('File "%(filename)s" identified!'))
                (recording_id, release_group_id, release_id, acoustid, node) = trackmatch
                if lookuptype == File.LookupType.ACOUSTID:
                    self.metadata['acoustid_id'] = acoustid
                    self.tagger.acoustidmanager.add(self, recording_id)
                if release_group_id is not None:
                    releasegroup = self.tagger.get_release_group_by_id(release_group_id)
                    releasegroup.loaded_albums.add(release_id)
                    self.tagger.move_file_to_track(self, release_id, recording_id)
                else:
                    self.tagger.move_file_to_nat(self, recording_id)
        else:
            statusbar(N_('No matching tracks for file "%(filename)s"'))

        self.clear_pending()

    def _match_to_track(self, tracks, min_similarity=0, min_margin=0):
        """Match file to best track candidate.

        Returns:
            (trackmatch_tuple, None) on success,
            (None, reason) on rejection where reason is 'below_floor' or 'ambiguous'.
        """
        # multiple matches -- calculate similarities to each of them
        all_matches = [compare_to_track(self.metadata, track, FILE_COMPARISON_WEIGHTS) for track in tracks]
        all_matches.sort(key=lambda m: m.similarity, reverse=True)

        if dbg := log.debug_if(DebugOpt.MATCHING):
            dbg(
                "match_to_track: file=%r, %d candidates, min_sim=%.3f, min_margin=%.3f",
                self.filename,
                len(all_matches),
                min_similarity,
                min_margin,
            )
            dbg(
                "  file meta: title=%r artist=%r tracknumber=%r length=%r",
                self.metadata.get('title', ''),
                self.metadata.get('artist', ''),
                self.metadata.get('tracknumber', ''),
                self.metadata.length,
            )
            prefs = get_config().setting
            dbg(
                "  prefs: countries=%s formats=%s types=%s discouraged=%s",
                prefs['preferred_release_countries'],
                prefs['preferred_release_formats'],
                prefs['preferred_release_types'],
                prefs['discouraged_release_types'],
            )
            for i, m in enumerate(all_matches[:5]):
                title = '?'
                mbid = '?'
                if m.track:
                    title = m.track.get('title', '?')
                    mbid = m.track.get('id', '?')
                dbg("  #%d %s  %r (%s)", i + 1, _format_sim_breakdown(m), title, mbid)

        no_match = SimMatchTrack(similarity=-1, releasegroup=None, release=None, track=None)
        best_match = find_best_match_with_margin(
            iter(all_matches), no_match, min_similarity=min_similarity, min_margin=min_margin
        )

        if best_match.result is no_match:
            log.debug_if(
                DebugOpt.MATCHING,
                "  REJECTED (%s): best=%.4f",
                best_match.reason,
                best_match.similarity,
            )
            return None, best_match.reason
        else:
            if best_match.reason == 'ambiguous':
                log.debug_if(
                    DebugOpt.MATCHING,
                    "  AMBIGUOUS: best=%.4f",
                    best_match.similarity,
                )
            track_id = best_match.result.track['id']
            release_group_id, release_id, node = None, None, None
            acoustid = best_match.result.track.get('acoustid', None)

            if best_match.result.release:
                release_group_id = best_match.result.releasegroup['id']
                release_id = best_match.result.release['id']
            elif 'title' in best_match.result.track:
                node = best_match.result.track
            return (track_id, release_group_id, release_id, acoustid, node), best_match.reason

    def lookup_metadata(self):
        """Try to identify the file using the existing metadata."""
        if self._lookup_task or self.smart_scan_stage is not None or self.state == File.State.REMOVED:
            return
        self.tagger.window.set_statusbar_message(
            N_("Looking up the metadata for file %(filename)s …"),
            {'filename': self.filename},
        )
        metadata = self.metadata
        self.set_pending()
        config = get_config()
        self._lookup_task = self.tagger.mb_api.find_tracks(
            partial(self._lookup_finished, File.LookupType.METADATA),
            track=metadata['title'],
            artist=metadata['artist'],
            release=metadata['album'],
            tnum=metadata['tracknumber'],
            tracks=metadata['totaltracks'],
            qdur=str(metadata.length // 2000),
            isrc=metadata['isrc'],
            limit=config.setting['query_limit'],
        )

    def clear_lookup_task(self):
        self.cancel_smart_scan()
        self.cancel_credit_lookup()
        if self._lookup_task:
            self.tagger.webservice.abort_task(self._lookup_task)
            self._lookup_task = None

    def set_pending(self):
        if self.state != File.State.REMOVED:
            self.state = File.State.PENDING
            self.update_item(update_selection=False)

    def clear_pending(self, signal=True):
        if self.state == File.State.PENDING:
            self.state = File.State.NORMAL
            # Update file to recalculate changed state
            self.update(signal=False)
            if signal:
                self.update_item(update_selection=False)

    def update_item(self, update_selection=True):
        if self.ui_item:
            self.ui_item.update(update_selection=update_selection)
        self.metadata_updated.emit()

    def iterfiles(self, save=False):
        yield self

    # --- Per-format feature toggles ---
    def is_date_sanitization_enabled(self) -> bool:
        """Determine whether date sanitization should be applied.

        Returns
        -------
        bool
            True if sanitization should be applied for this file's format, False otherwise.

        Notes
        -----
        This respects the per-format user configuration keyed by ``FORMAT_KEY``.
        If the current format does not support toggling (i.e.,
        ``DATE_SANITIZATION_TOGGLEABLE`` is False) or ``FORMAT_KEY`` is not set,
        this method returns True.
        """
        # If this format does not support toggling, always treat as enabled
        if not getattr(self.__class__, 'DATE_SANITIZATION_TOGGLEABLE', False):
            return True

        # If the format key is not set, always treat as enabled
        format_key = getattr(self.__class__, 'FORMAT_KEY', None)
        if not format_key:
            return True

        disabled = get_config().setting['disable_date_sanitization_formats']
        return format_key not in disabled


file_post_load_processors = PluginFunctions[[File], None](label='file_post_load_processors')
file_post_addition_to_track_processors = PluginFunctions[['Track', File], None](
    label='file_post_addition_to_track_processors'
)
file_post_removal_to_track_processors = PluginFunctions[['Track', File], None](
    label='file_post_removal_from_track_processors'
)
file_pre_save_processors = PluginFunctions[[File], None](label='file_pre_save_processors')
file_post_save_processors = PluginFunctions[[File], None](label='file_post_save_processors')


def run_file_post_load_processors(file_object: File):
    file_post_load_processors.run(file_object)


def run_file_post_addition_to_track_processors(track_object: 'Track', file_object: File):
    file_post_addition_to_track_processors.run(track_object, file_object)


def run_file_post_removal_from_track_processors(track_object: 'Track', file_object: File):
    file_post_removal_to_track_processors.run(track_object, file_object)


def run_file_pre_save_processors(file_object: File):
    file_pre_save_processors.run(file_object)


def run_file_post_save_processors(file_object: File):
    file_post_save_processors.run(file_object)
