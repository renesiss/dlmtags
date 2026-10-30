"""Manual Artwork search only stages an explicitly confirmed image."""

from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6 import (
    QtCore,
    QtGui,
    QtWidgets,
)
from PyQt6.QtNetwork import QNetworkReply

from picard.metadata import Metadata

import pytest

import picard.resources  # noqa: F401  # Register the bundled placeholder.

from picard.ui.dlmtags import artwork
from picard.ui.dlmtags.artwork import (
    ArtworkSuggestions,
    ManualArtworkDialog,
    ManualArtworkSearch,
)


RELEASE_ID = '12345678-1234-1234-1234-123456789abc'


def _png_bytes(color='blue'):
    image = QtGui.QImage(40, 30, QtGui.QImage.Format.Format_RGB32)
    image.fill(QtGui.QColor(color))
    data = QtCore.QByteArray()
    buffer = QtCore.QBuffer(data)
    buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, 'PNG')
    buffer.close()
    return bytes(data)


def _tagger():
    return SimpleNamespace(mb_api=Mock(), webservice=Mock())


def _file():
    metadata = Metadata()
    metadata['artist'] = 'Artist'
    metadata['title'] = 'Title'
    metadata['album'] = 'Album'
    return SimpleNamespace(metadata=metadata, can_show_coverart=True)


def test_manual_recording_search_displays_cover_thumbnails(qapp):
    tagger = _tagger()
    search = ManualArtworkSearch(tagger, 'Artist', 'Title', 'Album')
    search.start()
    args = tagger.mb_api.find_tracks.call_args
    assert 'recording:' in args.kwargs['query']
    assert 'artist:' in args.kwargs['query']
    assert 'release:' in args.kwargs['query']
    assert args.kwargs['advanced_search'] is True
    args.args[0]({'recordings': [{'releases': [{'id': RELEASE_ID, 'title': 'Album'}]}]}, None, None)
    assert tagger.webservice.download_url.call_count == 1
    tagger.webservice.download_url.call_args.kwargs['handler'](_png_bytes(), None, None)
    assert search.state == 'ready'
    assert len(search.images) == 1
    assert not search.images[0].pixmap.isNull()


def test_manual_release_search_reports_empty_timeout_and_network_error(qapp):
    tagger = _tagger()
    search = ManualArtworkSearch(tagger, 'Artist', '', 'Album')
    search.start()
    args = tagger.mb_api.find_releases.call_args
    assert 'artist:' in args.kwargs['query']
    assert 'release:' in args.kwargs['query']
    args.args[0]({'releases': []}, None, None)
    assert search.state == 'empty'
    assert 'No matching releases' in search.message

    timeout = ManualArtworkSearch(tagger, '', 'Title', '')
    timeout.start()
    tagger.mb_api.find_tracks.call_args.args[0](None, None, QNetworkReply.NetworkError.TimeoutError)
    assert timeout.state == 'error'
    assert 'timed out' in timeout.message

    failed = ManualArtworkSearch(tagger, 'Artist', '', '')
    failed.start()
    tagger.mb_api.find_releases.call_args.args[0](None, SimpleNamespace(errorString=lambda: 'offline'), 1)
    assert failed.state == 'error'
    assert 'offline' in failed.message


def test_manual_search_reports_missing_artwork_and_thumbnail_timeout(qapp):
    tagger = _tagger()
    search = ManualArtworkSearch(tagger, 'Artist', '', 'Album')
    search.start()
    tagger.mb_api.find_releases.call_args.args[0]({'releases': [{'id': RELEASE_ID}]}, None, None)
    tagger.webservice.download_url.call_args.kwargs['handler'](
        None, None, QNetworkReply.NetworkError.ContentNotFoundError
    )
    assert search.state == 'empty'
    assert 'No artwork' in search.message

    timed_out = ManualArtworkSearch(tagger, 'Artist', '', 'Album')
    timed_out.start()
    tagger.mb_api.find_releases.call_args.args[0]({'releases': [{'id': RELEASE_ID}]}, None, None)
    tagger.webservice.download_url.call_args.kwargs['handler'](
        None, SimpleNamespace(errorString=lambda: 'timeout'), QNetworkReply.NetworkError.TimeoutError
    )
    assert timed_out.state == 'error'
    assert 'timed out' in timed_out.message


