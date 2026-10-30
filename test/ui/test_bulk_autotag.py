"""Bulk review actions stage only explicitly approved proposals."""

from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6 import (
    QtCore,
    QtGui,
    QtWidgets,
)
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QDialog,
    QWidget,
)

from picard.album import Album
from picard.bulk_autotag import (
    AUTO_TAGS,
    Proposal,
)
from picard.cluster import Cluster
from picard.matching import SimMatchTrack
from picard.metadata import Metadata
from picard.smartscan import (
    Decision,
    Outcome,
    Reason,
)
from picard.track import Track
from picard.util import iter_files_from_objects

from picard.ui.dlmtags.bulk_autotag import (
    BulkAutoTagDialog,
    status_text,
)
from picard.ui.dlmtags.confirm_results import ConfirmResultsDialog
from picard.ui.dlmtags.sidebar import FileBrowserSidebar
from picard.ui.dlmtags.workspace import DLMtagsWorkspace


class _Window(QWidget):
    def __init__(self):
        super().__init__()
        self.tagger = SimpleNamespace(files={})
        self.selected_objects = []
        self.panel = SimpleNamespace(select_object=Mock())


def _window():
    return _Window()


def _file(name='track.mp3'):
    metadata = Metadata()
    metadata['title'] = 'Old title'
    metadata['artist'] = 'Old artist'
    metadata['album'] = 'Keep album'
    metadata['genre'] = 'Keep genre'
    metadata['date'] = '1999'
    metadata['tracknumber'] = '7'
    return SimpleNamespace(filename=name, metadata=metadata, update=Mock())


def _decision(score=0.91, outcome=Outcome.MATCHED):
    match = SimMatchTrack(score, None, None, {'id': 'recording-id', 'title': 'Correct title'})
    return Decision(outcome, match=match, reasons=(Reason.CLOSE_SCORES,) if outcome == Outcome.NEEDS_REVIEW else ())


def _matched(dialog, name='track.mp3', score=0.91):
    file = _file(name)
    original = Metadata(file.metadata)
    proposal = Proposal(
        {
            'title': (['Old title'], ['Correct title']),
            'artist': (['Old artist'], ['Correct artist']),
            'date': (['1999'], ['2001']),
            'genre': (['Keep genre'], ['New genre']),
        },
        score,
    )
    dialog._add(file, 'matched', 'Matched', proposal, original, _decision(score))
    return dialog.results[-1]


def test_confirm_stages_four_tags_only(qapp):
    dialog = BulkAutoTagDialog(_window())
    result = _matched(dialog)
    artwork = result.file.metadata.images
    assert result.file.metadata['title'] == 'Old title'
    assert dialog.confirm(result)
    assert {tag: result.file.metadata[tag] for tag in ('title', 'artist', 'date', 'genre')} == {
        'title': 'Correct title',
        'artist': 'Correct artist',
        'date': '2001',
        'genre': 'New genre',
    }
    assert result.file.metadata['album'] == 'Keep album'
    assert result.file.metadata['tracknumber'] == '7'
    assert result.file.metadata.images is artwork
    result.file.update.assert_called_once_with()
    assert result.state == 'confirmed'


def test_cancel_rejects_one_without_metadata_changes(qapp):
    dialog = BulkAutoTagDialog(_window())
    result = _matched(dialog)
    before = Metadata(result.file.metadata)
    assert dialog.cancel(result)
    assert result.file.metadata == before
    result.file.update.assert_not_called()
    assert not dialog.confirm(result)
    assert result.state == 'cancelled'


def test_confirm_all_excludes_cancelled_and_ambiguous(qapp):
    dialog = BulkAutoTagDialog(_window())
    eligible = _matched(dialog, 'eligible.mp3')
    cancelled = _matched(dialog, 'cancelled.mp3')
    dialog.cancel(cancelled)
    ambiguous = _file('ambiguous.mp3')
    decision = _decision(1.0, Outcome.NEEDS_REVIEW)
    dialog._add(ambiguous, 'ambiguous', decision.describe(), None, Metadata(ambiguous.metadata), decision)
    dialog.confirm_all()
    assert eligible.state == 'confirmed'
    assert cancelled.state == 'cancelled'
    assert cancelled.file.metadata['title'] == 'Old title'
    assert ambiguous.metadata['title'] == 'Old title'
    ambiguous.update.assert_not_called()
    assert dialog.preview.count() == 3
    assert dialog.results[-1].status == 'ambiguous'


