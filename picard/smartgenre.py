# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""Bounded, optional genre enrichment for a confirmed Smart Scan recording."""

from collections import Counter
from functools import partial
import re

from PyQt6 import QtCore
from PyQt6.QtNetwork import QNetworkReply

from picard import log
from picard.discogsgenre import DiscogsGenreRequest
from picard.config import get_config
from picard.item import MetadataItem
from picard.tagprovenance import set_source


_NON_MUSICAL_TAGS = frozenset(
    {
        'seen live', 'favorites', 'favourites', 'favorite', 'favourite', 'awesome',
        'love', 'loved', 'love it', 'my favorites', 'my favourites', 'best', 'cool',
        'albums i own', 'owned', 'my collection', 'beautiful', 'good', 'great',
    }
)


def filtered_genres(counter, *, separate=False):
    """Use Picard's count, filter, limit and multivalue policy."""
    # Track imports File, which imports this module. Delay to break that cycle.
    from picard.track import Track

    settings = get_config().setting
    musical = Counter(
        {
            name: count
            for name, count in counter.items()
            if re.sub(r'[-_\s]+', ' ', name.casefold()).strip() not in _NON_MUSICAL_TAGS
        }
    )
    return Track._genres_to_metadata(
        musical,
        limit=settings['max_genres'],
        minusage=settings['min_genre_usage'],
        filters=settings['genres_filter'],
        join_with=None if separate else settings['join_genres'],
    )


def genre_entries(node):
    """Return the collection selected in Picard's options, before filtering."""
    settings = get_config().setting
    key = 'tags' if settings['folksonomy_tags'] else 'genres'
    if settings['only_my_genres']:
        key = 'user-' + key
    entries = node.get(key, []) if isinstance(node, dict) else []
    return entries if isinstance(entries, list) else []


def genres_from_node(node):
    """Accept the same genre/tag collection selected in Picard's options."""
    settings = get_config().setting
    result = Counter()
    for entry in genre_entries(node):
        if not isinstance(entry, dict):
            continue
        name = entry.get('name')
        if not isinstance(name, str) or not name.strip():
            continue
        count = 1 if settings['only_my_genres'] else entry.get('count', 0)
        if isinstance(count, str):
            count = int(count) if re.fullmatch(r'[0-9]{1,9}', count) else 0
        if type(count) is int and count > 0:
            result[name.strip().casefold()] += count
    return result


