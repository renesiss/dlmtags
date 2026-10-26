# Picard, the next-generation MusicBrainz tagger
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

"""Smart Scan decision policy (DLMtags).

Smart Scan runs an AcoustID fingerprint lookup and, only when that does not
give a usable result, one metadata Lookup. Picard's matching engine
(:func:`picard.matching.compare_to_track`) scores recording identity without
release weights first. This module checks whether the top candidate may be
applied automatically, then evaluates its releases independently.

Why a separate policy is needed (measured against ``compare_to_track``):

* Picard's similarity only counts fields that are present. An untagged file
  scores a base similarity of ~0.87 against any recording of the right length,
  a title-only file ~0.91, a fully tagged correct file ~0.95. The number alone
  cannot tell "confirmed" from "unknown".
* A matching title by a *different* artist still scores ~0.52 base, which
  passes Picard's metadata floor (0.25) and trivially passes the AcoustID path,
  which uses no floor at all.
* For AcoustID candidates the final similarity is
  ``base * sources_ratio * fingerprint_score``: two unrelated scales multiplied.
  The fingerprint score is therefore evaluated on its own here.

So every candidate is checked for explicit evidence (artist, title, version,
length, identifiers), and the outcome is one of :class:`Outcome`. The policy
prefers Needs Review / No Match over applying a wrong recording or release.
Nothing in this module modifies metadata; it only returns decisions.
"""

from collections.abc import (
    Iterable,
    Mapping,
    Sequence,
)
from dataclasses import (
    dataclass,
    field,
    replace,
)
from datetime import date as calendar_date
from enum import Enum
import os.path
import re
import unicodedata

from picard.config import (
    FloatOption,
    IntOption,
    get_config,
)
from picard.i18n import (
    N_,
    gettext as _,
)
from picard.matching import (
    SimMatchTrack,
    _weights_from_preferred_countries,
    _weights_from_preferred_formats,
    _weights_from_preferred_release_types,
    compare_to_track,
)
from picard.mbjson import recording_to_metadata
from picard.metadata import Metadata
from picard.similarity import similarity2
from picard.tags import ALL_TAGS
from picard.util import (
    extract_year_from_date,
    tracknum_and_title_from_filename,
)
from picard.util.astrcmp import astrcmp


# ---------------------------------------------------------------------------
# Configurable thresholds (advanced settings, no UI). Defaults are documented
# in SmartScanThresholds below.
# ---------------------------------------------------------------------------

FloatOption('setting', 'smart_scan_acoustid_min_score', 0.50, bounds=(0.0, 1.0))
FloatOption('setting', 'smart_scan_acoustid_strong_score', 0.80, bounds=(0.0, 1.0))
FloatOption('setting', 'smart_scan_metadata_min_similarity', 0.70, bounds=(0.0, 1.0))
FloatOption('setting', 'smart_scan_min_margin', 0.05, bounds=(0.0, 1.0))
IntOption('setting', 'smart_scan_length_tolerance_ms', 7000, bounds=(0, 600000))
IntOption('setting', 'smart_scan_length_conflict_ms', 15000, bounds=(0, 600000))


@dataclass(frozen=True)
class SmartScanThresholds:
    """Thresholds for automatic acceptance.

    acoustid_min_score:
        AcoustID result score (0..1, the provider's own fingerprint match
        score) below which a fingerprint candidate is not usable evidence.
        0.5 is the conventional floor used by AcoustID clients.
    acoustid_strong_score:
        Fingerprint score from which the fingerprint alone may identify an
        untagged file (title and artist unknown), provided the length agrees.
        Between min and strong, title, artist and length must all confirm.
    metadata_min_similarity:
        Picard similarity (base * MusicBrainz search score) required for a
        metadata Lookup candidate. Correct, sparsely tagged files measure
        0.84-0.95 base; 0.70 leaves room for search scores around 0.8.
    min_margin:
        Minimum similarity gap to the best competing *plausible* recording.
        Picard's default is 0.02; Smart Scan uses at least this value.
    length_tolerance_ms / length_conflict_ms:
        Length difference that confirms a match / that is treated as a
        different version (fade, edit, extended mix). In between is "close":
        acceptable with fingerprint evidence, not for metadata-only matches.
    """

    acoustid_min_score: float = 0.50
    acoustid_strong_score: float = 0.80
    metadata_min_similarity: float = 0.70
    min_margin: float = 0.05
    length_tolerance_ms: int = 7000
    length_conflict_ms: int = 15000

    @classmethod
    def from_config(cls, config=None) -> 'SmartScanThresholds':
        config = config or get_config()

        def setting(name, default):
            try:
                value = config.setting[name]
            except KeyError:
                return default
            return default if value is None else value

        defaults = cls()
        try:
            picard_margin = float(setting('match_min_margin', 0.0))
        except (TypeError, ValueError):
            picard_margin = 0.0
        return cls(
            acoustid_min_score=setting('smart_scan_acoustid_min_score', defaults.acoustid_min_score),
            acoustid_strong_score=setting('smart_scan_acoustid_strong_score', defaults.acoustid_strong_score),
            metadata_min_similarity=setting('smart_scan_metadata_min_similarity', defaults.metadata_min_similarity),
            min_margin=max(setting('smart_scan_min_margin', defaults.min_margin), picard_margin),
            length_tolerance_ms=setting('smart_scan_length_tolerance_ms', defaults.length_tolerance_ms),
            length_conflict_ms=setting('smart_scan_length_conflict_ms', defaults.length_conflict_ms),
        )


# Text-comparison thresholds (on similarity2/astrcmp scales, 0..1).
NAME_MATCH_THRESHOLD = 0.85
TITLE_MATCH_THRESHOLD = 0.85
ALBUM_MATCH_THRESHOLD = 0.85
ALBUM_CONFLICT_THRESHOLD = 0.50

# Number of candidates retained on a decision for user review.
MAX_RETAINED_CANDIDATES = 10


# ---------------------------------------------------------------------------
# Decision types
# ---------------------------------------------------------------------------


class _StrEnum(str, Enum):
    """str-valued enum (enum.StrEnum needs Python 3.11; Picard supports 3.10)."""

    def __str__(self) -> str:
        return self.value


class Source(_StrEnum):
    ACOUSTID = 'acoustid'
    METADATA = 'metadata'


class Outcome(_StrEnum):
    MATCHED = 'matched'
    NEEDS_REVIEW = 'needs_review'
    NO_MATCH = 'no_match'
    ERROR = 'error'
    CANCELLED = 'cancelled'


# A Matched decision the user confirmed after review (Decision.user_confirmed).
USER_CONFIRMED_LABEL = N_('Confirmed by user')

OUTCOME_LABELS = {
    Outcome.MATCHED: N_('Matched'),
    Outcome.NEEDS_REVIEW: N_('Needs Review'),
    Outcome.NO_MATCH: N_('No Match'),
    Outcome.ERROR: N_('Error'),
    Outcome.CANCELLED: N_('Cancelled'),
}


class Reason(_StrEnum):
    # Reasons that block automatic application
    ARTIST_CONFLICT = 'artist_conflict'
    TITLE_CONFLICT = 'title_conflict'
    VERSION_MISMATCH = 'version_mismatch'
    LENGTH_MISMATCH = 'length_mismatch'
    LOW_SIMILARITY = 'low_similarity'
    LOW_FINGERPRINT_SCORE = 'low_fingerprint_score'
    INSUFFICIENT_EVIDENCE = 'insufficient_evidence'
    CLOSE_SCORES = 'close_scores'
    ALBUM_CONFLICT = 'album_conflict'
    SOURCE_DISAGREEMENT = 'source_disagreement'
    NO_CANDIDATES = 'no_candidates'
    NETWORK_ERROR = 'network_error'
    FINGERPRINT_ERROR = 'fingerprint_error'
    CANCELLED = 'cancelled'
    # Informational notes attached to a match that was applied
    RELEASE_UNVERIFIED = 'release_unverified'
    COMPETING_RELEASES = 'competing_releases'
    DATE_DIFFERS = 'date_differs'
    CONFIRMED_BY_IDENTIFIER = 'confirmed_by_identifier'


