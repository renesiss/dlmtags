# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""Song Info: a verified Wikipedia article about one recognised recording,
plus facts of that MusicBrainz recording (kept apart, with their source).

Informational only: nothing here touches File.metadata, artwork or Save.

The article is found in two ways, both through Picard's asynchronous web
service (rate limiting, timeouts, cancellation):

1. MusicBrainz → Wikidata → Wikipedia: Wikidata (or Wikipedia) URL relations
   of the recording and of its works lead to the article's site link.
2. Otherwise a Wikipedia search for the title and artist. A result is only
   accepted when its title is the song title, it describes a song, and it
   names one of the credited artists; a page about the artist, an album or a
   different song with the same title is rejected.
"""

from dataclasses import (
    dataclass,
    replace,
)
from functools import partial
import re
import unicodedata

from PyQt6 import QtCore
from PyQt6.QtNetwork import QNetworkReply

from picard.i18n import gettext as _
from picard.webservice import USER_AGENT_STRING


WIKI_LANGUAGE = 'en'
WIKIPEDIA_API = 'https://%s.wikipedia.org/w/api.php' % WIKI_LANGUAGE
WIKIDATA_API = 'https://www.wikidata.org/w/api.php'
# Wikimedia asks API clients to identify themselves.
# https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy
USER_AGENT = USER_AGENT_STRING + ' DLMtags-SongInfo/1.0'
LOOKUP_TIMEOUT_MS = 20000
SEARCH_RESULTS = 5
# About the Song: an excerpt, not the whole article.
EXCERPT_MAX_CHARS = 2400
# A lead section shorter than this is extended from the same article's body.
SHORT_INTRO_CHARS = 900
# Body sections that are lists or apparatus, not prose about the song.
SKIPPED_SECTIONS = {
    'accolades', 'certifications', 'chart', 'charts', 'credits', 'credits and personnel', 'external links',
    'formats and track listings', 'further reading', 'monthly charts', 'notes', 'personnel', 'references',
    'release history', 'sales and certifications', 'see also', 'sources', 'track listing', 'track listings',
    'weekly charts', 'year end charts', 'all time charts', 'decade end charts', 'citations', 'bibliography',
}
SONGWRITER_TYPES = {'composer', 'lyricist', 'writer', 'librettist'}
PRODUCER_TYPES = {'producer'}
MAX_GENRES = 3
HEADING_RE = re.compile(r'^(={2,6})\s*(.*?)\s*\1$')
LICENSE_NAME = 'CC BY-SA 4.0'
LICENSE_URL = 'https://creativecommons.org/licenses/by-sa/4.0/'

QID_RE = re.compile(r'https?://(?:www\.)?wikidata\.org/wiki/(Q\d+)')
WIKIPEDIA_URL_RE = re.compile(r'https?://%s\.wikipedia\.org/wiki/(.+)' % WIKI_LANGUAGE)
SONG_WORDS = {'song', 'single', 'track', 'ballad', 'anthem', 'composition', 'hymn'}
SONG_SENTENCE_RE = re.compile(
    r'\b(?:is|was) (?:an? |the )?(?:\w+ ){0,3}(?:%s)\b' % '|'.join(sorted(SONG_WORDS))
)
YEAR_RE = re.compile(r'(\d{4})(?:-\d{2}){0,2}$')
# Wikipedia short descriptions of songs start with the release year:
# "2024 single by …", "1975 song by …", "2019 promotional single by …".
DESCRIPTION_YEAR_RE = re.compile(
    r'^(\d{4}) (?:[\w-]+ ){0,2}(?:%s)\b' % '|'.join(sorted(SONG_WORDS)), re.IGNORECASE,
)


@dataclass(frozen=True)
class SongArticle:
    title: str
    url: str
    description: str
    # Plain-text excerpt as ('heading' | 'paragraph', text) blocks, in article order.
    blocks: tuple
    # True when text of the shown sections was cut or later sections omitted.
    shortened: bool
    # 'musicbrainz' (linked via MusicBrainz/Wikidata) or 'search' (verified search result)
    method: str
    pageid: int = 0

    @property
    def year(self):
        """Release year stated in the article's short description, or ''."""
        match = DESCRIPTION_YEAR_RE.match(self.description or '')
        return match.group(1) if match else ''

    @property
    def summary(self):
        return '\n\n'.join(text for kind, text in self.blocks if kind == 'paragraph')


