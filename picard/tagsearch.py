# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""Explicit, asynchronous field suggestions from every configured provider.

Queries never write metadata. Each provider runs independently through
Picard's rate-limited web service; results are merged by normalized value
while keeping each provider's own evidence. Apply changes one field only.
"""

from collections import Counter
from dataclasses import (
    dataclass,
    field,
)
from functools import partial
import re
import unicodedata

from PyQt6 import QtCore
from PyQt6.QtNetwork import QNetworkReply

from picard.config import get_config
from picard.discogsgenre import (
    discogs_get,
    exact_single_track_genres,
    folded,
    same_text,
)
from picard.i18n import (
    N_,
    gettext as _,
    ngettext,
)
from picard.item import MetadataItem
from picard.mbjson import (
    medium_to_metadata,
    recording_to_metadata,
    release_group_to_metadata,
    release_to_metadata,
)
from picard.metadata import Metadata
from picard.smartgenre import (
    filtered_genres,
    genre_entries,
    genres_from_node,
)
from picard.smartscan import (
    Outcome,
    _BRACKET_RE,
    file_hints,
    normalize_release_date,
    version_categories,
)
from picard.tagprovenance import set_source
from picard.util import format_time


RECORDING_FIELDS = frozenset({
    'artist', 'artists', 'artistsort', 'title', 'isrc', 'musicbrainz_artistid', 'musicbrainz_recordingid',
})
RELEASE_FIELDS = frozenset({
    'album', 'albumartist', 'albumartists', 'albumartistsort', 'date', 'originaldate', 'originalyear',
    'musicbrainz_albumid', 'musicbrainz_releasegroupid', 'musicbrainz_albumartistid', 'musicbrainz_trackid',
    'releasecountry', 'releasetype', 'releasestatus', 'barcode', 'catalognumber', 'label', 'asin',
    'tracknumber', 'discnumber', 'discsubtitle', 'totaltracks', 'totaldiscs', 'media',
})
# Fields that need each release's tracklist to find the searched recording.
TRACK_FIELDS = frozenset({
    'musicbrainz_trackid', 'tracknumber', 'discnumber', 'discsubtitle', 'totaltracks', 'totaldiscs', 'media',
})

MUSICBRAINZ_FIELDS = RECORDING_FIELDS | RELEASE_FIELDS | {'subtitle', 'genre'}
# Discogs search results describe releases; track genre/style needs a
# verified single-track release (see picard.discogsgenre).
DISCOGS_FIELDS = frozenset({
    'album', 'date', 'label', 'catalognumber', 'barcode', 'genre', 'style',
})
LASTFM_FIELDS = frozenset({'artist', 'title', 'album', 'genre'})
SUPPORTED_FIELDS = MUSICBRAINZ_FIELDS | DISCOGS_FIELDS | LASTFM_FIELDS

PROVIDERS = ('MusicBrainz', 'Discogs', 'Last.fm')

# Request bounds. Picard's web service additionally enforces per-host delays.
MB_SEARCH_PAGE = 10
MB_SEARCH_MAX = 50
MB_BROWSE_PAGE = 25
MB_BROWSE_MAX = 150
DISCOGS_PAGE = 25
DISCOGS_MAX_PAGES = 4
DISCOGS_VERIFY_PER_ROUND = 3
ROUND_DEADLINE_MS = 30000

STATUS_LABELS = {
    'new': N_('Waiting…'),
    'searching': N_('Searching…'),
    'ready': N_('Results returned'),
    'empty': N_('No results'),
    'filtered': N_('No results after genre filters'),
    'not_configured': N_('Provider not configured'),
    'unsupported': N_('Not available for this field'),
    'error': N_('Network error'),
    'cancelled': N_('Cancelled'),
}


def normalize_value(value, tag=''):
    """Comparison key only; the displayed value keeps the provider's spelling."""
    value = unicodedata.normalize('NFKC', value).casefold().replace('’', "'")
    if tag in {'genre', 'style'}:
        value = re.sub(r'[-_\s]+', ' ', value)
    return ' '.join(value.split())


def _year(date):
    match = re.match(r'([0-9]{4})', date or '')
    return match.group(1) if match else ''


def _discogs_artist(name):
    # Discogs disambiguates homonyms with "(2)" and marks name variations with "*".
    return re.sub(r'\s*\(\d+\)$', '', str(name or '').strip().rstrip('*'))


@dataclass(frozen=True)
class Evidence:
    provider: str
    source: str
    description: str
    url: str = ''
    kind: str = ''
    year: str = ''
    exact: bool = True