class SmartGenreRequest(QtCore.QObject):
    """One recording -> release/group -> optional Last.fm request chain.

    This never owns the file's pending state or changes its match decision.
    A total deadline also covers requests waiting in Picard's network queue.
    """

    def __init__(self, file, decision, *, on_result=None):
        super().__init__(file)
        self.file = file
        self.decision = decision
        self.on_result = on_result
        self.result_status = 'empty'
        self.diagnostics = []
        self._result_sent = False
        self.active = True
        self._task = None
        self._request_token = None
        self._provider = 'MusicBrainz recording'
        self._discogs_request = None
        self.source_url = ''
        self._discogs_styles = []
        self._deadline = QtCore.QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.setInterval(30000)
        self._deadline.timeout.connect(self._timeout)

    @property
    def styles(self):
        """Styles of the verified Discogs single-track release, if one was used."""
        return list(self._discogs_styles)

    def start(self):
        self._deadline.start()
        inc = set()
        authenticated = MetadataItem.set_genre_inc_params(inc)
        if self.on_result is not None and not inc:
            authenticated = get_config().setting['only_my_genres']
            inc = {'user-genres', 'user-tags'} if authenticated else {'genres', 'tags'}
        self._send(
            partial(
                self.file.tagger.mb_api.get_track_by_id, self.decision.recording_id,
                inc=inc, mblogin=authenticated,
            ),
            self._recording_finished,
        )
        self.file.tagger.tagger_stats_changed.emit()

    def _send(self, factory, handler, *, keyword_handler=False, source='MusicBrainz recording'):
        self._provider = source
        log.debug('Smart Genre: recording=%s provider=%s request started', self.decision.recording_id, source)
        token = self._request_token = object()

        def finished(document, http, error):
            if not self.active or token is not self._request_token:
                log.debug(
                    'Smart Genre: recording=%s provider=%s ignored stale response',
                    self.decision.recording_id, source,
                )
                return
            self._task = None
            self._request_token = None
            if error == QNetworkReply.NetworkError.OperationCanceledError:
                self.cancel()
                return
            if not self._can_fill():
                self._diagnostic(source, 'cancelled', reason='file removed, recording changed or Genre edited')
                self._finish()
                return
            handler(document, error)

        task = factory(handler=finished) if keyword_handler else factory(finished)
        if self.active and token is self._request_token:
            self._task = task

    def _can_fill(self):
        file = self.file
        if self.on_result is not None:
            return file.state != file.State.REMOVED
        return (
            file.state != file.State.REMOVED
            and file._smart_genre_recording_id == self.decision.recording_id
            and get_config().setting['use_genres']
            and get_config().setting['max_genres'] > 0
            and not any(value.strip() for value in file.metadata.getall('genre'))
            and 'genre' not in file.metadata.deleted_tags
        )

    def _diagnostic(self, source, status, *, result_count=0, accepted_count=0, reason=''):
        diagnostic = {
            'source': source, 'status': status, 'result_count': result_count,
            'accepted_count': accepted_count, 'filtered_count': max(0, result_count - accepted_count),
            'reason': reason,
        }
        self.diagnostics.append(diagnostic)
        if self.on_result is None:
            self.file.smart_genre_diagnostics.append(diagnostic)
        log.info(
            'Smart Genre: %r recording=%s provider=%s status=%s result_count=%d filtered_count=%d '
            'accepted_count=%d reason=%s',
            self.file.filename,
            self.decision.recording_id,
            source,
            status,
            result_count,
            diagnostic['filtered_count'],
            accepted_count,
            reason,
        )

    def _try_node(self, node, source):
        self.source_url = ''
        if source.startswith('MusicBrainz') and isinstance(node, dict) and node.get('id'):
            entity = 'release-group' if source.endswith('group') else 'release' if source.endswith('release') else 'recording'
            self.source_url = 'https://musicbrainz.org/%s/%s' % (entity, node['id'])
        return self._apply(genres_from_node(node), source, result_count=len(genre_entries(node)))

    def _apply(self, genres, source, *, result_count=None):
        result_count = len(genres) if result_count is None else result_count
        values = filtered_genres(genres, separate=True)
        if not result_count:
            reason = 'no entries in the configured genre/tag collection'
        elif not genres:
            reason = 'no valid names with positive usage counts'
        elif all(re.sub(r'[-_\s]+', ' ', name.casefold()).strip() in _NON_MUSICAL_TAGS for name in genres):
            reason = 'non-musical tags'
        else:
            reason = 'Picard genre filters, minimum usage or maximum genres'
        self._diagnostic(
            source, 'ready' if values else ('filtered' if result_count else 'empty'),
            result_count=result_count, accepted_count=len(values), reason='' if values else reason,
        )
        if not values:
            return False
        if self.on_result is None and get_config().setting['join_genres']:
            values = [get_config().setting['join_genres'].join(values)]
        if self.on_result is not None:
            self._result_sent = True
            self.on_result(values, source, 'ready')
        elif self._can_fill():
            self.file.metadata['genre'] = values
            self.file.smart_genre_source = source
            set_source(self.file, 'genre', source, self.source_url)
            if (
                source.startswith('Discogs') and self._discogs_styles
                and not self.file.metadata.getall('style')
                and 'style' not in self.file.metadata.deleted_tags
                and 'style' not in self.file._manually_edited_tags
            ):
                self.file.metadata['style'] = self._discogs_styles
                set_source(self.file, 'style', source, self.source_url)
            self.file.smart_genre_values = list(values)
        self._finish()
        return True

    def _recording_finished(self, document, error):
        source = 'MusicBrainz recording'
        if not error and isinstance(document, dict) and document.get('id') == self.decision.recording_id:
            if self._try_node(document, source):
                return
        else:
            self._diagnostic(source, 'error', reason='network error' if error else 'invalid recording response')
        release = self.decision.release or {}
        if self._try_node(release, 'MusicBrainz release'):
            return
        if self._try_node(release.get('release-group'), 'MusicBrainz release group'):
            return

        # Reuse already-loaded release data before requesting it again.
        album = self.file.tagger.albums.get(release.get('id'))
        if album and album.loaded and not get_config().setting['only_my_genres']:
            attr = 'folksonomy_tags' if get_config().setting['folksonomy_tags'] else 'genres'
            if self._apply(getattr(album, attr), 'MusicBrainz release'):
                return
            if album.release_group and self._apply(
                getattr(album.release_group, attr), 'MusicBrainz release group'
            ):
                return
        if release.get('id'):
            inc = {'release-groups'}
            authenticated = MetadataItem.set_genre_inc_params(inc)
            if self.on_result is not None and not get_config().setting['use_genres']:
                authenticated = get_config().setting['only_my_genres']
                inc |= {'user-genres', 'user-tags'} if authenticated else {'genres', 'tags'}
            self._send(
                partial(
                    self.file.tagger.mb_api.get_release_by_id, release['id'],
                    inc=inc, mblogin=authenticated,
                ),
                self._release_finished,
                source='MusicBrainz release/group',
            )
        else:
            self._discogs()

    def _release_finished(self, document, error):
        release_id = (self.decision.release or {}).get('id')
        if not error and isinstance(document, dict) and document.get('id') == release_id:
            if self._try_node(document, 'MusicBrainz release'):
                return
            if self._try_node(document.get('release-group'), 'MusicBrainz release group'):
                return
        else:
            self._diagnostic(
                'MusicBrainz release/group', 'error',
                reason='network error' if error else 'invalid release response',
            )
        self._discogs()

    def _discogs(self):
        settings = get_config().setting
        if not settings['dlmtags_discogs_enabled'] or not settings['dlmtags_discogs_token']:
            self._diagnostic('Discogs', 'not_configured', reason='opt-in disabled or token not configured')
            self._lastfm()
            return
        match = getattr(self.decision, 'match', None)
        track = match.track if match else {}
        artist = self.file.metadata['artist'] or track.get('artist-credit-phrase', '')
        title = self.file.metadata['title'] or track.get('title', '')
        if not artist or not title:
            self._diagnostic('Discogs', 'empty', reason='missing confirmed artist/title')
            self._lastfm()
            return
        self._provider = 'Discogs'
        self._discogs_request = DiscogsGenreRequest(
            self, artist, title, settings['dlmtags_discogs_token'],
            webservice=self.file.tagger.webservice, responses=self.file.dlmtags_discogs_responses,
        )
        self._discogs_request.finished.connect(self._discogs_finished)
        self._discogs_request.start()

    def _discogs_finished(self, values, status):
        self._discogs_styles = self._discogs_request.styles if self._discogs_request is not None else []
        self._discogs_request = None
        if not self.active:
            return
        if not self._can_fill():
            self._finish()
            return
        self.source_url = status if values and status.startswith('https://www.discogs.com/') else ''
        if values and self._apply(Counter({v.casefold(): 100 for v in values}), 'Discogs verified single-track release'):
            return
        self._diagnostic('Discogs', status if status in {'empty', 'error', 'timeout'} else 'empty', reason='no verified single-track release genres')
        self._lastfm()

    def _lastfm(self):
        settings = get_config().setting
        if (
            not settings['smart_genre_lastfm_enabled']
            or not settings['lastfm_api_key']
            or settings['only_my_genres']
        ):
            reason = (
                'excluded by only-my-genres preference' if settings['only_my_genres'] else
                'fallback disabled' if not settings['smart_genre_lastfm_enabled'] else 'API key not configured'
            )
            self._diagnostic('Last.fm', 'not_configured', reason=reason)
            self._finish()
            return
        # Official track.getTopTags API; MBID avoids a second identity guess.
        self._send(
            partial(
                self.file.tagger.webservice.get_url,
                url=QtCore.QUrl('https://ws.audioscrobbler.com/2.0/'),
                queryargs={
                    'method': 'track.getTopTags',
                    'mbid': self.decision.recording_id,
                    'api_key': settings['lastfm_api_key'],
                    'format': 'json',
                },
                parse_response_type='json',
            ),
            self._lastfm_finished,
            keyword_handler=True,
            source='Last.fm',
        )

    def _lastfm_finished(self, document, error):
        if not error and isinstance(document, dict) and not document.get('error'):
            tags = document.get('toptags')
            if isinstance(tags, dict):
                # Last.fm uses the same name/count shape as MusicBrainz tags.
                entries = tags.get('tag', [])
                if self._try_node({'genres': entries, 'tags': entries}, 'Last.fm'):
                    return
                self._finish()
                return
        code = document.get('error') if isinstance(document, dict) else None
        status = 'not_configured' if code in {4, 9, 10, 26} else 'error'
        self._diagnostic(
            'Last.fm', status, reason='network error' if error else 'invalid response or API error %s' % code,
        )
        self._finish()

    def _timeout(self):
        if not self.active:
            return
        self.result_status = 'timeout'
        self._diagnostic(self._provider, 'timeout', reason='30-second total deadline exceeded')
        self._finish()

    def cancel(self):
        if self.active:
            self.result_status = 'cancelled'
            self._diagnostic('Genre retrieval', 'cancelled')
        self._finish()

    def _finish(self):
        if not self.active:
            return
        self.active = False
        self._request_token = None
        discogs, self._discogs_request = self._discogs_request, None
        if discogs is not None:
            discogs.cancel()
        self._deadline.stop()
        task, self._task = self._task, None
        if task is not None:
            self.file.tagger.webservice.abort_task(task)
        if self.file._smart_genre_request is self:
            self.file._smart_genre_request = None
        if self.on_result is None:
            self.file.update()
        elif not self._result_sent:
            self._result_sent = True
            if self.result_status == 'empty':
                for status in ('error', 'filtered'):
                    if any(d['status'] == status for d in self.diagnostics):
                        self.result_status = status
                        break
                else:
                    if self.diagnostics and self.diagnostics[-1]['status'] == 'not_configured':
                        self.result_status = 'not_configured'
            self.on_result([], '', self.result_status)
        self.file.tagger.tagger_stats_changed.emit()
        self.deleteLater()