@dataclass(frozen=True)
class RecordingFacts:
    """Facts of the verified MusicBrainz recording (not from Wikipedia)."""

    # MusicBrainz first-release-date as given (full date where known).
    release_date: str = ''
    songwriters: tuple = ()
    producers: tuple = ()
    genres: tuple = ()
    length_ms: int = 0

    @property
    def year(self):
        return release_year(self.release_date)


def release_year(date):
    """'2021' from '2021', '2021-04' or '2021-04-09'; '' for anything else."""
    match = YEAR_RE.match((date or '').strip())
    return match.group(1) if match and match.group(1) != '0000' else ''


def recording_facts(recording):
    """Release date, credits, genres and length as MusicBrainz states them; nothing inferred."""
    recording = recording or {}
    songwriters, producers = [], []
    for relation in recording.get('relations') or ():
        if relation.get('target-type') == 'artist' and relation.get('type') in PRODUCER_TYPES:
            producers.append((relation.get('artist') or {}).get('name', ''))
        elif relation.get('target-type') == 'work':
            for work_relation in (relation.get('work') or {}).get('relations') or ():
                if work_relation.get('target-type') == 'artist' and work_relation.get('type') in SONGWRITER_TYPES:
                    songwriters.append((work_relation.get('artist') or {}).get('name', ''))
    genres = sorted(
        (genre for genre in recording.get('genres') or () if genre.get('name')),
        key=lambda genre: -(genre.get('count') or 0),
    )
    length = recording.get('length')
    return RecordingFacts(
        release_date=str(recording.get('first-release-date') or ''),
        songwriters=tuple(dict.fromkeys(name for name in songwriters if name)),
        producers=tuple(dict.fromkeys(name for name in producers if name)),
        genres=tuple(genre['name'] for genre in genres[:MAX_GENRES]),
        length_ms=length if isinstance(length, int) and length > 0 else 0,
    )


def normalize(text):
    """Casefolded words only: punctuation, quotes and accents do not decide a match."""
    text = unicodedata.normalize('NFKD', text or '')
    text = ''.join(c for c in text if not unicodedata.combining(c)).casefold()
    text = text.replace('&', ' and ')
    return ' '.join(re.findall(r'\w+', text))


def base_title(title):
    """Song title without parenthesised parts: "Smile (Lady Gaga song)" → "Smile"."""
    return normalize(re.sub(r'\s*[(\[][^)\]]*[)\]]', '', title or ''))


def describes_song(page):
    """The short description, or the opening "X is a … song", says it is a song.

    Other words in the intro ("a singer known for Song") do not count.
    """
    if SONG_WORDS & set(normalize(page.get('description', '')).split()):
        return True
    return bool(SONG_SENTENCE_RE.search(normalize(first_sentence(page.get('extract', '')))))


def names_artist(page, artists):
    text = ' %s ' % normalize(' '.join((page.get('description', ''), page.get('extract', '')[:600])))
    return any(name and ' %s ' % name in text for name in (normalize(artist) for artist in artists))


def first_sentence(text):
    match = re.match(r'(.+?[.!?])(\s|$)', text or '', re.DOTALL)
    return match.group(1) if match else (text or '')


def _cut(text, limit):
    """Text within limit, ending at a sentence where possible."""
    cut = text[:limit]
    end = max(cut.rfind('. '), cut.rfind('! '), cut.rfind('? '))
    return (cut[:end + 1] if end > 0 else cut.rstrip()) + ' …'


def excerpt(extract, limit=EXCERPT_MAX_CHARS):
    """Plain-text extract → (blocks, shortened), keeping paragraphs and section order.

    Section headings ("== Background ==", from exsectionformat=wikitext) are
    kept when their section has prose; list-like sections are skipped.
    """
    blocks, used, shortened = [], 0, False
    heading, section_skipped, skipping = None, False, False
    for line in (extract or '').split('\n'):
        line = line.strip()
        if not line:
            continue
        if match := HEADING_RE.match(line):
            level, name = len(match.group(1)), match.group(2)
            skipped = normalize(name) in SKIPPED_SECTIONS
            if level == 2:
                section_skipped = skipped
            # A subsection of a skipped section is skipped as well.
            skipping = section_skipped or skipped
            heading = None if skipping else name
            continue
        if skipping:
            shortened = True
            continue
        if used + len(line) > limit:
            room = limit - used
            if room > 200:
                if heading:
                    blocks.append(('heading', heading))
                blocks.append(('paragraph', _cut(line, room)))
            shortened = True
            break
        if heading:
            blocks.append(('heading', heading))
            heading = None
        blocks.append(('paragraph', line))
        used += len(line)
    return tuple(blocks), shortened