REASON_LABELS = {
    Reason.ARTIST_CONFLICT: N_('Artist conflict'),
    Reason.TITLE_CONFLICT: N_('Title conflict'),
    Reason.VERSION_MISMATCH: N_('Version mismatch'),
    Reason.LENGTH_MISMATCH: N_('Length mismatch'),
    Reason.LOW_SIMILARITY: N_('Low similarity'),
    Reason.LOW_FINGERPRINT_SCORE: N_('Low fingerprint score'),
    Reason.INSUFFICIENT_EVIDENCE: N_('Insufficient evidence'),
    Reason.CLOSE_SCORES: N_('Close candidate scores'),
    Reason.ALBUM_CONFLICT: N_('Album conflict'),
    Reason.SOURCE_DISAGREEMENT: N_('Fingerprint and metadata disagree'),
    Reason.NO_CANDIDATES: N_('No candidates'),
    Reason.NETWORK_ERROR: N_('Network error'),
    Reason.FINGERPRINT_ERROR: N_('Fingerprint or AcoustID service unavailable'),
    Reason.CANCELLED: N_('Cancelled'),
    Reason.RELEASE_UNVERIFIED: N_('Release not verified'),
    Reason.COMPETING_RELEASES: N_('Competing releases'),
    Reason.DATE_DIFFERS: N_('Date differs from file'),
    Reason.CONFIRMED_BY_IDENTIFIER: N_('Confirmed by identifier'),
}


class Evidence(_StrEnum):
    MATCH = 'match'
    PARTIAL = 'partial'  # artist: a credited artist matches, but not the primary one
    CLOSE = 'close'  # length: within conflict limit but outside tolerance
    MISMATCH = 'mismatch'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class ReleaseSummary:
    release_id: str
    title: str
    date: str
    status: str
    primary_type: str
    secondary_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class CandidateSummary:
    """Real candidate data retained for review. Never synthesized."""

    source: Source
    recording_id: str
    title: str
    artist: str
    length: int | None
    release_id: str | None
    release_title: str | None
    date: str | None
    similarity: float
    fingerprint_score: float | None
    reasons: tuple[Reason, ...] = ()
    releases: tuple[ReleaseSummary, ...] = ()
    versions: tuple[str, ...] = ()
    disambiguation: str = ''
    evidence: tuple[str, ...] = ()
    # The candidate's own recording node (AcoustID or MusicBrainz), retained
    # for unverified suggestions; not part of comparisons or logs.
    node: dict | None = field(default=None, compare=False, repr=False)


@dataclass
class CandidateEvidence:
    match: SimMatchTrack
    source: Source
    artist: Evidence = Evidence.UNKNOWN
    title: Evidence = Evidence.UNKNOWN
    version: Evidence = Evidence.UNKNOWN
    length: Evidence = Evidence.UNKNOWN
    fingerprint_score: float | None = None
    confirmed_by_identifier: bool = False
    conflicts: list[Reason] = field(default_factory=list)

    @property
    def recording_id(self) -> str:
        return self.match.track.get('id', '') if self.match.track else ''

    @property
    def similarity(self) -> float:
        return self.match.similarity


@dataclass
class Decision:
    outcome: Outcome
    source: Source | None = None
    reasons: tuple[Reason, ...] = ()
    notes: tuple[Reason, ...] = ()
    match: SimMatchTrack | None = None
    release: dict | None = None
    release_verified: bool = False
    candidates: tuple[CandidateSummary, ...] = ()
    # A fingerprint candidate that is not reliable enough to apply, but has no
    # conflicting evidence either. It is preserved through the metadata
    # fallback so fingerprint evidence is not silently discarded.
    weak: CandidateEvidence | None = None
    details: dict = field(default_factory=dict)
    # The user confirmed this recording after Needs Review; it was not
    # verified automatically and keeps the review reasons as notes.
    user_confirmed: bool = False

    @property
    def recording_id(self) -> str | None:
        if self.match and self.match.track:
            return self.match.track.get('id')
        return None

    @property
    def release_id(self) -> str | None:
        return self.release.get('id') if self.release else None

    def describe(self) -> str:
        """Translated, human readable summary: outcome plus reasons/notes."""
        label = decision_label(self)
        items = [_(REASON_LABELS[r]) for r in self.reasons + self.notes]
        if items:
            return '%s: %s' % (label, ', '.join(items))
        return label


# Evidence labels of a CandidateSummary.
EVIDENCE_IDENTIFIER = N_('Recording identifier matches')
EVIDENCE_ARTIST = N_('Artist matches')
EVIDENCE_TITLE = N_('Title matches')
EVIDENCE_VERSION = N_('No musical version conflict')
EVIDENCE_LENGTH = N_('Duration matches')


def decision_label(decision: 'Decision') -> str:
    """Translated outcome label; a user confirmation is not an automatic match."""
    return _(USER_CONFIRMED_LABEL) if decision.user_confirmed else _(OUTCOME_LABELS[decision.outcome])


# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------

_APOSTROPHES_RE = re.compile(r"[’'`´]")
_NON_WORD_RE = re.compile(r'[\W_]+', re.UNICODE)


def fold(text: str | None) -> str:
    """Case-fold, strip accents and punctuation, normalize '&' to 'and'."""
    if not text:
        return ''
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = text.casefold().replace('&', ' and ')
    text = _APOSTROPHES_RE.sub('', text)
    text = _NON_WORD_RE.sub(' ', text)
    return ' '.join(text.split())