@dataclass(eq=False)
class TagCandidate:
    """One selectable value. Identity equality keeps selections request-bound."""
    values: tuple[str, ...]
    evidence: list[Evidence] = field(default_factory=list)
    compilation: bool = False
    order: int = 0

    @property
    def providers(self):
        names = {e.provider for e in self.evidence}
        return tuple(name for name in PROVIDERS if name in names)

    @property
    def source(self):
        return ' + '.join(self.providers)

    @property
    def url(self):
        return next((e.url for e in self.evidence if e.url), '')

    @property
    def exact(self):
        return any(e.exact for e in self.evidence)

    @property
    def detail(self):
        """Quiet secondary line, e.g. 'Single · 2024'."""
        kinds = list(dict.fromkeys(e.kind for e in self.evidence if e.kind))
        years = sorted(e.year for e in self.evidence if e.year)
        parts = kinds[:2] + years[:1]
        releases = {e.url for e in self.evidence if e.kind and e.url}
        if len(releases) > 1:
            parts.append(ngettext('%d release', '%d releases', len(releases)) % len(releases))
        if not parts and self.evidence:
            parts.append(self.evidence[0].description)
        if not self.exact:
            parts.append(_('loose match'))
        return ' · '.join(parts)

    def sort_key(self):
        return (not self.exact, self.compilation, -len(self.providers), self.order)

    def matches(self, text):
        text = normalize_value(text)
        if not text:
            return True
        haystack = [*self.values, self.detail, *(e.description for e in self.evidence), self.source]
        return any(text in normalize_value(value) for value in haystack)


def filter_candidates(candidates, text='', provider=''):
    """Search within returned results and optionally restrict to one provider."""
    return [
        candidate for candidate in candidates
        if (not provider or provider in candidate.providers) and candidate.matches(text)
    ]


def recording_identity(file):
    current = file.metadata['musicbrainz_recordingid']
    decision = file.smart_scan_decision
    if current:
        return current
    return decision.recording_id if decision and decision.outcome == Outcome.MATCHED else ''


class ProviderSearch(QtCore.QObject):
    """One provider's independent, paginated query for the request's field."""

    name = ''
    fields = frozenset()

    def __init__(self, request):
        super().__init__(request)
        self.request = request
        self.file = request.file
        self.tag = request.tag
        self.state = 'new'
        self.reason = ''
        self.has_more = False
        self.filtered = 0
        self._task = None
        self._token = None
        self._steps = []

    @property
    def busy(self):
        return self.state == 'searching'

    @property
    def tagger(self):
        return self.file.tagger

    def availability(self):
        """Return (state, reason) when this provider cannot search, else None."""
        if self.tag not in self.fields:
            return 'unsupported', _('Does not supply this field.')
        return None

    def start(self):
        unavailable = self.availability()
        if unavailable:
            self._set(*unavailable)
            return
        self._set('searching')
        self.fetch()

    def load_more(self):
        if self.has_more and not self.busy:
            self.has_more = False
            self._set('searching')
            self.fetch_more()

    def fetch(self):
        raise NotImplementedError

    def fetch_more(self):
        self.done()

    def _send(self, factory, handler):
        token = self._token = object()

        def finished(document, http, error):
            if not self.request.active or self._token is not token:
                return
            self._task = None
            self._token = None
            if not self.request.current():
                self.request.context_changed()
            elif error == QNetworkReply.NetworkError.OperationCanceledError:
                self.abort(_('Cancelled.'), state='cancelled')
            else:
                handler(document, error)

        task = factory(finished)
        if self._token is token:
            self._task = task

    def _get_url(self, url, handler, **kwargs):
        self._send(
            lambda finished: self.tagger.webservice.get_url(
                url=QtCore.QUrl(url), handler=finished, parse_response_type='json',
                priority=True, important=False, **kwargs,
            ),
            handler,
        )

    def add(self, values, evidence, *, compilation=False):
        self.request.add(values, evidence, compilation=compilation)

    def next_step(self):
        """Run queued follow-up requests of this round, then finish it."""
        if self._steps:
            self._steps.pop(0)()
        else:
            self.done()

    def done(self, state=None, reason=''):
        if state is None:
            contributed = any(self.name in c.providers for c in self.request.candidates)
            state = 'ready' if contributed else ('filtered' if self.filtered else 'empty')
        self._set(state, reason)

    def fail(self, reason=''):
        self._steps = []
        self._set('error', reason or _('The request failed.'))

    def abort(self, reason='', *, state='error'):
        self._steps = []
        self._token = None
        task, self._task = self._task, None
        if task is not None:
            self.tagger.webservice.abort_task(task)
        if self.busy:
            self.has_more = False
            self._set(state, reason)

    def _set(self, state, reason=''):
        self.state = state
        self.reason = reason
        self.request.provider_changed(self)