def test_cancel_all_rejects_pending_proposals(qapp):
    dialog = BulkAutoTagDialog(_window())
    first = _matched(dialog, 'first.mp3')
    second = _matched(dialog, 'second.mp3')
    confirmed = _matched(dialog, 'confirmed.mp3')
    dialog.confirm(confirmed)
    dialog.cancel_all()
    assert first.state == second.state == 'cancelled'
    assert confirmed.state == 'confirmed'
    assert confirmed.file.metadata['title'] == 'Correct title'
    assert first.file.metadata['title'] == second.file.metadata['title'] == 'Old title'
    first.file.update.assert_not_called()
    second.file.update.assert_not_called()


def test_results_sort_by_numeric_score_descending(qapp):
    dialog = BulkAutoTagDialog(_window())
    _matched(dialog, 'low.mp3', 0.7)
    _matched(dialog, 'exact.mp3', 1.0)
    _matched(dialog, 'middle.mp3', 0.9)
    assert [
        dialog.results[dialog.preview.item(i).data(QtCore.Qt.ItemDataRole.UserRole)].file.filename for i in range(3)
    ] == [
        'exact.mp3',
        'middle.mp3',
        'low.mp3',
    ]
    first = dialog.results[dialog.preview.item(0).data(QtCore.Qt.ItemDataRole.UserRole)]
    assert dialog._score(first.decision) == 1.0


def test_ambiguous_cannot_be_confirmed_and_uses_manual_review(qapp, monkeypatch):
    dialog = BulkAutoTagDialog(_window())
    file = _file()
    decision = _decision(0.91, Outcome.NEEDS_REVIEW)
    file.smart_scan_decision = decision
    dialog._add(file, 'ambiguous', decision.describe(), None, Metadata(file.metadata), decision)
    result = dialog.results[-1]
    assert not dialog.confirm(result)
    assert file.metadata['title'] == 'Old title'
    opened = Mock()
    opened.return_value.exec.return_value = QDialog.DialogCode.Accepted
    monkeypatch.setattr('picard.ui.dlmtags.bulk_autotag.can_confirm', lambda _file: True)
    monkeypatch.setattr('picard.ui.dlmtags.bulk_autotag.ConfirmResultsDialog', opened)
    dialog.review(result)
    opened.assert_called_once_with(file, dialog, allowed_tags={'title', 'artist', 'date', 'genre'})
    assert result.state == 'reviewed'


def test_manual_review_limits_followup_suggestions_to_bulk_tags(qapp):
    recording = SimpleNamespace(
        recording_id='recording-id',
        artist='Artist',
        title='Title',
        reasons=(),
        confirmable=True,
        plausible=True,
    )
    decision = SimpleNamespace(candidates=(), reasons=())
    file = SimpleNamespace(
        smart_scan_decision=decision,
        confirmable_recordings=lambda: [recording],
        manual_identity_edits=lambda: [],
        dlmtags_tag_suggestions={},
        dlmtags_date_suggestion=None,
    )
    date_suggestion = ('2001', 'MusicBrainz', 'https://musicbrainz.org/recording/recording-id')

    def confirm_recording(*_args, **_kwargs):
        file.dlmtags_tag_suggestions = {
            'album': object(),
            'style': object(),
            'genre': object(),
            'date': object(),
        }
        file.dlmtags_date_suggestion = date_suggestion
        return True

    file.confirm_recording = Mock(side_effect=confirm_recording)
    dialog = ConfirmResultsDialog(file, allowed_tags=set(AUTO_TAGS))
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert set(file.dlmtags_tag_suggestions) == {'genre', 'date'}
    assert file.dlmtags_date_suggestion == date_suggestion
    file.confirm_recording.assert_called_once_with(decision, 'recording-id', replace_manual=False)