def is_usable(page):
    return (
        not page.get('missing') and not page.get('invalid')
        and 'disambiguation' not in (page.get('pageprops') or {})
        and bool(page.get('extract'))
    )


def verify(page, title, artists, method):
    """Accept a page only if it is about this song by this artist."""
    if not is_usable(page) or not describes_song(page):
        return False
    if method == 'musicbrainz':
        # MusicBrainz links the recording or its work to this exact item.
        return True
    return base_title(page.get('title', '')) == base_title(title) and names_artist(page, artists)


def collect_links(recording):
    """Wikidata QIDs and Wikipedia titles linked from a recording and its works."""
    qids, titles = [], []

    def scan(relations):
        for relation in relations or ():
            url = (relation.get('url') or {}).get('resource', '')
            if relation.get('target-type') == 'url':
                if match := QID_RE.match(url):
                    qids.append(match.group(1))
                elif match := WIKIPEDIA_URL_RE.match(url):
                    titles.append(QtCore.QUrl.fromPercentEncoding(match.group(1).encode()).replace('_', ' '))
            elif relation.get('target-type') == 'work':
                scan((relation.get('work') or {}).get('relations'))

    scan((recording or {}).get('relations'))
    return list(dict.fromkeys(qids)), list(dict.fromkeys(titles))


