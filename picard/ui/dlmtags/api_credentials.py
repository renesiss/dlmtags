# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""Shared asynchronous credential verification for optional metadata sources."""

import json

from PyQt6 import (
    QtCore,
    QtNetwork,
)

from picard.i18n import gettext as _


API_PROVIDERS = {
    'discogs': {
        'title': _('Discogs'),
        'enabled_key': 'dlmtags_discogs_enabled',
        'credential_key': 'dlmtags_discogs_token',
        'connect_url': 'https://www.discogs.com/settings/developers',
    },
    'lastfm': {
        'title': _('Last.fm'),
        'enabled_key': 'smart_genre_lastfm_enabled',
        'credential_key': 'lastfm_api_key',
        'connect_url': 'https://www.last.fm/api/account/create',
    },
}
REQUEST_TIMEOUT_MS = 10000


class ApiCredentialTester(QtCore.QObject):
    """Verify API keys with the same lightweight provider checks as setup."""

    finished = QtCore.pyqtSignal(str, bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._network = QtNetwork.QNetworkAccessManager(self)
        self._pending = {}

    def test(self, provider, credential):
        if provider not in API_PROVIDERS:
            raise ValueError('Unknown API provider: %s' % provider)
        credential = credential.strip()
        if not credential:
            self.finished.emit(provider, False, _('Enter a key before testing.'))
            return None
        previous = self._pending.pop(provider, None)
        if previous is not None:
            previous.abort()
        request = self._request(provider, credential)
        request.setTransferTimeout(REQUEST_TIMEOUT_MS)
        reply = self._network.get(request)
        if reply is None:
            self.finished.emit(provider, False, _('The network request could not be started.'))
            return None
        self._pending[provider] = reply
        reply.finished.connect(lambda name=provider, current=reply: self._finish(name, current))
        return reply

    def _request(self, provider, credential):
        if provider == 'discogs':
            request = QtNetwork.QNetworkRequest(QtCore.QUrl('https://api.discogs.com/oauth/identity'))
            request.setRawHeader(b'Authorization', ('Discogs token=' + credential).encode('utf-8'))
            request.setRawHeader(b'User-Agent', b'DLMtags/1.0')
            return request
        query = QtCore.QUrlQuery()
        for key, value in (
            ('method', 'tag.getTopTags'),
            ('tag', 'rock'),
            ('api_key', credential),
            ('format', 'json'),
        ):
            query.addQueryItem(key, value)
        url = QtCore.QUrl('https://ws.audioscrobbler.com/2.0/')
        url.setQuery(query)
        return QtNetwork.QNetworkRequest(url)

    def _finish(self, provider, reply):
        if self._pending.get(provider) is not reply:
            reply.deleteLater()
            return
        del self._pending[provider]
        ok, message = self._evaluate(provider, reply)
        reply.deleteLater()
        self.finished.emit(provider, ok, message)

    @staticmethod
    def _evaluate(provider, reply):
        if reply.error() != QtNetwork.QNetworkReply.NetworkError.NoError:
            if reply.error() == QtNetwork.QNetworkReply.NetworkError.TimeoutError:
                return False, _('The request timed out.')
            return False, _('Network request failed (error %s).') % int(reply.error())
        status = reply.attribute(QtNetwork.QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if status != 200:
            return False, _('The provider returned HTTP %s.') % (status or _('an error'))
        try:
            data = json.loads(bytes(reply.readAll()).decode('utf-8'))
        except (ValueError, UnicodeError):
            return False, _('The provider returned an invalid response.')
        if provider == 'discogs' and isinstance(data.get('username'), str) and data['username']:
            return True, _('Discogs verified this token.')
        if provider == 'lastfm' and 'error' not in data and isinstance(data.get('toptags'), dict):
            return True, _('Last.fm verified this key.')
        return False, _('The provider rejected the key or returned an unexpected response.')

    def cancel_all(self):
        pending, self._pending = self._pending, {}
        for reply in pending.values():
            reply.abort()