def test_confirmation_buttons_are_icon_only_and_accessible(qapp):
    dialog = BulkAutoTagDialog(_window())
    result = _matched(dialog)
    for button in (dialog.confirm_all_button, dialog.cancel_all_button):
        assert button.text() == ''
        assert button.toolTip()
        assert button.accessibleName()
        assert not button.icon().isNull()
    assert not dialog.cards.confirm_icon.isNull()
    assert not dialog.cards.cancel_icon.isNull()
    assert result.row.toolTip() == result.file.filename
    assert dialog.preview.itemWidget(result.row) is None


def test_cards_show_full_song_identity_and_path_tooltip(qapp):
    dialog = BulkAutoTagDialog(_window())
    result = _matched(dialog, '/music/filename_that_is_not_the_title.mp3')
    result.original['title'] = 'A full title with many words'
    result.original['artist'] = 'The complete artist name'
    result.proposal.changes['title'] = (['A full title with many words'], ['The proposed full title'])
    result.proposal.changes['artist'] = (['The complete artist name'], ['The proposed artist name'])
    assert dialog.cards._headline(result) == ('The proposed full title', 'The proposed artist name')
    assert result.row.toolTip() == '/music/filename_that_is_not_the_title.mp3'
    assert not dialog.findChildren(QtWidgets.QTreeWidget)


def test_card_icon_click_confirms_without_item_widgets(qapp):
    dialog = BulkAutoTagDialog(_window())
    result = _matched(dialog)
    dialog.show()
    qapp.processEvents()
    card = dialog.preview.visualItemRect(result.row).adjusted(7, 4, -7, -4)
    point = dialog.cards.action_rects(card, result)['confirm'].center()
    QTest.mouseClick(dialog.preview.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=point)
    assert result.state == 'confirmed'
    assert dialog.preview.itemWidget(result.row) is None


def test_card_cancel_icon_rejects_proposal(qapp):
    dialog = BulkAutoTagDialog(_window())
    result = _matched(dialog)
    dialog.show()
    qapp.processEvents()
    card = dialog.preview.visualItemRect(result.row).adjusted(7, 4, -7, -4)
    point = dialog.cards.action_rects(card, result)['cancel'].center()
    QTest.mouseClick(dialog.preview.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=point)
    assert result.state == 'cancelled'
    assert result.file.metadata['title'] == 'Old title'


def test_needs_review_card_has_visible_review_action(qapp, monkeypatch):
    dialog = BulkAutoTagDialog(_window())
    file = _file()
    decision = _decision(0.91, Outcome.NEEDS_REVIEW)
    file.smart_scan_decision = decision
    dialog._add(file, 'ambiguous', decision.describe(), None, Metadata(file.metadata), decision)
    result = dialog.results[-1]
    opened = Mock()
    opened.return_value.exec.return_value = QDialog.DialogCode.Accepted
    monkeypatch.setattr('picard.ui.dlmtags.bulk_autotag.can_confirm', lambda _file: True)
    monkeypatch.setattr('picard.ui.dlmtags.bulk_autotag.ConfirmResultsDialog', opened)
    dialog.show()
    qapp.processEvents()
    card = dialog.preview.visualItemRect(result.row).adjusted(7, 4, -7, -4)
    point = dialog.cards.action_rects(card, result)['review'].center()
    QTest.mouseClick(dialog.preview.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=point)
    assert result.state == 'reviewed'
    opened.assert_called_once_with(file, dialog, allowed_tags={'title', 'artist', 'date', 'genre'})


def test_cards_scroll_with_large_result_set(qapp):
    dialog = BulkAutoTagDialog(_window())
    for index in range(1000):
        _matched(dialog, f'/music/song_{index}.mp3', index / 1000)
    dialog.total = 1000
    dialog.show()
    qapp.processEvents()
    assert dialog.preview.count() == 1000
    assert dialog.preview.verticalScrollBar().maximum() > 0
    assert all(dialog.preview.itemWidget(dialog.preview.item(i)) is None for i in (0, 500, 999))
    assert not dialog.grab().isNull()


