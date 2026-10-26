# SPDX-License-Identifier: GPL-2.0-or-later
"""Conservative, optional Discogs release-level genre lookup.

Only a verified single-track release may lend release-level genre/style to a
recording. Requests go through Picard's WebService (per-host rate limiting,
429/503 retries, proxy, HTTP cache, transfer timeout); the token is sent as a
header, never in URLs or logs.
"""
import hashlib
import re
import unicodedata

from PyQt6 import QtCore
from PyQt6.QtNetwork import QNetworkReply


DISCOGS_API = 'https://api.discogs.com'
# Successful responses kept per file; a file makes only a handful of requests.
DISCOGS_STORED_RESPONSES = 32


def folded(value):
    """Comparison key ignoring case, accents and punctuation; every script is kept."""
    value = unicodedata.normalize('NFKD', str(value or '')).casefold()
    value = ''.join(c for c in value if not unicodedata.combining(c))
    return ' '.join(re.sub(r'[\W_]+', ' ', value).split())


def same_text(a, b):
    """True when two non-empty values are equal under folded().

    Values without any letter or digit fold to '', which must not make them
    equal to each other, so they are compared case-insensitively as written.
    """
    key_a, key_b = folded(a), folded(b)
    if key_a or key_b:
        return key_a == key_b
    a, b = str(a or '').strip().casefold(), str(b or '').strip().casefold()
    return bool(a) and a == b


def exact_single_track_genres(release, artist, title, *, field='genre'):
    """Never infer a track genre from an arbitrary album or compilation."""
    if not isinstance(release, dict):
        return []
    tracks = [t for t in (release.get('tracklist') or []) if isinstance(t, dict) and t.get('type_') == 'track']
    if len(tracks) != 1 or not same_text(tracks[0].get('title'), title):
        return []
    artists = tracks[0].get('artists') or release.get('artists') or []
    actual = [a.get('name', '') for a in artists if isinstance(a, dict)]
    if not actual or not any(same_text(a, artist) for a in actual):
        return []
    values = release.get('styles' if field == 'style' else 'genres', [])
    return list(dict.fromkeys(v.strip() for v in values if isinstance(v, str) and v.strip()))


def _page_size(queryargs):
    try:
        return int(queryargs['per_page'])
    except (KeyError, TypeError, ValueError):
        return None


class DiscogsResponses:
    """Successful Discogs documents of one file, shared by Smart Genre and Find Tag.

    A stored document answers a later request with the same path, parameters
    and token. A larger first search page also answers a smaller one with its
    leading results; nothing else is derived, so a 5-result page never stands
    in for 25 results and later pages are always requested.
    """

    def __init__(self):
        self._documents = {}

    @staticmethod
    def _key(path, queryargs, token):
        query = {name: str(value) for name, value in (queryargs or {}).items() if name != 'per_page'}
        if _page_size(queryargs) is not None:
            query.setdefault('page', '1')
        # Keep the authentication context in the key without storing the token.
        digest = hashlib.sha256(str(token).encode('utf-8')).hexdigest()
        return digest, path, tuple(sorted(query.items()))

    def get(self, path, queryargs, token):
        entry = self._documents.get(self._key(path, queryargs, token))
        if entry is None:
            return None
        stored_size, document = entry
        wanted = _page_size(queryargs)
        if wanted == stored_size:
            return document
        if wanted is None or stored_size is None or wanted > stored_size:
            return None
        if str((queryargs or {}).get('page', 1)) != '1':
            return None
        # Leading results of a larger first page; its pagination would be wrong.
        leading = {name: value for name, value in document.items() if name != 'pagination'}
        leading['results'] = document['results'][:wanted]
        return leading

    def put(self, path, queryargs, token, document):
        size = _page_size(queryargs)
        if not isinstance(document, dict) or (size is not None and not isinstance(document.get('results'), list)):
            return
        key = self._key(path, queryargs, token)
        stored = self._documents.pop(key, None)
        if stored is not None and size is not None and stored[0] is not None and stored[0] > size:
            # Never replace a larger page by a smaller one.
            self._documents[key] = stored
            return
        self._documents[key] = (size, document)
        while len(self._documents) > DISCOGS_STORED_RESPONSES:
            del self._documents[next(iter(self._documents))]


def discogs_get(webservice, path, handler, token, *, responses=None, **queryargs):
    """GET a Discogs API path through Picard's WebService.

    handler(document, reply, error) is called as for any WebService request.
    A response stored in `responses` answers immediately (reply None) and the
    pending task returned is then None; successful replies are stored there.
    """
    if responses is not None:
        document = responses.get(path, queryargs, token)
        if document is not None:
            handler(document, None, None)
            return None

    def finished(document, reply, error):
        if responses is not None and not error:
            responses.put(path, queryargs, token, document)
        handler(document, reply, error)

    return webservice.get_url(
        url=QtCore.QUrl(DISCOGS_API + path),
        handler=finished,
        parse_response_type='json',
        priority=True,
        important=False,
        unencoded_queryargs=queryargs or None,
        headers={'Authorization': 'Discogs token=' + token},
    )


class DiscogsGenreRequest(QtCore.QObject):
    """At most one search and two candidate releases, with a global deadline."""
    finished = QtCore.pyqtSignal(object, str)

    def __init__(self, parent, artist, title, token, *, webservice, responses=None, field='genre'):
        super().__init__(parent)
        self.artist, self.title, self._token = artist, title, token
        self.field = field
        self.styles = []
        self._webservice = webservice
        self._responses = responses
        self._task = None
        self._pending = None
        self._ids = []
        self._seen = set()
        self._active = False
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self._finish([], 'timeout'))

    def start(self):
        if self._active:
            return
        self._active = True
        self._timer.start(12000)
        self._get('/database/search', self._searched, type='release', artist=self.artist, track=self.title, per_page=5)

    def _get(self, path, callback, **queryargs):
        pending = self._pending = object()

        def received(document, reply, error):
            if not self._active or pending is not self._pending:
                return
            self._task = None
            self._pending = None
            if error == QNetworkReply.NetworkError.OperationCanceledError:
                self._finish([], 'cancelled')
            elif error or not isinstance(document, dict):
                self._finish([], 'error')
            else:
                callback(document)

        task = discogs_get(self._webservice, path, received, self._token, responses=self._responses, **queryargs)
        if self._active and pending is self._pending:
            self._task = task

    def _searched(self, payload):
        for item in (payload.get('results') or [])[:5]:
            ident = item.get('id') if isinstance(item, dict) else None
            if isinstance(ident, int) and ident > 0 and ident not in self._seen:
                self._seen.add(ident)
                self._ids.append(ident)
        self._ids = self._ids[:2]
        self._next()

    def _next(self):
        if not self._ids:
            self._finish([], 'empty')
            return
        ident = self._ids.pop(0)
        self._get(f'/releases/{ident}', self._release)

    def _release(self, payload):
        values = exact_single_track_genres(payload, self.artist, self.title, field=self.field)
        self.styles = exact_single_track_genres(payload, self.artist, self.title, field='style')
        if values:
            url = payload.get('uri', '')
            self._finish(values, url if isinstance(url, str) and url.startswith('https://www.discogs.com/') else 'ready')
        else:
            self._next()

    def _finish(self, values, status):
        if not self._active:
            return
        self._active = False
        self._pending = None
        self._timer.stop()
        task, self._task = self._task, None
        if task is not None:
            self._webservice.abort_task(task)
        self.finished.emit(values, status)

    def cancel(self):
        self._finish([], 'cancelled')