class MusicBrainzSearch(ProviderSearch):
    name = 'MusicBrainz'
    fields = MUSICBRAINZ_FIELDS

    def __init__(self, request):
        super().__init__(request)
        self._offset = 0
        self._hints = file_hints(self.file.metadata, self.file.base_filename)

    def availability(self):
        unavailable = super().availability()
        if unavailable:
            return unavailable
        if self.tag == 'genre' and not self.request.recording_id:
            return 'unsupported', _('Scan first to identify the recording.')
        if not self.request.recording_id and not (self._hints.artist and self._hints.title):
            return 'unsupported', _('Scan first, or enter Artist and Title.')
        return None

    @property
    def mb_api(self):
        return self.tagger.mb_api

    def fetch(self):
        mbid = self.request.recording_id
        if self.tag == 'genre':
            self._fetch_genres()
        elif not mbid:
            self._search_page()
        elif self.tag in RELEASE_FIELDS:
            if self.tag in {'date', 'originaldate', 'originalyear'}:
                self._steps.append(self._browse_page)
                self._lookup_recording()
            else:
                self._browse_page()
        else:
            self._lookup_recording()

    def fetch_more(self):
        if self.request.recording_id and self.tag in RELEASE_FIELDS:
            self._browse_page()
        else:
            self._search_page()

    # Recording lookup and search

    def _lookup_recording(self):
        self._send(
            partial(self.mb_api.get_track_by_id, self.request.recording_id, inc={'artist-credits', 'isrcs'}),
            self._recording_finished,
        )

    def _recording_finished(self, document, error):
        if error or not isinstance(document, dict) or document.get('id') != self.request.recording_id:
            self.fail()
            return
        self._collect_recording(document, exact=True)
        self.next_step()

    def _search_page(self):
        query = dict(track=self._hints.title, artist=self._hints.artist, limit=MB_SEARCH_PAGE)
        if self.file.metadata.length:
            query['qdur'] = str(self.file.metadata.length // 2000)
        if self._offset:
            query['offset'] = self._offset
        self._send(partial(self.mb_api.find_tracks, **query), self._search_finished)

    def _search_finished(self, document, error):
        if error or not isinstance(document, dict):
            self.fail()
            return
        nodes = [node for node in document.get('recordings') or [] if isinstance(node, dict)]
        for node in nodes:
            self._collect_recording(node, exact=self._is_exact(node), search=True)
        self._offset += len(nodes)
        total = document.get('count') if isinstance(document.get('count'), int) else 0
        self.has_more = bool(nodes) and self._offset < min(total, MB_SEARCH_MAX)
        self.done()

    def _is_exact(self, node):
        metadata = Metadata()
        recording_to_metadata(node, metadata)
        return (
            same_text(metadata['title'], self._hints.title)
            and same_text(metadata['artist'], self._hints.artist)
        )

    @staticmethod
    def _context(node):
        metadata = Metadata()
        recording_to_metadata(node, metadata)
        context = '%s — %s' % (metadata['artist'], metadata['title'])
        if node.get('disambiguation'):
            context += ' (' + node['disambiguation'] + ')'
        if node.get('length'):
            context += ' · ' + format_time(node['length'])
        return metadata, context

    def _collect_recording(self, node, *, exact, search=False):
        if not node.get('id'):
            return
        metadata, context = self._context(node)
        url = 'https://musicbrainz.org/recording/' + node['id']
        evidence = partial(Evidence, 'MusicBrainz', 'MusicBrainz recording', context, url, exact=exact)
        if self.tag in RECORDING_FIELDS:
            self.add(metadata.getall(self.tag), evidence())
        elif self.tag == 'subtitle':
            descriptions = [node.get('disambiguation', '')]
            descriptions.extend(m.group(1) for m in _BRACKET_RE.finditer(node.get('title', '')))
            originals = {'original', 'original version', 'original mix'}
            for value in descriptions:
                if version_categories(value) or value.lower().strip() in originals:
                    self.add([value], evidence())
        elif self.tag in {'date', 'originaldate', 'originalyear'}:
            first = normalize_release_date(node.get('first-release-date'))
            if first:
                value = first[:4] if self.tag == 'originalyear' else first
                self.add([value], Evidence(
                    'MusicBrainz', 'MusicBrainz recording first release', _('First release of the recording'),
                    url, year=_year(first), exact=exact,
                ))
        if search and self.tag in RELEASE_FIELDS:
            # Search hits embed their releases; the media list holds only the hit track.
            for release in node.get('releases') or []:
                self._collect_release(release, node['id'], context, exact=exact, hit=True)

    # Releases containing the recording (paginated browse)

    def _browse_page(self):
        inc = {'artist-credits', 'labels', 'release-groups', 'media'}
        if self.tag in TRACK_FIELDS:
            inc.add('recordings')
        self._send(
            partial(
                self.mb_api.browse_releases, inc=inc, recording=self.request.recording_id,
                limit=MB_BROWSE_PAGE, offset=self._offset,
            ),
            self._browse_finished,
        )

    def _browse_finished(self, document, error):
        if error or not isinstance(document, dict):
            self.fail()
            return
        releases = [node for node in document.get('releases') or [] if isinstance(node, dict)]
        for release in releases:
            self._collect_release(release, self.request.recording_id, '', exact=True)
        self._offset += len(releases)
        total = document.get('release-count') if isinstance(document.get('release-count'), int) else 0
        self.has_more = bool(releases) and self._offset < min(total, MB_BROWSE_MAX)
        self.done()

    def _collect_release(self, node, recording_id, context, *, exact, hit=False):
        if not isinstance(node, dict) or not node.get('id'):
            return
        metadata = Metadata()
        normalized = dict(node)
        if 'date' in node:
            normalized['date'] = normalize_release_date(node['date'])
        release_to_metadata(normalized, metadata)
        group = node.get('release-group') or {}
        if group.get('id'):
            group = dict(group)
            if 'first-release-date' in group:
                group['first-release-date'] = normalize_release_date(group['first-release-date'])
            release_group_to_metadata(group, metadata)
        secondary = [t for t in group.get('secondary-types') or [] if isinstance(t, str)]
        compilation = 'Compilation' in secondary
        kind = 'Compilation' if compilation else ' / '.join(filter(None, [group.get('primary-type') or '', *secondary]))
        date = normalized.get('date') or ''
        media = ', '.join(dict.fromkeys(
            m.get('format') for m in node.get('media') or [] if isinstance(m, dict) and m.get('format')
        ))
        details = [node.get('title', ''), date, node.get('country', ''), media]
        if node.get('status') and node['status'] != 'Official':
            details.append(node['status'])
        if context:
            details.insert(0, context)
        evidence = Evidence(
            'MusicBrainz', 'MusicBrainz release', ' · '.join(filter(None, details)),
            'https://musicbrainz.org/release/' + node['id'], kind=kind,
            year=_year(group.get('first-release-date') if self.tag in {'originaldate', 'originalyear'} else date),
            exact=exact,
        )
        if self.tag not in TRACK_FIELDS:
            self.add(metadata.getall(self.tag), evidence, compilation=compilation)
            return
        for medium in node.get('media') or []:
            track_metadata = Metadata(metadata)
            medium_to_metadata(medium, track_metadata)
            tracks = medium.get('track') if hit else medium.get('tracks')
            for track in tracks or []:
                if hit or (track.get('recording') or {}).get('id') == recording_id:
                    if track.get('position') or track.get('number'):
                        track_metadata['tracknumber'] = str(track.get('position') or track.get('number'))
                    if track.get('id'):
                        track_metadata['musicbrainz_trackid'] = track['id']
                    self.add(track_metadata.getall(self.tag), evidence, compilation=compilation)

    # Genres: recording, then the matched release and its release group

    def _genre_inc(self, inc):
        authenticated = MetadataItem.set_genre_inc_params(inc)
        if not inc - {'release-groups'}:
            authenticated = get_config().setting['only_my_genres']
            inc |= {'user-genres', 'user-tags'} if authenticated else {'genres', 'tags'}
        return authenticated

    def _fetch_genres(self):
        decision = self.file.smart_scan_decision
        release = getattr(decision, 'release', None) or {}
        if getattr(decision, 'recording_id', None) == self.request.recording_id and release.get('id'):
            self._steps.append(partial(self._lookup_release_genres, release['id']))
        inc = set()
        authenticated = self._genre_inc(inc)
        self._send(
            partial(self.mb_api.get_track_by_id, self.request.recording_id, inc=inc, mblogin=authenticated),
            self._genre_recording_finished,
        )

    def _add_genres(self, node, source, url, *, exact):
        entries = genre_entries(node)
        values = filtered_genres(genres_from_node(node), separate=True)
        if entries and not values:
            self.filtered += len(entries)
        label = {
            'MusicBrainz recording': _('Recording genre'),
            'MusicBrainz release': _('Release genre (album-level)'),
            'MusicBrainz release group': _('Release group genre (album-level)'),
        }[source]
        for value in values:
            self.add([value], Evidence('MusicBrainz', source, label, url, exact=exact))

    def _genre_recording_finished(self, document, error):
        if error or not isinstance(document, dict) or document.get('id') != self.request.recording_id:
            self.fail()
            return
        url = 'https://musicbrainz.org/recording/' + document['id']
        self._add_genres(document, 'MusicBrainz recording', url, exact=True)
        self.next_step()

    def _lookup_release_genres(self, release_id):
        inc = {'release-groups'}
        authenticated = self._genre_inc(inc)
        self._send(
            partial(self.mb_api.get_release_by_id, release_id, inc=inc, mblogin=authenticated),
            partial(self._genre_release_finished, release_id),
        )

    def _genre_release_finished(self, release_id, document, error):
        if error or not isinstance(document, dict) or document.get('id') != release_id:
            # The recording's own genres, if any, remain valid results.
            self.done(None if any(self.name in c.providers for c in self.request.candidates) else 'error')
            return
        self._add_genres(document, 'MusicBrainz release', 'https://musicbrainz.org/release/' + release_id, exact=False)
        group = document.get('release-group')
        if isinstance(group, dict) and group.get('id'):
            self._add_genres(
                group, 'MusicBrainz release group', 'https://musicbrainz.org/release-group/' + group['id'], exact=False,
            )
        self.done()


class DiscogsSearch(ProviderSearch):
    """Release search by artist + track; genre/style only from verified single-track releases."""

    name = 'Discogs'
    fields = DISCOGS_FIELDS

    def __init__(self, request):
        super().__init__(request)
        self._page = 0
        self._pages = 0
        self._verify = []
        self._verified = 0

    def availability(self):
        unavailable = super().availability()
        if unavailable:
            return unavailable
        settings = get_config().setting
        if not settings['dlmtags_discogs_enabled'] or not settings['dlmtags_discogs_token']:
            return 'not_configured', _('Enable Discogs and add a token in Options → Genres.')
        if not self.request.artist or not self.request.title:
            return 'unsupported', _('Needs Artist and Title.')
        return None

    def _discogs_get(self, path, handler, **queryargs):
        # Token goes in a header, never in the URL or logs. Responses are
        # shared with Smart Genre for this file (see DiscogsResponses).
        self._send(
            partial(
                discogs_get, self.tagger.webservice, path,
                token=get_config().setting['dlmtags_discogs_token'],
                responses=self.file.dlmtags_discogs_responses, **queryargs,
            ),
            handler,
        )

    def fetch(self):
        self._search_page()

    def fetch_more(self):
        if self._verify:
            self._verify_next()
        else:
            self._search_page()

    def _search_page(self):
        self._page += 1
        self._verified = 0
        self._discogs_get(
            '/database/search', self._search_finished, type='release',
            artist=self.request.artist, track=self.request.title, per_page=DISCOGS_PAGE, page=self._page,
        )

    def _artist_match(self, result_artist):
        """Return True for an exact artist, False for a loose one, None for a mismatch."""
        actual = folded(_discogs_artist(result_artist))
        if same_text(self.request.artist, _discogs_artist(result_artist)):
            return True
        first = folded(re.split(r'\s*(?:[,&;/]|\bfeat\.?|\bft\.?|\bx\b)\s*', self.request.artist)[0])
        return False if first and first in actual else None

    def _search_finished(self, document, error):
        if error or not isinstance(document, dict) or not isinstance(document.get('results'), list):
            self.fail()
            return
        pagination = document.get('pagination') if isinstance(document.get('pagination'), dict) else {}
        self._pages = pagination.get('pages') if isinstance(pagination.get('pages'), int) else self._page
        for result in document['results']:
            if isinstance(result, dict):
                self._collect(result)
        if self.tag in {'genre', 'style'}:
            self._verify_next()
            return
        self._finish_round()

    def _collect(self, result):
        artist, sep, album = str(result.get('title') or '').partition(' - ')
        if not sep:
            return
        exact = self._artist_match(artist)
        ident = result.get('id')
        if exact is None or not isinstance(ident, int):
            return
        if self.tag in {'genre', 'style'}:
            listed = result.get('genre' if self.tag == 'genre' else 'style')
            if exact and listed and ident not in self._verify:
                self._verify.append(ident)
            return
        descriptions = [
            d for f in result.get('formats') or [] if isinstance(f, dict)
            for d in f.get('descriptions') or [] if isinstance(d, str)
        ]
        compilation = 'Compilation' in descriptions
        kind = 'Compilation' if compilation else next(
            (k for k in ('Single', 'EP', 'Album', 'LP') if k in descriptions), '',
        ).replace('LP', 'Album')
        year = str(result.get('year') or '')
        values = {
            'album': [album.strip()],
            'date': [year] if _year(year) else [],
            'label': (result.get('label') or [])[:1],
            'catalognumber': [result['catno']] if result.get('catno') and result['catno'] != 'none' else [],
            'barcode': (result.get('barcode') or [])[:1],
        }[self.tag]
        formats = ', '.join(dict.fromkeys(f for f in result.get('format') or [] if isinstance(f, str)))
        uri = str(result.get('uri') or '')
        url = 'https://www.discogs.com' + uri if uri.startswith('/') else ''
        description = ' · '.join(filter(None, [album.strip(), year, result.get('country', ''), formats]))
        self.add(values, Evidence(
            'Discogs', 'Discogs release', description + ' · ' + _('matched by artist and track search'),
            url, kind=kind, year=_year(year), exact=False,
        ), compilation=compilation)

    def _verify_next(self):
        if not self._verify or self._verified >= DISCOGS_VERIFY_PER_ROUND:
            self._finish_round()
            return
        self._verified += 1
        ident = self._verify.pop(0)
        self._discogs_get('/releases/%d' % ident, partial(self._release_finished, ident))

    def _release_finished(self, ident, document, error):
        if error or not isinstance(document, dict):
            self.fail()
            return
        values = exact_single_track_genres(document, self.request.artist, self.request.title, field=self.tag)
        if self.tag == 'genre' and values:
            accepted = filtered_genres(Counter({v.casefold(): 100 for v in values}), separate=True)
            self.filtered += len(values) - len(accepted)
            values = accepted
        uri = document.get('uri')
        url = uri if isinstance(uri, str) and uri.startswith('https://www.discogs.com/') else ''
        description = ' · '.join(filter(None, [
            str(document.get('title') or ''), str(document.get('year') or ''), _('verified single-track release'),
        ]))
        for value in values:
            self.add([value], Evidence('Discogs', 'Discogs release', description, url, exact=True))
        self._verify_next()

    def _finish_round(self):
        more_pages = self._page < min(self._pages, DISCOGS_MAX_PAGES)
        self.has_more = bool(self._verify) or more_pages
        self.done()


class LastfmSearch(ProviderSearch):
    """Official track.getInfo / track.getTopTags; no pagination is offered."""

    name = 'Last.fm'
    fields = LASTFM_FIELDS
    API = 'https://ws.audioscrobbler.com/2.0/'

    def availability(self):
        unavailable = super().availability()
        if unavailable:
            return unavailable
        settings = get_config().setting
        if not settings['smart_genre_lastfm_enabled'] or not settings['lastfm_api_key']:
            return 'not_configured', _('Enable Last.fm and add an API key in Options → Genres.')
        if self.tag == 'genre' and settings['only_my_genres']:
            return 'not_configured', _('Excluded by the “Use only my genres” preference.')
        if not self.request.artist or not self.request.title:
            return 'unsupported', _('Needs Artist and Title.')
        return None

    def fetch(self):
        method = 'track.getTopTags' if self.tag == 'genre' else 'track.getInfo'
        self._get_url(self.API, self._finished, unencoded_queryargs={
            'method': method,
            'artist': self.request.artist,
            'track': self.request.title,
            'autocorrect': '1',
            'api_key': get_config().setting['lastfm_api_key'],
            'format': 'json',
        })

    def _finished(self, document, error):
        if error or not isinstance(document, dict):
            self.fail()
            return
        code = document.get('error')
        if code == 6:  # Track not found
            self.done('empty')
            return
        if code in {4, 9, 10, 26}:
            self.done('not_configured', _('Last.fm rejected the API key.'))
            return
        if code:
            self.fail(_('Last.fm error %s') % code)
            return
        if self.tag == 'genre':
            tags = document.get('toptags')
            entries = tags.get('tag', []) if isinstance(tags, dict) else []
            node = {'genres': entries, 'tags': entries}
            values = filtered_genres(genres_from_node(node), separate=True)
            if entries and not values:
                self.filtered += len(entries)
            url = 'https://www.last.fm/music/%s/_/%s' % (
                QtCore.QUrl.toPercentEncoding(self.request.artist).data().decode(),
                QtCore.QUrl.toPercentEncoding(self.request.title).data().decode(),
            )
            for value in values:
                self.add([value], Evidence('Last.fm', 'Last.fm', _('Top tag for this track'), url))
            self.done()
            return
        track = document.get('track')
        if not isinstance(track, dict):
            self.done('empty')
            return
        artist = track.get('artist', {}).get('name', '') if isinstance(track.get('artist'), dict) else ''
        title = str(track.get('name') or '')
        exact = same_text(artist, self.request.artist) and same_text(title, self.request.title)
        url = track.get('url') if isinstance(track.get('url'), str) else ''
        context = '%s — %s' % (artist, title)
        if self.tag == 'album':
            album = track.get('album') if isinstance(track.get('album'), dict) else {}
            url = album.get('url') if isinstance(album.get('url'), str) else url
            value = str(album.get('title') or '')
        else:
            value = {'artist': artist, 'title': title}[self.tag]
        self.add([value], Evidence('Last.fm', 'Last.fm track', context, url, exact=exact))
        self.done()


PROVIDER_CLASSES = (MusicBrainzSearch, DiscogsSearch, LastfmSearch)


class TagSearchRequest(QtCore.QObject):
    """All providers for one file/field. Emits `updated` on any change and
    `completed(candidates, message)` whenever a search round settles."""

    updated = QtCore.pyqtSignal()
    completed = QtCore.pyqtSignal(object, str)

    def __init__(self, file, tag):
        super().__init__(file)
        self.file = file
        self.tag = tag
        self.active = False
        self.state = 'new'
        self.message = ''
        self.candidates = []
        self.applied = []
        self.providers = {}
        self._starting = False
        self._index = {}
        self._order = 0
        self._baseline = (tuple(file.metadata.getall(tag)), tag in file.metadata.deleted_tags)
        self._context = self._identity()
        hints = file_hints(file.metadata, file.base_filename)
        # Resolved values: embedded tags, else the parsed filename (not
        # Picard's raw filename guess in the title).
        self.artist = hints.artist or file.metadata['artist']
        self.title = hints.title or file.metadata['title']
        self._deadline = QtCore.QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.setInterval(ROUND_DEADLINE_MS)
        self._deadline.timeout.connect(self._timeout)

    @property
    def recording_id(self):
        return self._context[0]

    @property
    def song(self):
        return ' — '.join(filter(None, [self.artist, self.title]))

    @property
    def has_more(self):
        return any(p.has_more for p in self.providers.values())

    def _identity(self):
        return (recording_identity(self.file), self.file.metadata['artist'], self.file.metadata['title'])

    def current(self):
        return (
            self.file.state != self.file.State.REMOVED
            and self._identity() == self._context
            and self._baseline == (
                tuple(self.file.metadata.getall(self.tag)), self.tag in self.file.metadata.deleted_tags,
            )
        )

    def start(self):
        existing = self.file._tag_search_requests.get(self.tag)
        if existing is not None and existing is not self and existing.active:
            self._settle('cancelled', _('A search for this field is already running.'))
            return
        if self.state != 'new':
            return
        if self.tag not in SUPPORTED_FIELDS or not self.file.can_edit_tags or not self.file.supports_tag(self.tag):
            self._settle('unsupported', _('No provider supplies this field. You can edit it manually.'))
            return
        if self.tag == 'genre' and self.file.smart_genre_pending:
            self._settle('cancelled', _('Genre lookup is already running. Wait for Scan to finish.'))
            return
        self.providers = {cls.name: cls(self) for cls in PROVIDER_CLASSES}
        self._run_round(lambda provider: provider.start())

    def load_more(self):
        """Fetch the next bounded page from each provider that offers one."""
        if self.active or self.state in {'cancelled', 'applied', 'new'} or not self.has_more:
            return False
        existing = self.file._tag_search_requests.get(self.tag)
        if existing is not None and existing is not self and existing.active:
            return False
        if not self.current():
            self.context_changed()
            return False
        self._run_round(lambda provider: provider.load_more())
        return True

    def _run_round(self, action):
        self.active = True
        self.state = 'searching'
        self.message = _('Searching…')
        self.file._tag_search_requests[self.tag] = self
        self._deadline.start()
        self.file.tagger.tagger_stats_changed.emit()
        # Providers that are unavailable settle synchronously; wait for all.
        self._starting = True
        try:
            for provider in list(self.providers.values()):
                if self.active:
                    action(provider)
        finally:
            self._starting = False
        self._check_round()

    def add(self, values, evidence, *, compilation=False):
        values = tuple(v.strip() for v in values if isinstance(v, str) and v.strip())
        if not values or not self.active:
            return
        key = (tuple(normalize_value(v, self.tag) for v in values), compilation)
        candidate = self._index.get(key)
        if candidate is None:
            self._order += 1
            candidate = self._index[key] = TagCandidate(values, [], compilation, self._order)
            self.candidates.append(candidate)
        elif PROVIDERS.index(evidence.provider) < min(PROVIDERS.index(p) for p in candidate.providers):
            # Prefer the spelling of the most authoritative provider.
            candidate.values = values
        if evidence not in candidate.evidence:
            candidate.evidence.append(evidence)
        self.candidates.sort(key=TagCandidate.sort_key)
        self.updated.emit()

    def provider_changed(self, provider):
        self.updated.emit()
        if self.active:
            self._check_round()

    def _check_round(self):
        if self._starting or not self.active or any(p.busy for p in self.providers.values()):
            return
        states = {p.state for p in self.providers.values()}
        if self.candidates:
            state, message = 'ready', (
                _('Choose one or more values to apply to this field only.') if self.tag == 'genre'
                else _('Choose a value to apply to this field only.')
            )
        elif states <= {'not_configured', 'unsupported'}:
            state = 'not_configured' if 'not_configured' in states else 'unsupported'
            message = _('No available provider can search this field. You can edit it manually.')
        elif 'error' in states:
            state, message = 'error', _('No results. A provider request failed.')
        elif 'filtered' in states:
            state, message = 'filtered', _('Returned values did not pass the genre settings.')
        else:
            state, message = 'empty', _('No results from the available providers.')
        self._settle(state, message)

    def context_changed(self):
        self._stop_providers(_('Discarded: the field or recording changed.'), 'cancelled')
        self._settle('cancelled', _('The field or recording changed. Search again.'))

    def _timeout(self):
        self._stop_providers(_('Timed out.'), 'error')
        self._check_round()

    def cancel(self):
        if self.state in {'cancelled', 'applied'}:
            return
        self._stop_providers(_('Cancelled.'), 'cancelled')
        self._settle('cancelled', _('Search cancelled.'))

    def _stop_providers(self, reason, state):
        for provider in self.providers.values():
            if provider.busy:
                provider.abort(reason, state=state)
            else:
                provider.abort()

    def _settle(self, state, message):
        if self.state in {'cancelled', 'applied'}:
            return
        self.active = False
        self.state = state
        self.message = message
        self._deadline.stop()
        if self.file._tag_search_requests.get(self.tag) is self:
            del self.file._tag_search_requests[self.tag]
        self.file.tagger.tagger_stats_changed.emit()
        self.updated.emit()
        self.completed.emit(list(self.candidates), message)

    def apply(self, candidates):
        """Write the chosen value(s) to this one field of File.metadata. Never saves.

        Choosing again replaces an earlier choice, but only while the field
        still holds exactly what this request wrote.
        """
        if self.state in {'new', 'cancelled'} or not self.current() or not candidates:
            return False
        if self.tag != 'genre' and len(candidates) != 1:
            return False
        if any(all(candidate is not c for c in self.candidates) for candidate in candidates):
            return False
        if self.active:
            self._stop_providers(_('Stopped: a value was applied.'), 'cancelled')
            self._settle('ready', '')
        values = list(dict.fromkeys(value for candidate in candidates for value in candidate.values))
        if self.tag == 'genre' and get_config().setting['join_genres']:
            values = [get_config().setting['join_genres'].join(values)]
        providers = [name for name in PROVIDERS if any(name in c.providers for c in candidates)]
        url = next((c.url for c in candidates if c.url), '')
        self.state = 'applied'
        self.file._manually_edited_tags.add(self.tag)
        self.file.metadata[self.tag] = values
        self._baseline = (tuple(self.file.metadata.getall(self.tag)), False)
        self.applied = list(candidates)
        set_source(self.file, self.tag, ' + '.join(providers), url)
        if self.tag == 'genre':
            self.file.smart_genre_source = ' + '.join(providers)
            self.file.smart_genre_values = list(values)
        self.file.update()
        return True