def test_card_height_adapts_to_available_width(qapp):
    dialog = BulkAutoTagDialog(_window())
    result = _matched(dialog)
    result.proposal.changes['title'] = (['Old title'], ['A complete song title with many words ' * 8])
    dialog.resize(620, 640)
    dialog.show()
    qapp.processEvents()
    option = QtWidgets.QStyleOptionViewItem()
    option.font = dialog.font()
    narrow = dialog.cards.sizeHint(option, dialog.preview.indexFromItem(result.row)).height()
    dialog.resize(1200, 640)
    qapp.processEvents()
    wide = dialog.cards.sizeHint(option, dialog.preview.indexFromItem(result.row)).height()
    assert narrow > wide


def test_opening_popup_starts_all_loaded_tracks(qapp, monkeypatch):
    class LoadedFile:
        def __init__(self, name):
            self.filename = name
            self.metadata = Metadata()
            self.start_smart_scan = Mock(return_value=True)
            self.cancel_smart_scan = Mock()

    window = _window()
    first = LoadedFile('first.mp3')
    second = LoadedFile('second.mp3')
    window.tagger.files = {first.filename: first, second.filename: second}
    monkeypatch.setattr('picard.ui.dlmtags.bulk_autotag.File', LoadedFile)
    dialog = BulkAutoTagDialog(window)
    qapp.processEvents()
    qapp.processEvents()
    assert dialog.total == 2
    assert dialog.active is first
    first.start_smart_scan.assert_called_once()
    dialog.cancel_scan()


def test_sixteen_selected_tracks_all_reach_results_and_cards(qapp, monkeypatch):
    class SelectedFile:
        def __init__(self, name):
            self.filename = name
            self.metadata = Metadata()
            self.start_smart_scan = Mock(return_value=True)
            self.cancel_smart_scan = Mock()

        def iterfiles(self, save=False):
            yield self

    window = _window()
    selected = [SelectedFile(f'track_{index}.mp3') for index in range(16)]
    window.selected_objects = selected
    window.tagger.files = {file.filename: file for file in selected[:4]}
    monkeypatch.setattr('picard.ui.dlmtags.bulk_autotag.File', SelectedFile)
    monkeypatch.setattr(
        'picard.ui.dlmtags.bulk_autotag.propose', lambda _metadata, decision: Proposal({}, decision.match.similarity)
    )
    dialog = BulkAutoTagDialog(window)
    qapp.processEvents()
    qapp.processEvents()
    assert dialog.total == 16
    assert dialog.active is selected[0]
    pending_rows = {result.file.filename: result.row for result in dialog.results}
    for index, file in enumerate(selected):
        assert dialog.active is file
        outcome = (Outcome.MATCHED, Outcome.NEEDS_REVIEW, Outcome.NO_MATCH, Outcome.ERROR)[index % 4]
        decision = (
            _decision(index / 16, outcome) if outcome in {Outcome.MATCHED, Outcome.NEEDS_REVIEW} else Decision(outcome)
        )
        file.start_smart_scan.call_args.kwargs['bulk_callback'](file, decision)
        qapp.processEvents()
        qapp.processEvents()
    assert len(dialog.results) == dialog.preview.count() == 16
    assert {result.file for result in dialog.results} == set(selected)
    assert {result.file.filename: result.row for result in dialog.results} == pending_rows
    assert {result.status for result in dialog.results} == {'matched', 'ambiguous', 'skipped', 'failed'}
    scores = [dialog._score(dialog.cards._result(dialog.preview.model().index(row, 0)).decision) for row in range(16)]
    assert scores == sorted(scores, reverse=True)
    assert sum(status_text(result) == 'Already Correct' for result in dialog.results) == 4
    assert 'Selected UI objects: 16' in dialog.summary.text()
    assert 'Processed: 16' in dialog.summary.text()
    assert 'Displayed: 16' in dialog.summary.text()