def test_manual_dialog_previews_full_image_before_confirm_and_cancel_keeps_metadata(qapp):
    tagger = _tagger()
    file = _file()
    dialog = ManualArtworkDialog(tagger, file)
    assert dialog.artist.text() == 'Artist'
    assert dialog.title.text() == 'Title'
    assert dialog.album.text() == 'Album'
    dialog.search()
    tagger.mb_api.find_tracks.call_args.args[0](
        {'recordings': [{'releases': [{'id': RELEASE_ID, 'title': 'Album'}]}]}, None, None
    )
    tagger.webservice.download_url.call_args.kwargs['handler'](_png_bytes(), None, None)
    assert dialog.results.count() == 1
    assert not dialog.results.item(0).icon().isNull()
    dialog.results.setCurrentRow(0)
    assert dialog.cover is None
    assert not dialog.confirm_button.isEnabled()
    assert dialog.preview.pixmap() is not None
    tagger.webservice.download_url.call_args.kwargs['handler'](None, None, QNetworkReply.NetworkError.TimeoutError)
    assert dialog.cover is None
    assert not dialog.confirm_button.isEnabled()
    assert 'timed out' in dialog.status.text()
    dialog.select_result(dialog.results.item(0))
    tagger.webservice.download_url.call_args.kwargs['handler'](_png_bytes('red'), None, None)
    assert dialog.cover is not None
    assert dialog.confirm_button.isEnabled()
    assert not file.metadata.images
    dialog.reject()
    assert not file.metadata.images


def test_local_import_previews_without_staging_and_reports_invalid_file(qapp, monkeypatch, tmp_path):
    tagger = _tagger()
    file = _file()
    dialog = ManualArtworkDialog(tagger, file)
    image_path = tmp_path / 'cover.png'
    image_path.write_bytes(_png_bytes())

    def immediate(work, callback, **_kwargs):
        callback(result=work())

    monkeypatch.setattr(artwork, 'run_task', immediate)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, 'getOpenFileName', lambda *_args, **_kwargs: (str(image_path), 'Image files')
    )
    dialog.import_file()
    assert dialog.cover is not None
    assert dialog.confirm_button.isEnabled()
    assert dialog.preview.pixmap() is not None
    assert not file.metadata.images
    dialog.accept()
    assert not file.metadata.images

    bad = tmp_path / 'bad.png'
    bad.write_bytes(b'not an image')
    other = ManualArtworkDialog(tagger, file)
    other.load_local_path(str(bad))
    assert other.cover is None
    assert not other.confirm_button.isEnabled()
    assert 'not a readable image' in other.status.text()

    placeholder = tmp_path / 'placeholder.png'
    resource = QtCore.QFile(':/images/dlmtags-artwork-default.png')
    assert resource.open(QtCore.QIODevice.OpenModeFlag.ReadOnly)
    placeholder.write_bytes(bytes(resource.readAll()))
    blocked = ManualArtworkDialog(tagger, file)
    blocked.load_local_path(str(placeholder))
    assert blocked.cover is None
    assert not blocked.confirm_button.isEnabled()
    assert 'placeholder cannot be used' in blocked.status.text()


@pytest.mark.parametrize('accepted', [False, True])
def test_only_confirmed_manual_artwork_is_staged(qapp, monkeypatch, accepted):
    file = _file()
    cover = object()
    dialog = Mock()
    dialog.exec.return_value = (
        QtWidgets.QDialog.DialogCode.Accepted if accepted else QtWidgets.QDialog.DialogCode.Rejected
    )
    dialog.cover = cover
    dialog_type = Mock(return_value=dialog)
    setter = Mock()
    monkeypatch.setattr(artwork, 'ManualArtworkDialog', dialog_type)
    monkeypatch.setattr(artwork, 'CoverArtSetter', setter)
    panel = SimpleNamespace(_file=file, tagger=_tagger(), refresh=Mock())
    ArtworkSuggestions.open_manual_search(panel)
    assert setter.called is accepted
    if accepted:
        setter.return_value.set_coverart.assert_called_once()
    else:
        assert not file.metadata.images


def test_manual_action_is_enabled_only_for_selected_song(qapp, monkeypatch):
    class SelectedFile:
        class State:
            REMOVED = object()

    monkeypatch.setattr(artwork, 'File', SelectedFile)
    tagger = _tagger()
    panel = ArtworkSuggestions(tagger)
    assert not panel.manual_button.isEnabled()
    assert not panel.manual_button.icon().pixmap(26, 26).isNull()
    file = SelectedFile()
    file.state = object()
    file.can_show_coverart = True
    file.metadata = Metadata()
    file.smart_scan_decision = None
    panel.set_file(file)
    assert panel.manual_button.isEnabled()
    dialog = Mock()
    dialog.exec.return_value = QtWidgets.QDialog.DialogCode.Rejected
    opened = Mock(return_value=dialog)
    monkeypatch.setattr(artwork, 'ManualArtworkDialog', opened)
    panel.manual_button.click()
    opened.assert_called_once_with(tagger, file, panel)
    panel.set_file(None)
    assert not panel.manual_button.isEnabled()