class SongInfoLookup(QtCore.QObject):
    """One lookup per recording. States: loading, found, not_found, error."""

    changed = QtCore.pyqtSignal()

    def __init__(self, tagger, recording_id, title, artists, parent=None):
        super().__init__(parent)
        self.tagger = tagger
        self.recording_id = recording_id
        self.title = title
        self.artists = [artist for artist in artists if artist]
        self.state = 'new'
        self.message = ''
        self.article = None
        self.facts = RecordingFacts()
        self._tasks = []
        self._token = None
        self._searched = False
        self._deadline = QtCore.QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.setInterval(LOOKUP_TIMEOUT_MS)
        self._deadline.timeout.connect(lambda: self._finish('error', _('Wikipedia did not answer in time.')))

    @property
    def active(self):
        return self.state == 'loading'

    def start(self):
        if self.state != 'new':
            return
        self.state = 'loading'
        self._deadline.start()
        self._request(
            lambda handler: self.tagger.mb_api.get_track_by_id(
                self.recording_id, handler,
                inc=('url-rels', 'work-rels', 'work-level-rels', 'artist-rels', 'genres'),
            ),
            self._recording_loaded,
        )
        self.changed.emit()

    def cancel(self):
        if self.active:
            self._finish('error', _('Cancelled.'))

    def _request(self, factory, handler):
        token = self._token = object()

        def finished(document, http, error):
            if token is not self._token or not self.active:
                return
            self._tasks = []
            if error == QNetworkReply.NetworkError.OperationCanceledError:
                self._finish('error', _('Cancelled.'))
            else:
                handler(document, http, error)

        task = factory(finished)
        if token is self._token:
            self._tasks = [task]

    def _get(self, url, queryargs, handler):
        self._request(
            lambda finished: self.tagger.webservice.get_url(
                url=QtCore.QUrl(url), handler=finished, parse_response_type='json',
                priority=False, important=False, unencoded_queryargs=queryargs,
                headers={'User-Agent': USER_AGENT},
            ),
            handler,
        )

    def _recording_loaded(self, document, http, error):
        # Without the relations, the verified search is still possible.
        if not error:
            self.facts = recording_facts(document)
        qids, titles = collect_links(document if not error else None)
        if titles:
            self._fetch_pages(titles, 'musicbrainz')
        elif qids:
            self._get(WIKIDATA_API, {
                'action': 'wbgetentities', 'ids': '|'.join(qids[:10]), 'props': 'sitelinks',
                'sitefilter': WIKI_LANGUAGE + 'wiki', 'format': 'json',
            }, self._wikidata_loaded)
        else:
            self._search()

    def _wikidata_loaded(self, document, http, error):
        if error:
            self._search()
            return
        titles = [
            ((entity.get('sitelinks') or {}).get(WIKI_LANGUAGE + 'wiki') or {}).get('title', '')
            for entity in ((document or {}).get('entities') or {}).values()
        ]
        titles = [title for title in titles if title]
        if titles:
            self._fetch_pages(titles, 'musicbrainz')
        else:
            self._search()

    def _page_query(self):
        return {
            'action': 'query', 'format': 'json', 'formatversion': '2', 'redirects': '1',
            'prop': 'extracts|description|pageprops|info', 'exintro': '1', 'explaintext': '1',
            'exlimit': str(SEARCH_RESULTS), 'ppprop': 'disambiguation', 'inprop': 'url',
        }

    def _fetch_pages(self, titles, method):
        query = self._page_query()
        query['titles'] = '|'.join(titles[:SEARCH_RESULTS])
        self._get(WIKIPEDIA_API, query, partial(self._pages_loaded, method))

    def _search(self):
        if self._searched or not self.title or not self.artists:
            self._finish('not_found', _('No verified Wikipedia article found'))
            return
        self._searched = True
        query = self._page_query()
        query.update({
            'generator': 'search', 'gsrnamespace': '0', 'gsrlimit': str(SEARCH_RESULTS),
            'gsrsearch': '"%s" %s song' % (self.title.replace('"', ''), self.artists[0]),
        })
        self._get(WIKIPEDIA_API, query, partial(self._pages_loaded, 'search'))

    def _pages_loaded(self, method, document, http, error):
        if error:
            self._finish('error', _('Wikipedia request failed: %s') % (http.errorString() if http else error))
            return
        pages = ((document or {}).get('query') or {}).get('pages') or []
        # Search rank first; among linked pages, the one named like the song
        # (not e.g. "List of … cover versions").
        pages = sorted(pages, key=lambda page: (
            base_title(page.get('title', '')) != base_title(self.title), page.get('index') or 0,
        ))
        page = next((page for page in pages if verify(page, self.title, self.artists, method)), None)
        if page is None:
            # A linked page that is not a song article falls back to search.
            self._search()
            return
        blocks, shortened = excerpt(page['extract'])
        self.article = SongArticle(
            title=page['title'],
            url=page.get('fullurl') or 'https://%s.wikipedia.org/wiki/%s' % (
                WIKI_LANGUAGE, page['title'].replace(' ', '_'),
            ),
            description=page.get('description', ''),
            blocks=blocks,
            shortened=True,
            method=method,
            pageid=page.get('pageid') or 0,
        )
        if len(page['extract']) < SHORT_INTRO_CHARS and self.article.pageid:
            # Only a short lead: read more of the same, already verified page.
            self._get(WIKIPEDIA_API, {
                'action': 'query', 'format': 'json', 'formatversion': '2', 'prop': 'extracts',
                'explaintext': '1', 'exsectionformat': 'wikitext', 'exlimit': '1',
                'pageids': str(self.article.pageid),
            }, self._body_loaded)
            return
        self.article = replace(self.article, shortened=shortened)
        self._finish('found', '')

    def _body_loaded(self, document, http, error):
        # The lead stays shown if the longer excerpt is unavailable.
        pages = ((document or {}).get('query') or {}).get('pages') or [] if not error else []
        page = next((page for page in pages if page.get('pageid') == self.article.pageid), None)
        if page is not None and page.get('extract'):
            blocks, shortened = excerpt(page['extract'])
            if len(blocks) > len(self.article.blocks):
                self.article = replace(self.article, blocks=blocks, shortened=shortened)
        self._finish('found', '')

    def _finish(self, state, message):
        if not self.active:
            return
        self._deadline.stop()
        self._token = None
        tasks, self._tasks = self._tasks, []
        if state == 'error':
            for task in tasks:
                if task is not None:
                    self.tagger.webservice.abort_task(task)
        self.state = state
        self.message = message
        self.changed.emit()