def test_cancelled_scan_displays_every_selected_track_once(qapp, monkeypatch):
    class SelectedFile:
        def __init__(self, name):
            self.filename = name
            self.metadata = Metadata()
            self.callback = None

        def iterfiles(self, save=False):
            yield self

        def start_smart_scan(self, *, bulk_callback):
            self.callback = bulk_callback
            return True

        def cancel_smart_scan(self):
            assert self.callback is not None
            self.callback(self, Decision(Outcome.CANCELLED))

    window = _window()
    selected = [SelectedFile(f'track_{index}.mp3') for index in range(16)]
    window.selected_objects = selected
    window.tagger.files = {file.filename: file for file in selected[:4]}
    monkeypatch.setattr('picard.ui.dlmtags.bulk_autotag.File', SelectedFile)
    dialog = BulkAutoTagDialog(window)
    qapp.processEvents()
    qapp.processEvents()
    dialog.cancel_scan()
    qapp.processEvents()
    assert len(dialog.results) == dialog.preview.count() == 16
    assert {result.file for result in dialog.results} == set(selected)
    assert all(result.status == 'skipped' for result in dialog.results)
    assert 'Selected UI objects: 16' in dialog.summary.text()
    assert 'Processed: 16' in dialog.summary.text()
    assert 'Displayed: 16' in dialog.summary.text()


def test_sidebar_qt_selection_resolves_all_sixteen_paths(qapp, monkeypatch):
    class SelectedFile:
        def __init__(self, name):
            self.filename = name
            self.metadata = Metadata()
            self.start_smart_scan = Mock(return_value=True)
            self.cancel_smart_scan = Mock()

        def iterfiles(self, save=False):
            yield self

    window = _window()
    selected = [SelectedFile(f'/music/track_{index}.mp3') for index in range(16)]
    window.selected_objects = selected[:2]
    window.tagger.files = {file.filename: file for file in selected[:2]}
    loaded_callbacks = []

    def add_paths(paths, *, metadata_only, on_loaded):
        assert metadata_only
        assert paths == [file.filename for file in selected]
        for file in selected[2:]:
            window.tagger.files[file.filename] = file
            loaded_callbacks.append((on_loaded, file))

    window.tagger.add_paths = Mock(side_effect=add_paths)
    window.ignore_selection_changes = False
    window.tagger.format_registry = SimpleNamespace(formats_changed=Mock())
    monkeypatch.setattr('picard.ui.dlmtags.sidebar.tagger_instance', lambda: window.tagger)
    monkeypatch.setattr(FileBrowserSidebar, 'restore_state', lambda _self: None)
    monkeypatch.setattr(FileBrowserSidebar, '_restore_bookmarks', lambda _self: None)
    sidebar = FileBrowserSidebar(window)
    window.file_sidebar = sidebar
    assert not sidebar._browse_action.icon().isNull()
    assert not sidebar._browse_action.icon().pixmap(QtCore.QSize(16, 16)).isNull()
    assert sidebar._browse_action.toolTip()
    for file in selected:
        items = [QtGui.QStandardItem('1'), QtGui.QStandardItem(file.filename), QtGui.QStandardItem('—')]
        items[1].setData(file.filename, QtCore.Qt.ItemDataRole.UserRole)
        items[1].setData(False, QtCore.Qt.ItemDataRole.UserRole + 1)
        sidebar.song_model.appendRow(items)
    selection = QtCore.QItemSelection(sidebar.song_filter.index(0, 0), sidebar.song_filter.index(15, 2))
    selection_model = sidebar.songs.selectionModel()
    assert selection_model is not None
    selection_model.select(
        selection,
        QtCore.QItemSelectionModel.SelectionFlag.Select | QtCore.QItemSelectionModel.SelectionFlag.Rows,
    )
    assert len(selection_model.selectedRows(0)) == 16
    assert len(sidebar._selected_paths()) == 16
    assert window._dlmtags_bulk_selection_source == 'songs'
    assert len(list(iter_files_from_objects(window.selected_objects))) == 2
    monkeypatch.setattr('picard.ui.dlmtags.bulk_autotag.File', SelectedFile)
    dialog = BulkAutoTagDialog(window)
    qapp.processEvents()
    assert dialog.total == 16
    assert dialog.resolved_count == 16

    def finish(file):
        qapp.processEvents()
        assert dialog.active is file
        outcome = (Outcome.NO_MATCH, Outcome.ERROR, Outcome.CANCELLED, Outcome.NEEDS_REVIEW)[selected.index(file) % 4]
        file.start_smart_scan.call_args.kwargs['bulk_callback'](file, Decision(outcome))
        qapp.processEvents()

    for file in selected[:2]:
        finish(file)
    assert dialog.processed_count == 2
    assert len(dialog.results) == dialog.preview.count() == 16
    assert dialog.active is None
    for callback, file in loaded_callbacks:
        callback(file)
    QTest.qWait(60)
    for file in selected[2:]:
        finish(file)
    assert len(dialog.results) == dialog.preview.count() == 16
    assert {result.file for result in dialog.results} == set(selected)
    assert {result.status for result in dialog.results} == {'skipped', 'failed', 'ambiguous'}
    selected[0].start_smart_scan.call_args.kwargs['bulk_callback'](selected[0], Decision(Outcome.ERROR))
    assert len(dialog.results) == dialog.preview.count() == 16
    assert 'Resolved unique files: 16' in dialog.summary.text()
    assert 'Queued: 16' in dialog.summary.text()
    assert 'Processed: 16' in dialog.summary.text()
    assert 'Displayed: 16' in dialog.summary.text()
    selection_model.clearSelection()
    assert window._dlmtags_bulk_selection_source == 'picard'


