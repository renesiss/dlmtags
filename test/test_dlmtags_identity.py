"""DLMtags and Picard must not share a Qt settings store."""

import os
import subprocess
import sys
from unittest.mock import patch

from PyQt6.QtCore import QUrl

from picard.plugin3.manager.install import PluginInstaller
from picard.util.pipe import (
    HaikuPipe,
    UnixPipe,
)
from picard.webservice import WebService


def test_settings_are_independent_in_one_home(tmp_path):
    environment = os.environ.copy()
    environment.update(
        HOME=str(tmp_path),
        XDG_CONFIG_HOME=str(tmp_path / 'config'),
        XDG_CACHE_HOME=str(tmp_path / 'cache'),
        XDG_DATA_HOME=str(tmp_path / 'data'),
    )
    script = """
from PyQt6.QtCore import QSettings
from picard import PICARD_APP_NAME, PICARD_ORG_NAME
from picard.config import Config
from picard.const.appdirs import cache_folder, config_folder, plugin_folder

picard = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope, 'MusicBrainz', 'Picard')
picard.setValue('setting/lastfm_api_key', 'upstream-secret')
picard.sync()
before = open(picard.fileName(), 'rb').read()
dlmtags = Config.from_app(None)
assert (PICARD_ORG_NAME, PICARD_APP_NAME) == ('DLMtags', 'DLMtags')
assert dlmtags.fileName() != picard.fileName()
assert dlmtags.setting['lastfm_api_key'] != 'upstream-secret'
dlmtags.setting['lastfm_api_key'] = 'dlmtags-secret'
dlmtags.sync()
assert open(picard.fileName(), 'rb').read() == before
assert 'DLMtags' in config_folder() and 'DLMtags' in cache_folder() and 'DLMtags' in plugin_folder()
"""
    result = subprocess.run([sys.executable, '-c', script], env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_api_key_is_redacted_from_logged_url():
    displayed = WebService.display_url(QUrl('https://example.test/?api_key=private-key&token=private-token'))
    assert 'private-key' not in displayed
    assert 'private-token' not in displayed
    assert 'REDACTED' in displayed


def test_ipc_fallback_paths_do_not_use_picards_config_directory():
    for pipe_class in (UnixPipe, HaikuPipe):
        assert all('MusicBrainz/Picard' not in path for path in pipe_class.PIPE_DIRS if path)
        assert 'DLMtags' in pipe_class.PIPE_DIRS[-1]


def test_local_plugin_temp_path_has_dlmtags_namespace(tmp_path):
    with (
        patch('picard.plugin3.manager.install.tempfile.gettempdir', return_value=str(tmp_path)),
        patch('picard.plugin3.manager.install.PluginSourceGit') as source,
    ):
        source.return_value.sync.return_value = 'commit'
        path, _, _ = PluginInstaller(None)._sync_plugin_source('file:///example', None, is_local=True)
    assert path.parent == tmp_path
    assert path.name.startswith('dlmtags-plugin-')