def text_similarity(a: str | None, b: str | None) -> float:
    a, b = fold(a), fold(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return similarity2(a, b)


# ---------------------------------------------------------------------------
# Version qualifiers
# ---------------------------------------------------------------------------

# Qualifiers that do not denote a different recording (remasters are normally
# the same MusicBrainz recording; explicit/clean and album version are labels).
_NEUTRAL_QUALIFIER_RE = re.compile(
    r'\b(?:\d{2,4}\s+)?(?:digital(?:ly)?\s+)?remaster(?:ed)?(?:\s+\d{2,4})?(?:\s+version)?\b'
    r'|\b(?:mono|stereo)(?:\s+version)?\b'
    r'|\b(?:explicit|clean|dirty)(?:\s+version)?\b'
    r'|\b(?:album|lp|original)\s+(?:version|mix)\b'
    r'|\bbonus\s+track\b'
)

# Qualifiers that denote a distinct version. Matched on folded text.
_VERSION_CATEGORIES = {
    'live': re.compile(r'\b(?:live|en vivo|en directo|ao vivo|in concert)\b'),
    'remix': re.compile(r'\b(?:remix(?:ed)?|rmx|re mix|rework|dub|vip|mix|mashup|bootleg)\b'),
    'extended': re.compile(r'\b(?:extended|12 inch|12inch|maxi|long version|full length)\b'),
    'edit': re.compile(r'\b(?:edit|radio version|single version|short version)\b'),
    'acoustic': re.compile(r'\b(?:acoustic|unplugged|stripped)\b'),
    'instrumental': re.compile(r'\b(?:instrumental|karaoke|backing track)\b'),
    'acappella': re.compile(r'\b(?:a ?cappella|acapella|vocals only)\b'),
    'demo': re.compile(r'\b(?:demo|rough mix|early version|alternate (?:take|version)|outtake)\b'),
    'cover': re.compile(r'\b(?:cover|tribute|originally performed|in the style of|made famous)\b'),
    'orchestral': re.compile(r'\b(?:orchestral|symphonic|piano version|string version|strings version)\b'),
    'tempo': re.compile(r'\b(?:sped up|speed up|slowed|nightcore|chopped|screwed)\b'),
    'reprise': re.compile(r'\breprise\b'),
}

_FEAT_RE = re.compile(r'\b(?:feat|ft|featuring|with)\b\.?\s+(.+)$', re.IGNORECASE)
# Outside brackets "with" is an ordinary title word ("Stay With Me",
# "With or Without You"); only an explicit marker starts a credit there.
_EXPLICIT_FEAT_RE = re.compile(r'\b(?:feat|ft|featuring)\b\.?\s+(.+)$', re.IGNORECASE)
_BRACKET_RE = re.compile(r'[\(\[\{]([^\(\)\[\]\{\}]*)[\)\]\}]')
_DASH_SUFFIX_RE = re.compile(r'\s+[-–—]\s+([^-–—]+)$')


_SOURCE_LABEL_RE = re.compile(
    r'\b(?:official(?:\s+music)?\s+video|official\s+audio|(?:official\s+)?lyric(?:s)?\s+video|music\s+video'
    r'|official\s+visuali[sz]er|official\s+promo|(?:full\s+)?hd|4k|1080p|720p)\b',
    re.IGNORECASE,
)


def strip_source_labels(title: str) -> str:
    """Remove source descriptions without treating them as recording evidence."""

    def bracket(match):
        inner = _SOURCE_LABEL_RE.sub(' ', match.group(1))
        inner = re.sub(r'\s+', ' ', inner).strip(' -–—|,')
        return '(' + inner + ')' if inner else ' '

    cleaned = _BRACKET_RE.sub(bracket, title)
    # Only trailing unbracketed labels are descriptions; keep actual song names.
    trailing = re.compile(r'\s+(?:[-–—|]\s*)?(?:' + _SOURCE_LABEL_RE.pattern + r')\s*$', re.IGNORECASE)
    while trailing.search(cleaned):
        cleaned = trailing.sub('', cleaned)
    return re.sub(r'\s+', ' ', cleaned).strip(' -–—|') or title


def version_categories(text: str | None) -> frozenset[str]:
    folded = _NEUTRAL_QUALIFIER_RE.sub(' ', fold(text))
    return frozenset(name for name, regex in _VERSION_CATEGORIES.items() if regex.search(folded))


def _credit_match(text: str, bracketed: bool):
    return (_FEAT_RE if bracketed else _EXPLICIT_FEAT_RE).match(text.strip())


def _is_qualifier(text: str, bracketed: bool = False) -> bool:
    folded = fold(text)
    return bool(
        folded == 'original'
        or _NEUTRAL_QUALIFIER_RE.search(folded)
        or version_categories(text)
        or _credit_match(text, bracketed)
    )


@dataclass(frozen=True)
class TitleParts:
    base: str
    versions: frozenset[str]
    featured: tuple[str, ...]


def split_title(title: str | None) -> TitleParts:
    """Split a title into base title, version categories and featured artists.

    Only bracketed parts or a trailing " - ..." part are inspected for version
    qualifiers, so titles such as "Live and Let Die" keep their meaning.
    """
    title = strip_source_labels(title or '')
    qualifiers: list[tuple[str, bool]] = []
    featured: list[str] = []

    def strip_bracket(m):
        inner = m.group(1)
        if _is_qualifier(inner, bracketed=True):
            qualifiers.append((inner, True))
            return ' '
        return m.group(0)

    base = _BRACKET_RE.sub(strip_bracket, title)
    m = _DASH_SUFFIX_RE.search(base)
    if m and _is_qualifier(m.group(1)):
        qualifiers.append((m.group(1), False))
        base = base[: m.start()]
    feat = _EXPLICIT_FEAT_RE.search(base)
    if feat:
        base = base[: feat.start()]
    for qualifier, bracketed in qualifiers:
        feat = _credit_match(qualifier, bracketed)
        if feat:
            featured.extend(split_artist_names(feat.group(1)))
    versions = frozenset().union(*(version_categories(q) for q, _bracketed in qualifiers))
    return TitleParts(base=base.strip(), versions=versions, featured=tuple(featured))


# ---------------------------------------------------------------------------
# Artists
# ---------------------------------------------------------------------------

_ARTIST_SPLIT_RE = re.compile(
    r'\s*(?:;|,|/|\+|&|\s(?:feat|ft|featuring|with|vs|versus|x|and|presents|pres|meets)\b\.?)\s*',
    re.IGNORECASE,
)
_VARIOUS_ARTISTS_FOLDED = frozenset({'various artists', 'various', 'va', 'v a', 'unknown artist', 'unknown'})


def split_artist_names(text: str | None) -> list[str]:
    if not text:
        return []
    return [name for name in (fold(part) for part in _ARTIST_SPLIT_RE.split(' ' + text)) if name]


def _strip_article(name: str) -> str:
    return name[4:] if name.startswith('the ') else name


def name_similarity(a: str, b: str) -> float:
    a, b = _strip_article(fold(a)), _strip_article(fold(b))
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return astrcmp(a, b)


def _sort_name_variants(sort_name: str) -> list[str]:
    # "Cocker, Joe" -> "Joe Cocker"
    if sort_name and ',' in sort_name:
        last, first = sort_name.split(',', 1)
        return [sort_name, '%s %s' % (first.strip(), last.strip())]
    return [sort_name] if sort_name else []


def candidate_artist_names(track: dict) -> tuple[str, list[str]]:
    """Return (full credit string, individual names incl. aliases)."""
    full = ''
    names: list[str] = []
    for credit in track.get('artist-credit') or []:
        credited = credit.get('name') or ''
        artist = credit.get('artist') or {}
        full += credited or artist.get('name', '')
        full += credit.get('joinphrase', '; ')
        for value in (credited, artist.get('name', '')):
            if value:
                names.append(value)
        names.extend(_sort_name_variants(artist.get('sort-name', '')))
        names.extend(alias['name'] for alias in artist.get('aliases') or [] if alias.get('name'))
    return full.strip(' ;'), names


def _best_name_similarity(name: str, candidates: Iterable[str]) -> float:
    return max((name_similarity(name, c) for c in candidates), default=0.0)


def artist_evidence(file_artists: Sequence[str], file_full: str, track: dict) -> Evidence:
    """Compare the file's artist(s) with a candidate's artist credit.

    MATCH:    the full credit matches, or the file's primary artist is credited
    PARTIAL:  another credited artist matches one of the file's artists
              (featured artist tagged as main artist, credit variations)
    MISMATCH: no artist on either side matches
    UNKNOWN:  no usable artist on one side
    """
    if not file_artists or not track.get('artist-credit'):
        return Evidence.UNKNOWN
    cand_full, cand_names = candidate_artist_names(track)
    if name_similarity(file_full, cand_full) >= NAME_MATCH_THRESHOLD:
        return Evidence.MATCH
    split_candidates = list(cand_names)
    for name in cand_names:
        split_candidates.extend(split_artist_names(name))
    if _best_name_similarity(file_artists[0], split_candidates) >= NAME_MATCH_THRESHOLD:
        return Evidence.MATCH
    if any(_best_name_similarity(name, split_candidates) >= NAME_MATCH_THRESHOLD for name in file_artists[1:]):
        return Evidence.PARTIAL
    return Evidence.MISMATCH


# ---------------------------------------------------------------------------
# File evidence
# ---------------------------------------------------------------------------

_FILENAME_TRACKNO_RE = re.compile(r'^\s*\d{1,3}\s*[-._)]?\s+')
_FILENAME_SEPARATOR_RE = re.compile(r'\s+[-–—]\s+')
_VIDEO_ID_RE = re.compile(r'\s*[\[(][A-Za-z0-9_-]{11}[\])]\s*$')
# yt-dlp playlist downloads: "NNN-<uploader> - <video title> [<video id>]".
# The glued 3-4 digit index (not a year) and the video id identify the
# template; the uploader is a channel, not the artist.
_UPLOADER_PREFIX_RE = re.compile(r'^(?!19\d\d-|20\d\d-)\d{3,4}-(?=\S)')


def clean_filename(base_filename: str) -> tuple[str, tuple[str, ...]]:
    """Remove upload decorations, retaining source descriptions separately."""
    stem = os.path.splitext(base_filename)[0]
    has_video_id = bool(_VIDEO_ID_RE.search(stem))
    stem = _VIDEO_ID_RE.sub('', stem).replace('_', ' ')
    if has_video_id and _UPLOADER_PREFIX_RE.match(stem):
        uploader_and_title = _FILENAME_SEPARATOR_RE.split(stem, maxsplit=1)
        if len(uploader_and_title) == 2:
            stem = uploader_and_title[1]
    stem = _FILENAME_TRACKNO_RE.sub('', stem)
    parts = _FILENAME_SEPARATOR_RE.split(stem)
    # A caption ending in an explicit video label can be followed by an
    # uploader credit. A genuine musical qualifier must remain evidence.
    if len(parts) >= 2 and _SOURCE_LABEL_RE.search(parts[-2]) and not _is_qualifier(parts[-1]):
        parts.pop()
    elif has_video_id and len(parts) >= 3 and fold(parts[-1]) == fold(parts[0]):
        parts.pop()
    stem = ' - '.join(parts)
    labels = tuple(dict.fromkeys(m.group(0) for m in _SOURCE_LABEL_RE.finditer(stem)))
    return stem, labels


def filename_artist_title(base_filename: str) -> tuple[str, str] | None:
    """Parse "[NN - ]Artist - [Album - ]Title.ext" style filenames."""
    stem, _labels = clean_filename(base_filename)
    cleaned = strip_source_labels(stem)
    parts = [p.strip() for p in _FILENAME_SEPARATOR_RE.split(cleaned) if p.strip()]
    if len(parts) < 2:
        # A title such as "HD" must not disappear as the only song name.
        parts = [p.strip() for p in _FILENAME_SEPARATOR_RE.split(stem) if p.strip()]
    if len(parts) < 2:
        return None
    title = ' - '.join(parts[-2:]) if len(parts) > 2 and _qualifies_previous_part(parts[-1]) else parts[-1]
    return parts[0], title


def _qualifies_previous_part(text: str) -> bool:
    """True for a part that only qualifies the title before it ("Live",
    "(Radio Edit)"), not for a title carrying its own qualifier ("Song (Live)")."""
    base = split_title(text).base
    return not _SOURCE_LABEL_RE.sub(' ', base).strip(' -–—|,') or _is_qualifier(base)


@dataclass(frozen=True)
class FileHints:
    title: str = ''
    title_source: str | None = None  # 'tag' | 'filename'
    artist: str = ''
    artist_names: tuple[str, ...] = ()
    artist_source: str | None = None  # 'tag' | 'filename'
    album: str = ''
    date: str = ''
    length: int = 0
    isrcs: frozenset[str] = frozenset()
    recording_ids: frozenset[str] = frozenset()
    album_ids: frozenset[str] = frozenset()
    barcode: str = ''
    is_compilation: bool = False
    filename_text: str = ''
    source_labels: tuple[str, ...] = ()


def candidate_filename_hints(hints: FileHints, track: dict) -> FileHints:
    """Resolve a separator-free caption only against returned credit names.

    Exact compact spelling allows punctuation lost to filename sanitizing,
    e.g. Rag_n_Bone_Man / Rag’n’Bone Man and P_nk / P!nk. It does not map
    arbitrary names or infer that a particular video contains identical audio.
    """
    if not hints.filename_text:
        return hints
    text = hints.filename_text
    tokens = list(re.finditer(r'\w+', text, re.UNICODE))
    offset = 0
    credits = track.get('artist-credit') or []
    if not credits:
        return hints
    for index, credit in enumerate(credits):
        if index:
            while offset < len(tokens) and tokens[offset].group().casefold() in {
                'and', 'feat', 'ft', 'featuring', 'with', 'x',
            }:
                offset += 1
        names = candidate_artist_names({'artist-credit': [credit]})[1]
        compact_names = {fold(name).replace(' ', '') for name in names if name}
        accumulated = ''
        matched_end = None
        for end in range(offset, len(tokens)):
            accumulated += fold(tokens[end].group()).replace(' ', '')
            if accumulated in compact_names:
                matched_end = end + 1
            if not any(name.startswith(accumulated) for name in compact_names):
                break
        if matched_end is None:
            return hints
        offset = matched_end
    if offset >= len(tokens):
        return hints
    full, _names = candidate_artist_names(track)
    return replace(
        hints, artist=full, artist_names=tuple(split_artist_names(full)), artist_source='filename',
        title=text[tokens[offset].start():].strip(), title_source='filename', filename_text='',
    )


def file_hints(metadata: Metadata, base_filename: str = '') -> FileHints:
    """Collect identity evidence from the proposed metadata (New Tags).

    Filename-derived artist/title are only used when the corresponding tag is
    missing, or when the title tag is merely Picard's filename guess.
    """
    title = metadata.get('title', '') or ''
    title_source = 'tag' if title else None
    artist_values = [a for a in metadata.getall('artist') if a]
    artist = '; '.join(artist_values)
    artist_source = 'tag' if artist else None

    parsed = filename_artist_title(base_filename) if base_filename else None
    cleaned, source_labels = clean_filename(base_filename) if base_filename else ('', ())
    cleaned = strip_source_labels(cleaned)
    guessed_title = tracknum_and_title_from_filename(base_filename).title if base_filename else ''
    title_is_guess = bool(title) and not artist and fold(title) == fold(guessed_title)
    if parsed and not artist:
        artist, artist_values, artist_source = parsed[0], [parsed[0]], 'filename'
        if not title or title_is_guess:
            title, title_source = parsed[1], 'filename'
    elif title_is_guess:
        # Picard copied an unstructured filename ("track01") into the title
        # tag. That is not evidence about the recording.
        title, title_source = '', None

    title = strip_source_labels(title)
    source_labels = tuple(label for label in source_labels if fold(label) != fold(title))
    names: list[str] = []
    for value in artist_values:
        names.extend(split_artist_names(value))
    for value in metadata.getall('artists'):
        names.extend(n for n in split_artist_names(value) if n not in names)
    names.extend(n for n in split_title(title).featured if n not in names)
    if names and fold(names[0]) in _VARIOUS_ARTISTS_FOLDED:
        names, artist, artist_source = [], '', None

    albumartist = fold(metadata.get('albumartist', ''))
    return FileHints(
        title=title,
        title_source=title_source,
        artist=artist,
        artist_names=tuple(names),
        artist_source=artist_source,
        album=metadata.get('album', '') or '',
        date=metadata.get('date', '') or '',
        length=metadata.length or 0,
        isrcs=frozenset(i.upper() for i in metadata.getall('isrc') if i),
        recording_ids=frozenset(i for i in metadata.getall('musicbrainz_recordingid') if i),
        album_ids=frozenset(i for i in metadata.getall('musicbrainz_albumid') if i),
        barcode=metadata.get('barcode', '') or '',
        is_compilation=metadata.get('compilation', '') == '1' or albumartist in _VARIOUS_ARTISTS_FOLDED,
        filename_text=cleaned if not parsed and not artist and not title and len(cleaned.split()) >= 4 else '',
        source_labels=source_labels,
    )


def hints_metadata(metadata: Metadata, hints: FileHints) -> Metadata:
    """Metadata used for scoring: New Tags plus filename-derived artist/title
    when (and only when) those tags are missing."""
    if hints.title == metadata['title'] and hints.artist == metadata['artist']:
        return metadata
    scoring = Metadata()
    scoring.copy(metadata)
    if hints.title:
        scoring['title'] = hints.title
    if hints.artist_source == 'filename':
        scoring['artist'] = hints.artist
    return scoring


# ---------------------------------------------------------------------------
# Candidate assessment
# ---------------------------------------------------------------------------


def fingerprint_score(track: dict) -> float | None:
    """AcoustID's own result score for this candidate (0..1), if known."""
    value = track.get('acoustid_score')
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def length_evidence(file_length: int, track_length, thresholds: SmartScanThresholds) -> Evidence:
    try:
        track_length = int(track_length or 0)
    except (TypeError, ValueError):
        track_length = 0
    if not file_length or not track_length:
        return Evidence.UNKNOWN
    diff = abs(file_length - track_length)
    if diff <= thresholds.length_tolerance_ms:
        return Evidence.MATCH
    if diff <= thresholds.length_conflict_ms:
        return Evidence.CLOSE
    return Evidence.MISMATCH


def assess_candidate(
    match: SimMatchTrack, source: Source, hints: FileHints, thresholds: SmartScanThresholds
) -> CandidateEvidence:
    track = match.track or {}
    ev = CandidateEvidence(match=match, source=source)
    if source == Source.ACOUSTID:
        ev.fingerprint_score = fingerprint_score(track)

    track_isrcs = {i.upper() for i in (track.get('isrcs') or [])}
    ev.confirmed_by_identifier = bool(
        (hints.recording_ids and track.get('id') in hints.recording_ids)
        or (hints.isrcs and track_isrcs and hints.isrcs <= track_isrcs)
    )

    ev.artist = artist_evidence(hints.artist_names, hints.artist, track)
    if hints.filename_text:
        # A meaningful caption that could not be reconciled with the returned
        # credit must not become "no identity evidence" for a strong fingerprint.
        ev.artist = Evidence.MISMATCH
        ev.title = Evidence.MISMATCH

    if hints.title:
        file_title = split_title(hints.title)
        cand_title = split_title(track.get('title', ''))
        if text_similarity(file_title.base, cand_title.base) >= TITLE_MATCH_THRESHOLD:
            ev.title = Evidence.MATCH
        else:
            ev.title = Evidence.MISMATCH
        cand_versions = cand_title.versions | version_categories(track.get('disambiguation', ''))
        ev.version = Evidence.MATCH if file_title.versions == cand_versions else Evidence.MISMATCH

    ev.length = length_evidence(hints.length, track.get('length'), thresholds)

    if ev.artist == Evidence.MISMATCH:
        ev.conflicts.append(Reason.ARTIST_CONFLICT)
    if ev.title == Evidence.MISMATCH:
        ev.conflicts.append(Reason.TITLE_CONFLICT)
    if ev.version == Evidence.MISMATCH:
        ev.conflicts.append(Reason.VERSION_MISMATCH)
    if ev.length == Evidence.MISMATCH:
        ev.conflicts.append(Reason.LENGTH_MISMATCH)
    # Explicit exception: an identical recording MBID or ISRC set is
    # independent proof of the recording, so textual disagreements (renamed
    # artist, translated title) do not block it.
    if ev.confirmed_by_identifier:
        ev.conflicts.clear()
    return ev


def recording_match(metadata: Metadata, track: dict, weights) -> SimMatchTrack:
    """Reuse Picard's recording signals without any release-level evidence."""
    identity_weights = {
        'identifiers': {k: v for k, v in weights.get('identifiers', {}).items() if k == 'isrc'},
        'similarity': {
            k: v for k, v in weights.get('similarity', {}).items() if k in {'artist', 'title', 'length'}
        },
    }
    node = dict(track, title=strip_source_labels(track.get('title', '')), releases=[])
    match = compare_to_track(metadata, node, identity_weights)
    # Retain all original release nodes for the separate release decision.
    return replace(match, track=track)


def dedupe_matches(matches: Iterable[SimMatchTrack]) -> list[SimMatchTrack]:
    """Group by Recording MBID, retaining releases from every provider entry."""
    grouped: dict[str, list[SimMatchTrack]] = {}
    for match in matches:
        if match.track and match.track.get('id'):
            grouped.setdefault(match.track['id'], []).append(match)
    result = []
    for entries in grouped.values():
        best = max(entries, key=lambda m: m.similarity)
        releases = {}
        for entry in entries:
            for release in entry.track.get('releases') or []:
                key = (release.get('id'), normalize_release_date(release.get('date')), release.get('country'))
                releases.setdefault(key, release)
        result.append(replace(best, track=dict(best.track, releases=list(releases.values()))))
    return sorted(result, key=lambda m: m.similarity, reverse=True)


def summarize(ev: CandidateEvidence, release: dict | None = None) -> CandidateSummary:
    track = ev.match.track or {}
    release = release if release is not None else ev.match.release
    full, _names = candidate_artist_names(track)
    return CandidateSummary(
        source=ev.source,
        recording_id=track.get('id', ''),
        title=track.get('title', ''),
        artist=full,
        length=track.get('length'),
        release_id=release.get('id') if release else None,
        release_title=release.get('title') if release else None,
        date=normalize_release_date(release.get('date')) if release else None,
        similarity=round(ev.similarity, 4),
        fingerprint_score=ev.fingerprint_score,
        reasons=tuple(ev.conflicts),
        releases=tuple(
            ReleaseSummary(
                release_id=r.get('id', ''),
                title=r.get('title', ''),
                date=normalize_release_date(r.get('date')),
                status=r.get('status') or '',
                primary_type=(r.get('release-group') or {}).get('primary-type', ''),
                secondary_types=tuple((r.get('release-group') or {}).get('secondary-types') or []),
            )
            for r in track.get('releases') or []
        ),
        versions=tuple(
            sorted(split_title(track.get('title', '')).versions | version_categories(track.get('disambiguation', '')))
        ),
        disambiguation=track.get('disambiguation', ''),
        node=track or None,
        evidence=tuple(
            label
            for present, label in (
                (ev.confirmed_by_identifier, EVIDENCE_IDENTIFIER),
                (ev.artist in {Evidence.MATCH, Evidence.PARTIAL}, EVIDENCE_ARTIST),
                (ev.title == Evidence.MATCH, EVIDENCE_TITLE),
                (ev.version == Evidence.MATCH, EVIDENCE_VERSION),
                (ev.length == Evidence.MATCH, EVIDENCE_LENGTH),
            )
            if present
        ),
    )


# ---------------------------------------------------------------------------
# Release selection
# ---------------------------------------------------------------------------

_UNWANTED_SECONDARY_TYPES = frozenset({'Compilation', 'DJ-mix', 'Mixtape/Street'})
_STATUS_RANK = {'official': 3, '': 2, 'promotion': 1, 'bootleg': 0, 'pseudo-release': 0}


def normalize_release_date(value: object) -> str:
    """Return an ISO partial date from MusicBrainz strings or AcoustID mappings.

    Missing components are zero, as in Picard dates; trailing unknown components
    are omitted. Invalid input is an unknown date, never a stringified object.
    """
    if isinstance(value, str):
        if not re.fullmatch(r'[0-9]{4}(?:-[0-9]{1,2}){0,2}', value.strip()):
            return ''
        parts = value.strip().split('-')
    elif isinstance(value, Mapping):
        parts = [value.get(key) for key in ('year', 'month', 'day')]
    else:
        return ''

    numbers = []
    for part in parts:
        if part is None or part == '':
            numbers.append(0)
        elif type(part) is int:
            numbers.append(part)
        elif isinstance(part, str) and re.fullmatch(r'[0-9]+', part):
            # Bound the conversion, including malformed provider values.
            if len(part) > 4:
                return ''
            numbers.append(int(part))
        else:
            return ''
    numbers.extend([0] * (3 - len(numbers)))
    year, month, day = numbers
    if not (1 <= year <= 9999 and 0 <= month <= 12 and 0 <= day <= 31):
        return ''
    if month and day:
        try:
            calendar_date(year, month, day)
        except ValueError:
            return ''
    if day:
        return f'{year:04d}-{month:02d}-{day:02d}'
    if month:
        return f'{year:04d}-{month:02d}'
    return f'{year:04d}'


def release_year(value: object) -> str:
    """'2021' from '2021-04-09', '2021-04' or '2021'; '' when not a release date."""
    return normalize_release_date(value)[:4]


def first_release_date(track: dict) -> str:
    """First release date of a confirmed recording, as an ISO (partial) date, or ''.

    MusicBrainz' own first-release-date of the recording comes first. Otherwise
    the earliest dated release listed for the recording, leaving compilations
    out: their date is a re-release, not the song's first release.
    """
    date = normalize_release_date(track.get('first-release-date'))
    if date:
        return date
    dates = [
        normalize_release_date(release.get('date'))
        for release in track.get('releases') or ()
        if isinstance(release, dict) and 'Compilation' not in _release_types(release)
    ]
    dates = [date for date in dates if date]
    return max(dates, key=_date_sort_value) if dates else ''


def _date_sort_value(date: object) -> int:
    """Earlier dates rank higher; missing dates rank lowest."""
    date = normalize_release_date(date)
    if not date:
        return -99999999
    return -int((date.replace('-', '') + '0000')[:8])


def _release_types(release: dict) -> list[str]:
    rg = release.get('release-group') or {}
    types = [rg['primary-type']] if rg.get('primary-type') else []
    return types + list(rg.get('secondary-types') or [])


def _preference_score(release: dict, config) -> float:
    parts: list[tuple[float, int]] = []
    setting = config.setting
    _weights_from_preferred_countries(parts, release, setting['preferred_release_countries'], 1)
    _weights_from_preferred_formats(parts, release, setting['preferred_release_formats'], 1)
    _weights_from_preferred_release_types(
        parts, release, setting['preferred_release_types'], setting['discouraged_release_types'], 1
    )
    scored = [score for score, weight in parts if weight == 1]
    return sum(scored) / len(scored) if scored else 0.5


_EDITION_RE = re.compile(
    r'\b(?:edition|deluxe|expanded|anniversary|remaster(?:ed)?|reissue|special|collector s|bonus|limited|version)\b'
)


def album_base(title: str | None) -> str:
    """Album title without edition qualifiers ("(Deluxe Edition)",
    " - 20th Anniversary Edition", "[Remastered]")."""
    title = title or ''

    def strip_bracket(m):
        return ' ' if _EDITION_RE.search(fold(m.group(1))) else m.group(0)

    base = _BRACKET_RE.sub(strip_bracket, title)
    m = _DASH_SUFFIX_RE.search(base)
    if m and _EDITION_RE.search(fold(m.group(1))):
        base = base[: m.start()]
    return base.strip()


def _album_similarity(hints: FileHints, release: dict) -> float:
    if not hints.album:
        return 0.0
    file_album, release_album = hints.album, release.get('title', '')
    return max(
        text_similarity(file_album, release_album),
        text_similarity(album_base(file_album), album_base(release_album)),
    )


@dataclass
class ReleaseChoice:
    release: dict | None
    verified: bool
    notes: tuple[Reason, ...] = ()
    conflict: bool = False


def select_release(match: SimMatchTrack, hints: FileHints, scoring_metadata: Metadata, weights, config=None):
    """Pick the release for an already selected recording.

    The recording is never changed. Ranking, most significant first:
    1. release matches the file's album MBID, barcode or album title
    2. not a configured discouraged release type
    3. release status: official > unknown > promotion > bootleg/pseudo
    4. if the file is tagged as a compilation (Various Artists album artist,
       compilation=1): a compilation release; otherwise not a
       compilation/DJ-mix/mixtape, unless the user prefers that type
    5. configured preferred countries / formats / release types
    6. earliest supported release date, preferring a single on the same date
    7. Picard's release similarity, then release id (stable)

    An unmatched album is a release note, not a recording rejection.
    Without existing release evidence, only an unambiguous original official
    single is verified automatically. Other releases remain review suggestions.
    """
    config = config or get_config()
    track = match.track or {}
    releases = list(track.get('releases') or [])
    if not releases:
        return ReleaseChoice(release=None, verified=False, notes=(Reason.RELEASE_UNVERIFIED,))

    discouraged = set(config.setting['discouraged_release_types'])
    preferred = set(config.setting['preferred_release_types'])
    accept_compilations = bool(preferred & _UNWANTED_SECONDARY_TYPES)

    def compilation_rank(types):
        is_compilation = bool(types & _UNWANTED_SECONDARY_TYPES)
        if hints.is_compilation:
            return 1 if is_compilation else 0
        return 1 if accept_compilations or not is_compilation else 0

    def key(release):
        types = set(_release_types(release))
        album_id_matches = release.get('id') in hints.album_ids
        barcode_matches = bool(hints.barcode) and hints.barcode == release.get('barcode')
        album_sim = _album_similarity(hints, release)
        if album_id_matches:
            album_level = 3
        elif barcode_matches:
            album_level = 2
        elif album_sim >= ALBUM_MATCH_THRESHOLD:
            album_level = 1
        else:
            album_level = 0
        status = (release.get('status') or '').lower()
        single = dict(track)
        single['releases'] = [release]
        picard_sim = compare_to_track(scoring_metadata, single, weights).similarity
        return (
            album_level,
            0 if types & discouraged else 1,
            _STATUS_RANK.get(status, 2),
            compilation_rank(types),
            round(_preference_score(release, config), 6),
            _date_sort_value(release.get('date')),
            1 if 'Single' in types else 0,
            round(picard_sim, 6),
            release.get('id', ''),
        )

    keyed = sorted(((key(r), r) for r in releases), key=lambda kr: kr[0], reverse=True)
    best_key, best = keyed[0]
    identified = best_key[0] > 0
    # An unsupported album must never invalidate an independently confirmed
    # recording. Keep the release only as a review suggestion.
    conflict = bool(hints.album and not identified)
    equivalent = [r for rank, r in keyed if rank[:-1] == best_key[:-1]]
    unique_release = bool(
        best.get('id') and (best.get('release-group') or {}).get('id')
    ) and len({r.get('id') for r in equivalent}) == 1
    types = set(_release_types(best))
    official_single = (
        (best.get('status') or '').lower() == 'official'
        and 'Single' in types
        and not types & _UNWANTED_SECONDARY_TYPES
        and not types & discouraged
        and bool(normalize_release_date(best.get('date')))
    )
    official_releases = [
        r for r in releases
        if (r.get('status') or '').lower() == 'official'
        and not set(_release_types(r)) & (_UNWANTED_SECONDARY_TYPES | discouraged)
    ]
    official_dates = [
        normalize_release_date(r.get('date')) for r in official_releases
        if normalize_release_date(r.get('date'))
    ]
    earliest = bool(official_dates) and _date_sort_value(best.get('date')) == max(
        _date_sort_value(d) for d in official_dates
    )
    best_date = normalize_release_date(best.get('date'))
    best_group = (best.get('release-group') or {}).get('id')
    chronology_clear = True
    for other in official_releases:
        if other is best:
            continue
        other_date = normalize_release_date(other.get('date'))
        other_group = (other.get('release-group') or {}).get('id')
        # Unknown dates and overlapping partial dates cannot establish which
        # release came first. A full same-day date supports a simultaneous single.
        if not other_date:
            if not best_group or best_group != other_group:
                chronology_clear = False
        elif other_date == best_date:
            if len(best_date) < 10 and (not best_group or best_group != other_group):
                chronology_clear = False
        elif other_date.startswith(best_date) or best_date.startswith(other_date):
            chronology_clear = False
    verified = unique_release and (
        identified or (
            not (hints.album or hints.album_ids or hints.barcode)
            and official_single and earliest and chronology_clear
        )
    )
    notes: list[Reason] = []
    if not verified:
        notes.append(Reason.RELEASE_UNVERIFIED)
    if conflict:
        notes.append(Reason.ALBUM_CONFLICT)
    groups = {(r.get('release-group') or {}).get('id') for r in releases}
    if len(groups) > 1:
        notes.append(Reason.COMPETING_RELEASES)

    file_year = extract_year_from_date(hints.date) if hints.date else None
    if file_year is not None:
        release_year = extract_year_from_date(normalize_release_date(best.get('date')))
        years = [y for y in (extract_year_from_date(normalize_release_date(r.get('date'))) for r in releases) if y is not None]
        original_year = min(years) if years else None
        # A differing date is only noted, never treated as an error: the file
        # may carry the original release year, the release year or neither.
        if release_year is not None and file_year != release_year and file_year != original_year:
            notes.append(Reason.DATE_DIFFERS)
    return ReleaseChoice(release=best, verified=verified, notes=tuple(notes), conflict=conflict)


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


def _error_reason(error) -> Reason:
    return Reason.NETWORK_ERROR if error else Reason.FINGERPRINT_ERROR


def decide(
    source: Source,
    document,
    error,
    metadata: Metadata,
    base_filename: str,
    weights,
    thresholds: SmartScanThresholds | None = None,
    config=None,
    cancelled: bool = False,
) -> Decision:
    """Evaluate one Smart Scan stage result and return a Decision.

    document/error are what the AcoustID client or MusicBrainz search passed
    to the lookup callback. For AcoustID a document without a 'recordings' key
    means fingerprinting or the service failed (not "no match").
    """
    config = config or get_config()
    thresholds = thresholds or SmartScanThresholds.from_config(config)
    if cancelled:
        return Decision(Outcome.CANCELLED, source)
    if error:
        return Decision(Outcome.ERROR, source, reasons=(Reason.NETWORK_ERROR,), details={'error': str(error)})
    try:
        tracks = document['recordings']
    except (KeyError, TypeError):
        reason = Reason.FINGERPRINT_ERROR if source == Source.ACOUSTID else Reason.NETWORK_ERROR
        return Decision(Outcome.ERROR, source, reasons=(reason,))
    if not tracks:
        return Decision(Outcome.NO_MATCH, source, reasons=(Reason.NO_CANDIDATES,))

    hints = file_hints(metadata, base_filename)
    matches = dedupe_matches(
        recording_match(hints_metadata(metadata, candidate_filename_hints(hints, track)), track, weights)
        for track in tracks
    )
    if not matches:
        return Decision(Outcome.NO_MATCH, source, reasons=(Reason.NO_CANDIDATES,))
    evidence = [assess_candidate(m, source, candidate_filename_hints(hints, m.track), thresholds) for m in matches]
    evidence.sort(key=lambda ev: (ev.confirmed_by_identifier, ev.similarity), reverse=True)
    candidates = tuple(summarize(ev) for ev in evidence[:MAX_RETAINED_CANDIDATES])
    top = evidence[0]

    def result(outcome, reasons, **kwargs):
        return Decision(outcome, source, reasons=tuple(reasons), candidates=candidates, **kwargs)

    if source == Source.ACOUSTID:
        fp = top.fingerprint_score
        if fp is not None and fp < thresholds.acoustid_min_score:
            # Not usable as evidence. Keep it as a weak candidate only if
            # nothing contradicts it.
            weak = top if not top.conflicts else None
            return result(Outcome.NO_MATCH, [Reason.LOW_FINGERPRINT_SCORE], weak=weak)

    if top.conflicts:
        return result(Outcome.NEEDS_REVIEW, top.conflicts)

    # Ambiguity: compare against the best *plausible* competing recording.
    competitors = [
        ev
        for ev in evidence[1:]
        if not ev.conflicts
        and not (top.confirmed_by_identifier and not ev.confirmed_by_identifier)
        and not (
            source == Source.ACOUSTID
            and ev.fingerprint_score is not None
            and ev.fingerprint_score < thresholds.acoustid_min_score
        )
    ]
    if competitors and top.similarity - competitors[0].similarity < thresholds.min_margin:
        return result(Outcome.NEEDS_REVIEW, [Reason.CLOSE_SCORES])

    notes: list[Reason] = []
    if top.confirmed_by_identifier:
        notes.append(Reason.CONFIRMED_BY_IDENTIFIER)
    elif source == Source.ACOUSTID:
        fp = top.fingerprint_score if top.fingerprint_score is not None else 0.0
        strong = fp >= thresholds.acoustid_strong_score
        identity_unknown = top.title == Evidence.UNKNOWN and top.artist == Evidence.UNKNOWN
        if identity_unknown:
            sufficient = strong and top.length == Evidence.MATCH
        elif strong:
            sufficient = top.length != Evidence.MISMATCH
        else:
            sufficient = (
                top.title == Evidence.MATCH
                and top.artist in {Evidence.MATCH, Evidence.PARTIAL}
                and top.length == Evidence.MATCH
            )
        if not sufficient:
            return result(Outcome.NO_MATCH, [Reason.INSUFFICIENT_EVIDENCE], weak=top)
    else:
        if top.similarity < thresholds.metadata_min_similarity:
            return result(Outcome.NO_MATCH, [Reason.LOW_SIMILARITY])
        if top.title != Evidence.MATCH or top.artist not in {Evidence.MATCH, Evidence.PARTIAL}:
            # Missing information is not proof of a match.
            return result(Outcome.NO_MATCH, [Reason.INSUFFICIENT_EVIDENCE])
        if top.length == Evidence.CLOSE:
            return result(Outcome.NEEDS_REVIEW, [Reason.LENGTH_MISMATCH])
        if top.length != Evidence.MATCH:
            return result(Outcome.NEEDS_REVIEW, [Reason.INSUFFICIENT_EVIDENCE])

    hints = candidate_filename_hints(hints, top.match.track)
    scoring = hints_metadata(metadata, hints)
    choice = select_release(top.match, hints, scoring, weights, config)
    notes.extend(choice.notes)
    candidates = (summarize(top, choice.release),) + candidates[1:]
    return Decision(
        Outcome.MATCHED,
        source,
        notes=tuple(notes),
        match=top.match,
        release=choice.release,
        release_verified=choice.verified,
        candidates=candidates,
        details={'fingerprint_score': top.fingerprint_score, 'source_labels': hints.source_labels},
    )


def _same_song(a: CandidateEvidence, b: SimMatchTrack, thresholds: SmartScanThresholds) -> bool:
    ta, tb = a.match.track or {}, b.track or {}
    if ta.get('id') and ta.get('id') == tb.get('id'):
        return True
    pa, pb = split_title(ta.get('title', '')), split_title(tb.get('title', ''))
    if fold(pa.base) != fold(pb.base) or pa.versions != pb.versions:
        return False
    names_a = candidate_artist_names(ta)[1]
    names_b = candidate_artist_names(tb)[1]
    if not any(name_similarity(x, y) >= NAME_MATCH_THRESHOLD for x in names_a for y in names_b):
        return False
    return length_evidence(ta.get('length') or 0, tb.get('length'), thresholds) == Evidence.MATCH


# Review reasons where the fingerprint's recording disagrees with the file's
# own artist, title or version: a text search may find the file's song.
_IDENTITY_DISAGREEMENT = frozenset({Reason.ARTIST_CONFLICT, Reason.TITLE_CONFLICT, Reason.VERSION_MISMATCH})


def metadata_lookup_useful(decision: Decision, hints: FileHints) -> bool:
    """Whether the single metadata Lookup follows an AcoustID decision.

    Always after No Match or Error. After Needs Review only when the
    fingerprint's recording disagrees with the file's artist, title or version
    and both are known, so the search can offer the file's song for review. A
    duration-only or ambiguous review would only find the same recording again.
    """
    if decision.outcome in {Outcome.NO_MATCH, Outcome.ERROR}:
        return True
    return bool(
        decision.outcome == Outcome.NEEDS_REVIEW
        and hints.artist
        and hints.title
        and _IDENTITY_DISAGREEMENT & set(decision.reasons)
    )


SUGGESTION_TAGS = ('artist', 'title')
PROVENANCE_MUSICBRAINZ = 'MusicBrainz'
PROVENANCE_FILENAME = 'Filename'


@dataclass(frozen=True)
class TagSuggestion:
    """An unverified New Tags value after a Smart Scan without a match.

    It is only shown; it is never applied or saved, and it is stale once the
    field no longer holds values_then.
    """

    value: str
    provenance: str  # PROVENANCE_MUSICBRAINZ or PROVENANCE_FILENAME
    values_then: tuple[str, ...]
    # The field held only Picard's own file-name guess (guess_tracknumber_and_title).
    replaces_guess: bool = False
    recording_id: str = ''


def _plausible_recordings(decision: Decision) -> list[CandidateSummary]:
    """Recordings whose artist and title match the file with no artist, title
    or version conflict. None when the fingerprint contradicts the text."""
    if Reason.SOURCE_DISAGREEMENT in decision.reasons:
        return []
    plausible: dict[str, CandidateSummary] = {}
    for candidate in decision.candidates:
        if (
            candidate.node
            and candidate.recording_id not in plausible
            and not _IDENTITY_DISAGREEMENT & set(candidate.reasons)
            and {EVIDENCE_ARTIST, EVIDENCE_TITLE} <= set(candidate.evidence)
        ):
            plausible[candidate.recording_id] = candidate
    return list(plausible.values())


def _canonical_values(decision: Decision, credits: Mapping[str, dict]):
    """MusicBrainz artist/title that every plausible recording agrees on.

    Returns ({tag: (value, recording id)}, recording id whose artist credit
    must be looked up, or None). AcoustID recordings carry no credit join
    phrases, so a multi-artist credit is only taken from a MusicBrainz node.
    """
    plausible = _plausible_recordings(decision)
    values = {}
    titles = {candidate.node.get('title', '') for candidate in plausible}
    if len(titles) == 1 and next(iter(titles)):
        values['title'] = (titles.pop(), plausible[0].recording_id)
    artist_lists = set()
    credited = []
    for candidate in plausible:
        node = credits.get(candidate.recording_id) or candidate.node
        metadata = Metadata()
        recording_to_metadata(node, metadata)
        artist_lists.add(tuple(metadata.getall('artists')))
        if (
            candidate.source == Source.METADATA
            or candidate.recording_id in credits
            or len(node.get('artist-credit') or []) == 1
        ):
            credited.append((metadata['artist'], candidate.recording_id))
    lookup = None
    if len(artist_lists) == 1:
        credit_values = {artist for artist, _recording_id in credited}
        if len(credit_values) == 1:
            values['artist'] = credited[0]
        elif not credited:
            lookup = plausible[0].recording_id
    return values, lookup


def tag_suggestions(
    decision: Decision,
    metadata: Metadata,
    base_filename: str,
    manual_tags: Iterable[str] = (),
    credits: Mapping[str, dict] | None = None,
) -> tuple[dict[str, TagSuggestion], str | None]:
    """Unverified Artist/Title for New Tags when Smart Scan found no match.

    Only for fields that are empty or hold Picard's file-name guess, and not
    manually edited. MusicBrainz values are used only when every plausible
    candidate agrees; otherwise the value parsed from the file name. Returns
    (suggestions, recording id whose MusicBrainz credit is still needed).
    """
    if decision.outcome in {Outcome.MATCHED, Outcome.CANCELLED}:
        return {}, None
    canonical, lookup = _canonical_values(decision, credits or {})
    hints = file_hints(metadata, base_filename)
    from_name = {
        'artist': hints.artist if hints.artist_source == 'filename' else '',
        'title': hints.title if hints.title_source == 'filename' else '',
    }
    guess = tracknum_and_title_from_filename(base_filename).title if base_filename else ''
    manual_tags = set(manual_tags)
    suggestions = {}
    for tag in SUGGESTION_TAGS:
        values_then = tuple(metadata.getall(tag))
        replaces_guess = tag == 'title' and bool(guess) and list(values_then) == [guess]
        if tag in manual_tags or (any(values_then) and not replaces_guess):
            continue
        if tag in canonical:
            (value, recording_id), provenance = canonical[tag], PROVENANCE_MUSICBRAINZ
        else:
            value, recording_id, provenance = from_name[tag], '', PROVENANCE_FILENAME
        if value and [value] != list(values_then):
            suggestions[tag] = TagSuggestion(value, provenance, values_then, replaces_guess, recording_id)
    if 'artist' not in suggestions or suggestions['artist'].provenance == PROVENANCE_MUSICBRAINZ:
        lookup = None
    return suggestions, lookup


# Recording fields of a MusicBrainz recording lookup that replace the same
# fields of an AcoustID recording node, whose credits lack join phrases.
_MUSICBRAINZ_RECORDING_FIELDS = ('artist-credit', 'title', 'first-release-date', 'length', 'disambiguation', 'video')


def _canonical_node(candidate: CandidateSummary, credits: Mapping[str, dict]) -> dict | None:
    """The candidate's recording node with a complete MusicBrainz artist credit,
    or None while that credit is unknown."""
    node = candidate.node
    looked_up = credits.get(candidate.recording_id)
    if looked_up:
        merged = dict(node)
        merged.update({key: looked_up[key] for key in _MUSICBRAINZ_RECORDING_FIELDS if key in looked_up})
        return merged
    if candidate.source == Source.METADATA or len(node.get('artist-credit') or []) <= 1:
        return node
    return None


@dataclass(frozen=True)
class ConfirmableRecording:
    """A reviewed recording the user may confirm (Confirm Results)."""

    recording_id: str
    artist: str  # canonical MusicBrainz credit, '' while unknown
    title: str
    reasons: tuple[Reason, ...]
    plausible: bool  # no artist, title or version conflict with the file
    similarity: float

    @property
    def confirmable(self) -> bool:
        return bool(self.artist and self.title)


def confirmable_recordings(decision: Decision | None, credits: Mapping[str, dict] | None = None):
    """Recordings of a Needs Review decision, plausible ones first. Only the
    scan's own candidates; nothing is searched again."""
    if decision is None or decision.outcome != Outcome.NEEDS_REVIEW or decision.user_confirmed:
        return []
    credits = credits or {}
    plausible = {candidate.recording_id for candidate in _plausible_recordings(decision)}
    result, seen = [], set()
    for candidate in decision.candidates:
        if not candidate.node or not candidate.recording_id or candidate.recording_id in seen:
            continue
        seen.add(candidate.recording_id)
        node = _canonical_node(candidate, credits)
        artist = ''
        if node is not None:
            metadata = Metadata()
            recording_to_metadata(node, metadata)
            artist = metadata['artist']
        result.append(ConfirmableRecording(
            candidate.recording_id, artist, (node or candidate.node).get('title', '') or candidate.title,
            tuple(candidate.reasons), candidate.recording_id in plausible, candidate.similarity,
        ))
    return sorted(result, key=lambda r: not r.plausible)


def preselected_recording(recordings) -> str | None:
    """The recording to preselect: only when exactly one is plausible."""
    plausible = [r for r in recordings if r.plausible and r.confirmable]
    return plausible[0].recording_id if len(plausible) == 1 else None


def confirmed_decision(
    review: Decision, recording_id: str, metadata: Metadata, base_filename: str, weights,
    credits: Mapping[str, dict] | None = None, config=None,
) -> Decision | None:
    """The user-confirmed recording of a Needs Review decision, or None.

    The review reasons stay as notes (a duration or version conflict is not
    resolved). A release is kept only when select_release() verifies it.
    """
    if review.outcome != Outcome.NEEDS_REVIEW or review.user_confirmed:
        return None
    candidate = next((c for c in review.candidates if c.recording_id == recording_id and c.node), None)
    node = _canonical_node(candidate, credits or {}) if candidate is not None else None
    if node is None:
        return None
    hints = candidate_filename_hints(file_hints(metadata, base_filename), node)
    match = SimMatchTrack(candidate.similarity, None, None, node)
    choice = select_release(match, hints, hints_metadata(metadata, hints), weights, config)
    return Decision(
        Outcome.MATCHED,
        candidate.source,
        notes=tuple(dict.fromkeys(tuple(review.reasons) + tuple(candidate.reasons) + choice.notes)),
        match=match,
        release=choice.release if choice.verified else None,
        release_verified=choice.verified,
        candidates=review.candidates,
        details=dict(review.details),
        user_confirmed=True,
    )


def _combine_after_review(acoustid: Decision, metadata: Decision) -> Decision:
    """The fingerprint's recording conflicts with the file's artist/title/version.

    The text search used that same file evidence, so it can neither confirm
    nor overrule the fingerprint. It only adds recordings for review.
    """
    fingerprinted = {c.recording_id for c in acoustid.candidates}
    added = tuple(c for c in metadata.candidates if c.recording_id not in fingerprinted)
    candidates = tuple(acoustid.candidates) + added
    if metadata.outcome == Outcome.CANCELLED:
        metadata.candidates = candidates
        return metadata
    reasons = tuple(acoustid.reasons)
    if any(not c.reasons for c in added):
        reasons += (Reason.SOURCE_DISAGREEMENT,)
    return Decision(
        Outcome.NEEDS_REVIEW,
        Source.ACOUSTID,
        reasons=reasons,
        notes=acoustid.notes,
        candidates=candidates,
        details=dict(acoustid.details),
    )


def combine(acoustid: Decision | None, metadata: Decision, thresholds: SmartScanThresholds | None = None) -> Decision:
    """Final decision after the metadata fallback ran.

    After No Match or Error, fingerprint evidence is not discarded: a weak
    fingerprint candidate that disagrees with the metadata result turns a
    metadata match into Needs Review. After Needs Review (see
    metadata_lookup_useful) the result stays Needs Review.
    """
    if acoustid is not None and acoustid.outcome == Outcome.NEEDS_REVIEW:
        return _combine_after_review(acoustid, metadata)
    thresholds = thresholds or SmartScanThresholds.from_config()
    acoustid_candidates = acoustid.candidates if acoustid else ()
    candidates = tuple(acoustid_candidates) + tuple(metadata.candidates)
    weak = acoustid.weak if acoustid else None

    if metadata.outcome == Outcome.MATCHED:
        if weak is not None and metadata.match is not None and not _same_song(weak, metadata.match, thresholds):
            return Decision(
                Outcome.NEEDS_REVIEW,
                Source.METADATA,
                reasons=(Reason.SOURCE_DISAGREEMENT,),
                candidates=candidates,
            )
        metadata.candidates = tuple(metadata.candidates) + tuple(acoustid_candidates)
        return metadata
    if metadata.outcome in {Outcome.ERROR, Outcome.CANCELLED}:
        metadata.candidates = candidates
        return metadata
    if weak is not None and acoustid is not None:
        reasons = tuple(acoustid.reasons) + tuple(r for r in metadata.reasons if r not in acoustid.reasons)
        return Decision(Outcome.NEEDS_REVIEW, Source.ACOUSTID, reasons=reasons, candidates=candidates)
    if metadata.outcome == Outcome.NEEDS_REVIEW:
        metadata.candidates = candidates
        return metadata
    errored = [d for d in (acoustid, metadata) if d is not None and d.outcome == Outcome.ERROR]
    if errored:
        reasons = tuple(dict.fromkeys(r for d in errored for r in d.reasons))
        return Decision(Outcome.ERROR, metadata.source, reasons=reasons, candidates=candidates)
    reasons = tuple(dict.fromkeys(tuple(acoustid.reasons if acoustid else ()) + tuple(metadata.reasons)))
    return Decision(Outcome.NO_MATCH, Source.METADATA, reasons=reasons, candidates=candidates)


# ---------------------------------------------------------------------------
# Metadata protection
# ---------------------------------------------------------------------------

# Never copy an uncertain release's fields into a recording-only match.
RELEASE_SPECIFIC_TAGS = frozenset(
    {
        'album', 'albumartist', 'albumartists', 'albumartistsort', 'date', 'originaldate', 'originalyear',
        'tracknumber', 'totaltracks', 'totalalbumtracks', 'discnumber', 'totaldiscs', 'discsubtitle',
        'musicbrainz_albumid', 'musicbrainz_releasegroupid', 'musicbrainz_albumartistid',
        'musicbrainz_trackid', 'releasecountry', 'releasetype', 'releasestatus', 'media',
        'label', 'catalognumber', 'barcode', 'compilation',
    }
)

# Tags restored from the pre-match New Tags when the match provides no value.
PROTECTED_TAGS = frozenset(
    {
        'bpm',
        '~rating',
        'rating',
        'comment',
        'genre',
        'mood',
        'key',
        'grouping',
        'isrc',
        'lyrics',
        'acoustid_id',
        'acoustid_fingerprint',
        'encodedby',
        'encodersettings',
        'copyright',
        'license',
        'website',
    }
)
_PROTECTED_PREFIXES = ('comment:', 'lyrics:', 'replaygain_', 'r128_', 'performer:')


def is_protected_tag(name: str) -> bool:
    """User-specific, technical or custom tag that recognition must not drop.

    MusicBrainz identity and release fields (title, album, date, track numbers,
    MBIDs, ...) are deliberately excluded: restoring them from a different
    release would mix two releases in one file.
    """
    if not name:
        return False
    lowered = name.lower()
    if lowered in PROTECTED_TAGS or lowered.startswith(_PROTECTED_PREFIXES):
        return True
    if lowered.startswith('~'):
        return False
    # Custom tags: anything Picard does not know about.
    return ALL_TAGS.tagvar_from_name(lowered) is None


def protected_snapshot(metadata: Metadata) -> dict[str, list[str]]:
    return {name: list(values) for name, values in metadata.rawitems() if values and is_protected_tag(name)}


def restore_protected(target: Metadata, snapshot: dict[str, list[str]]) -> list[str]:
    """Add snapshot values for tags the new metadata leaves empty.

    Populated tags are never replaced, and tags the user deleted are not
    re-added. Returns the restored tag names.
    """
    restored = []
    for name, values in snapshot.items():
        if name in target.deleted_tags or target.getall(name):
            continue
        target[name] = list(values)
        restored.append(name)
    return restored


__all__ = [
    'CandidateSummary',
    'Decision',
    'Evidence',
    'Outcome',
    'Reason',
    'SmartScanThresholds',
    'Source',
    'ConfirmableRecording',
    'combine',
    'confirmable_recordings',
    'confirmed_decision',
    'decide',
    'decision_label',
    'file_hints',
    'is_protected_tag',
    'metadata_lookup_useful',
    'preselected_recording',
    'tag_suggestions',
    'protected_snapshot',
    'restore_protected',
    'select_release',
]