def test_bulk_action_and_queue_follow_sidebar_selection(qapp, monkeypatch):
    class SelectedFile:
        def __init__(self, name):
            self.filename = name
            self.metadata = Metadata()
            self.start_smart_scan = Mock(return_value=True)
            self.cancel_smart_scan = Mock()

    window = _window()
    files = [SelectedFile(f'/music/track_{index}.mp3') for index in range(16)]
    window.tagger.files = {file.filename: file for file in files}
    window.selected_objects = files  # A stale Picard selection must not queue the library.
    window.ignore_selection_changes = False
    window.tagger.format_registry = SimpleNamespace(formats_changed=Mock())
    window.tagger.add_paths = Mock()
    monkeypatch.setattr('picard.ui.dlmtags.sidebar.tagger_instance', lambda: window.tagger)
    monkeypatch.setattr(FileBrowserSidebar, 'restore_state', lambda _self: None)
    monkeypatch.setattr(FileBrowserSidebar, '_restore_bookmarks', lambda _self: None)
    monkeypatch.setattr('picard.ui.dlmtags.bulk_autotag.File', SelectedFile)
    sidebar = FileBrowserSidebar(window)
    window.file_sidebar = sidebar
    for file in files:
        items = [QtGui.QStandardItem('1'), QtGui.QStandardItem(file.filename), QtGui.QStandardItem('—')]
        items[1].setData(file.filename, QtCore.Qt.ItemDataRole.UserRole)
        items[1].setData(False, QtCore.Qt.ItemDataRole.UserRole + 1)
        sidebar.song_model.appendRow(items)
    folder = [QtGui.QStandardItem('1'), QtGui.QStandardItem('folder'), QtGui.QStandardItem('—')]
    folder[1].setData('/music/folder', QtCore.Qt.ItemDataRole.UserRole)
    folder[1].setData(True, QtCore.Qt.ItemDataRole.UserRole + 1)
    sidebar.song_model.appendRow(folder)
    action = QtGui.QAction()
    workspace = SimpleNamespace(_window=window, bulk_action=action)

    def refresh():
        DLMtagsWorkspace._update_bulk_action(workspace)

    sidebar.selected_songs_changed.connect(refresh)
    selection_model = sidebar.songs.selectionModel()
    refresh()
    assert not action.isEnabled()

    for count in (1, 16, 0):
        selection_model.clearSelection()
        if count:
            selection_model.select(
                QtCore.QItemSelection(sidebar.song_filter.index(0, 0), sidebar.song_filter.index(count - 1, 2)),
                QtCore.QItemSelectionModel.SelectionFlag.Select | QtCore.QItemSelectionModel.SelectionFlag.Rows,
            )
        assert action.isEnabled() is bool(count)
        assert len(sidebar._selected_paths()) == count
        dialog = BulkAutoTagDialog(window)
        qapp.processEvents()
        assert dialog.selected_count == dialog.total == count
        dialog.cancel_scan()

    selection_model.select(
        sidebar.song_filter.index(16, 0),
        QtCore.QItemSelectionModel.SelectionFlag.Select | QtCore.QItemSelectionModel.SelectionFlag.Rows,
    )
    assert not action.isEnabled()
    selection_model.clearSelection()
    selection_model.select(
        sidebar.song_filter.index(0, 0),
        QtCore.QItemSelectionModel.SelectionFlag.Select | QtCore.QItemSelectionModel.SelectionFlag.Rows,
    )
    assert action.isEnabled()
    sidebar.song_model.removeRow(0)
    assert not action.isEnabled()
    selection_model.select(
        sidebar.song_filter.index(0, 0),
        QtCore.QItemSelectionModel.SelectionFlag.Select | QtCore.QItemSelectionModel.SelectionFlag.Rows,
    )
    assert action.isEnabled()
    monkeypatch.setattr(sidebar, '_update_song_filters', lambda: None)
    sidebar._preview_folder('/music/another-folder')
    assert not action.isEnabled()


def test_picard_selected_objects_expand_files_albums_clusters_and_child_tracks(qapp, monkeypatch):
    class SelectedFile:
        def __init__(self, name):
            self.filename = name
            self.metadata = Metadata()
            self.start_smart_scan = Mock(return_value=True)
            self.cancel_smart_scan = Mock()

        def iterfiles(self, save=False):
            yield self

    files = [SelectedFile(f'/music/track_{index}.mp3') for index in range(16)]
    album = Album('album-id')
    album_track = Track('album-track', album)
    album_track.files.extend(files[:4])
    album.tracks.append(album_track)
    cluster = Cluster('Cluster')
    cluster.files.extend(files[4:8])
    child_track = Track('child-track')
    child_track.files.extend(files[8:12])
    selected_objects = [album, cluster, child_track, *files[12:]]
    assert len(selected_objects) == 7
    assert list(iter_files_from_objects(selected_objects)) == files
    window = _window()
    window.selected_objects = selected_objects
    window.tagger.files = {file.filename: file for file in files[:2]}
    window._dlmtags_bulk_selection_source = 'picard'
    monkeypatch.setattr('picard.ui.dlmtags.bulk_autotag.File', SelectedFile)
    dialog = BulkAutoTagDialog(window)
    qapp.processEvents()
    assert dialog.selected_count == 7
    assert dialog.resolved_count == dialog.total == 16
    dialog.cancel_scan()


def test_sidebar_path_excluded_during_load_gets_skipped_card(qapp, monkeypatch):
    class SelectedFile:
        pass

    paths = ['/music/first.mp3', '/music/excluded.mp3']
    window = _window()
    window._dlmtags_bulk_selection_source = 'songs'
    selection = Mock()
    selection.selectedRows.return_value = [object(), object()]
    window.file_sidebar = SimpleNamespace(
        songs=SimpleNamespace(selectionModel=lambda: selection),
        _selected_paths=lambda: paths,
    )
    window.tagger.add_paths = Mock()
    monkeypatch.setattr('picard.ui.dlmtags.bulk_autotag.File', SelectedFile)
    dialog = BulkAutoTagDialog(window)
    for _ in range(4):
        qapp.processEvents()
    assert dialog.total == 2
    assert len(dialog.results) == dialog.preview.count() == 2
    assert all(result.status == 'skipped' for result in dialog.results)
    assert {result.file.filename for result in dialog.results} == set(paths)


def test_card_delegate_ignores_missing_result_in_all_event_paths(qapp):
    dialog = BulkAutoTagDialog(_window())
    option = QtWidgets.QStyleOptionViewItem()
    option.font = dialog.font()
    option.rect = QtCore.QRect(0, 0, 400, 220)
    invalid = QtCore.QModelIndex()
    assert dialog.cards.action_rects(option.rect, None) == {}
    assert dialog.cards.sizeHint(option, invalid).height() > 0
    pixmap = QtGui.QPixmap(400, 220)
    painter = QtGui.QPainter(pixmap)
    dialog.cards.paint(painter, option, invalid)
    painter.end()
    tooltip = QtGui.QHelpEvent(QtCore.QEvent.Type.ToolTip, QtCore.QPoint(20, 20), QtCore.QPoint(20, 20))
    assert not dialog.cards.helpEvent(tooltip, dialog.preview, option, invalid)
    mouse = QtGui.QMouseEvent(
        QtCore.QEvent.Type.MouseButtonRelease,
        QtCore.QPointF(20, 20),
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    assert not dialog.cards.editorEvent(mouse, dialog.preview.model(), option, invalid)
    key = QtGui.QKeyEvent(QtCore.QEvent.Type.KeyPress, QtCore.Qt.Key.Key_Return, QtCore.Qt.KeyboardModifier.NoModifier)
    assert not dialog.cards.editorEvent(key, dialog.preview.model(), option, invalid)
    unbound = QtWidgets.QListWidgetItem()
    dialog.preview.addItem(unbound)
    unbound_index = dialog.preview.indexFromItem(unbound)
    assert dialog.cards._result(unbound_index) is None
    assert not dialog.cards.helpEvent(tooltip, dialog.preview, option, unbound_index)
    assert not dialog.cards.editorEvent(mouse, dialog.preview.model(), option, unbound_index)


def test_all_sixteen_cards_are_visible_before_scans_finish(qapp, monkeypatch):
    class SelectedFile:
        def __init__(self, name):
            self.filename = name
            self.metadata = Metadata()
            self.start_smart_scan = Mock(return_value=True)
            self.cancel_smart_scan = Mock()

        def iterfiles(self, save=False):
            yield self

    window = _window()
    files = [SelectedFile(f'/music/track_{index}.mp3') for index in range(16)]
    window.selected_objects = files
    monkeypatch.setattr('picard.ui.dlmtags.bulk_autotag.File', SelectedFile)
    dialog = BulkAutoTagDialog(window)
    qapp.processEvents()
    qapp.processEvents()
    assert dialog.total == 16
    assert dialog.preview.count() == 16
    assert dialog.processed_count == 0
    assert all(result.status == 'pending' for result in dialog.results)
    assert {dialog.preview.item(index).toolTip() for index in range(16)} == {file.filename for file in files}
    dialog.show()
    qapp.processEvents()
    assert not dialog.grab().isNull()
    option = QtWidgets.QStyleOptionViewItem()
    option.rect = dialog.preview.visualItemRect(dialog.preview.item(0))
    index = dialog.preview.indexFromItem(dialog.preview.item(0))
    assert dialog.cards.action_rects(option.rect, dialog.results[0]) == {}
    tooltip = QtGui.QHelpEvent(QtCore.QEvent.Type.ToolTip, QtCore.QPoint(20, 20), QtCore.QPoint(20, 20))
    assert dialog.cards.helpEvent(tooltip, dialog.preview, option, index)
    mouse = QtGui.QMouseEvent(
        QtCore.QEvent.Type.MouseButtonRelease,
        QtCore.QPointF(20, 20),
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    assert not dialog.cards.editorEvent(mouse, dialog.preview.model(), option, index)
    key = QtGui.QKeyEvent(QtCore.QEvent.Type.KeyPress, QtCore.Qt.Key.Key_Return, QtCore.Qt.KeyboardModifier.NoModifier)
    assert not dialog.cards.editorEvent(key, dialog.preview.model(), option, index)
    dialog.cancel_scan()
    assert dialog.preview.count() == dialog.processed_count == 16
