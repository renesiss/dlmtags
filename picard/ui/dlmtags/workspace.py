# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""Modern workspace composed over the existing Picard widgets and actions.

Edit Tags spans the top. Below it, Wikipedia song info (with the compact
recognition state) sits next to the artwork. The album is chosen in New Tags
through Find Tag; nothing is saved without Save.
"""

from collections import Counter
import os.path

from PyQt6 import (
    QtCore,
    QtGui,
    QtWidgets,
)

from picard.file import File
from picard.i18n import gettext as _
from picard.smartscan import (
    OUTCOME_LABELS,
    REASON_LABELS,
    Outcome,
    Reason,
    Source,
    candidate_artist_names,
    decision_label,
)
from picard.tagprovenance import (
    provider_names,
    source_for,
)
from picard.util import iter_files_from_objects

from picard.ui.dlmtags.artwork import ArtworkSuggestions
from picard.ui.dlmtags.bulk_autotag import BulkAutoTagDialog
from picard.ui.dlmtags.components.action_button import ActionButton
from picard.ui.dlmtags.components.card import Card
from picard.ui.dlmtags.components.section_header import SectionHeader
from picard.ui.dlmtags.components.splitter import Splitter
from picard.ui.dlmtags.components.styles import style_component
from picard.ui.dlmtags.components.table import CompactTable
from picard.ui.dlmtags.song_info import SongInfoPanel
from picard.ui.dlmtags.suggestions import (
    album_search_status,
    status_text,
)
from picard.ui.dlmtags.tag_form import TagEditor
from picard.ui.enums import MainAction


class MatchingResults(QtWidgets.QWidget):
    """Compact recognition state of the selected song: a status chip, Scan
    progress and on-demand technical details. No invented candidate rows.

    The Song Info panel places `status_label` and `details_toggle` in its
    header; the album is chosen in New Tags through Find Tag.
    """

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._window = window
        self._targets = []
        self._files = ()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.cancel_action = QtGui.QAction(_('Cancel network requests'), self)
        self.cancel_action.triggered.connect(window.stop_network_requests)
        # Recognition and field searches stay in the app. Explicit browser
        # actions remain available in Picard's menus.
        window.search_edit.hide()
        window.search_combo.hide()
        self.status_label = QtWidgets.QLabel(self)
        self.status_label.setObjectName('dlmtags_recognition_status')
        self.status_label.setProperty('dlmtagsHint', True)
        self.status_label.setAccessibleName(_('Recognition status'))
        self.details_toggle = QtWidgets.QToolButton(self)
        self.details_toggle.setObjectName('dlmtags_matching_details_toggle')
        self.details_toggle.setText(_('Details'))
        self.details_toggle.setToolTip(_('Show technical recognition details'))
        self.details_toggle.setCheckable(True)
        self.details_toggle.setAutoRaise(True)
        self.details_toggle.toggled.connect(self._details_toggled)

        self.progress = QtWidgets.QWidget(self)
        progress_layout = QtWidgets.QHBoxLayout(self.progress)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(6)
        self.elapsed_label = QtWidgets.QLabel(self.progress)
        bar = QtWidgets.QProgressBar(self.progress)
        bar.setObjectName('dlmtagsSearchProgress')
        bar.setAccessibleName(_('Search progress'))
        bar.setRange(0, 0)
        bar.setTextVisible(False)
        bar.setMaximumHeight(8)
        progress_layout.addWidget(self.elapsed_label)
        progress_layout.addWidget(bar, 1)
        progress_layout.addWidget(ActionButton(self.cancel_action, self.progress))
        layout.addWidget(self.progress)
        self.progress.hide()
        self._elapsed = QtCore.QElapsedTimer()
        self._clock = QtCore.QTimer(self)
        self._clock.setInterval(250)
        self._clock.timeout.connect(self._update_elapsed)
        self._operation_timer = QtCore.QTimer(self)
        self._operation_timer.setSingleShot(True)
        self._operation_timer.timeout.connect(self._update_operation)
        self._settle_timer = QtCore.QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.setInterval(750)
        self._settle_timer.timeout.connect(self._finish_operation)

        self.details = QtWidgets.QWidget(self)
        details_layout = QtWidgets.QVBoxLayout(self.details)
        details_layout.setContentsMargins(0, 0, 0, 0)
        self.hint = QtWidgets.QLabel(_('No matching candidates to display.'), self.details)
        self.hint.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.hint.setWordWrap(True)
        self.hint.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self.hint.setProperty('dlmtagsHint', True)
        details_layout.addWidget(self.hint)
        self.table = CompactTable(self.details)
        model = QtGui.QStandardItemModel(self)
        model.setHorizontalHeaderLabels([_('Artist'), _('Title / Version'), _('Evidence')])
        self.table.setModel(model)
        self.table.hide()
        details_layout.addWidget(self.table, 1)
        layout.addWidget(self.details, 1)
        self.details.hide()

        for action_id in (MainAction.AUTOTAG, MainAction.ANALYZE):
            window.action_map[action_id].triggered.connect(self._watch_operation)
        window.tagger.tagger_stats_changed.connect(self._queue_update)
        window.tagger.webservice.pending_requests_changed.connect(self._queue_update)
        self._queue_update()

    def set_files(self, files):
        """Follow the selection: one song shows its own recognition state."""
        self._files = tuple(file for file in files if isinstance(file, File))
        self._render()

    def _watch_operation(self, *args):
        targets = list(self._window.selected_objects)
        targets.extend(iter_files_from_objects(tuple(targets)))
        for target in targets:
            if target not in self._targets:
                self._targets.append(target)
        self._queue_update()

    def watch_file(self, file):
        if file not in self._targets:
            self._targets.append(file)
        self._queue_update()

    def _queue_update(self, *args):
        # Inspect pending state after backend callbacks finish assigning it.
        self._operation_timer.start(0)

    def _update_operation(self):
        pending_network = bool(self._window.tagger.webservice.num_pending_web_requests)
        active = self._has_active_operation()
        self.cancel_action.setEnabled(active or pending_network)

        if active or (self._targets and pending_network):
            self._settle_timer.stop()
            if not self._clock.isActive():
                if not self._elapsed.isValid():
                    self._elapsed.start()
                self._clock.start()
            self._update_elapsed()
            self.progress.show()
        elif self._targets:
            if not self._settle_timer.isActive():
                self._settle_timer.start()
        else:
            # Album suggestions may settle after the Scan itself finished.
            self._render()

    def _has_active_operation(self):
        for target in self._targets:
            task = getattr(target, '_lookup_task', None)
            if (
                isinstance(target, File)
                and (target.state == File.State.PENDING or target.smart_genre_pending or target.tag_search_pending)
            ) or (task is not None and not getattr(task, 'aborted', False)):
                return True
        return False

    def _finish_operation(self):
        pending_network = bool(self._window.tagger.webservice.num_pending_web_requests)
        if pending_network or self._has_active_operation():
            self._queue_update()
            return

        self._clock.stop()
        self.progress.hide()
        self._targets.clear()
        self._elapsed.invalidate()
        self._render()
        self._window.metadata_box.update()

    def _render(self):
        files = self._files
        single = files[0] if len(files) == 1 else None
        decision = single.smart_scan_decision if single is not None else None
        lines = self._describe(single) if single is not None else self._describe_many(files)
        self.hint.setText('\n'.join(lines))
        if decision is not None:
            self.status_label.setText(decision_label(decision))
            self.status_label.setToolTip('\n'.join(lines[1:3]))
        else:
            self.status_label.setText('')
            self.status_label.setToolTip('')
        self.status_label.setVisible(decision is not None)
        self.details_toggle.setVisible(decision is not None or len(files) > 1)
        self._details_toggled(self.details_toggle.isChecked())
        self._show_candidates(decision)

    def _details_toggled(self, expanded):
        self.details.setVisible(expanded and not self.details_toggle.isHidden())

    def _describe_many(self, files):
        decisions = [file.smart_scan_decision for file in files if file.smart_scan_decision is not None]
        if not decisions:
            return (
                [_('No matching candidates to display.')] if not files else [_('Select one song to see its details.')]
            )
        counts = Counter(decision.outcome for decision in decisions)
        return [
            ', '.join('%s: %d' % (_(OUTCOME_LABELS[outcome]), count) for outcome, count in counts.items()),
            _('Select one song to see its details.'),
        ]

    def _describe(self, file):
        """The Smart Scan decision of one file and the provenance it retained."""
        decision = file.smart_scan_decision
        if decision is None:
            return [_('No matching candidates to display.')]
        lines = [decision_label(decision)]
        if decision.details.get('temporary') and decision.details.get('http_status') == 503:
            lines.append(_('Service temporarily unavailable (HTTP 503). Try Scan again later.'))
        match = decision.match if decision.outcome == Outcome.MATCHED else None
        if match and match.track:
            artist = candidate_artist_names(match.track)[0]
            lines.append('%s — %s' % (artist, match.track.get('title', '')))
            recognition = 'AcoustID + MusicBrainz' if decision.source == Source.ACOUSTID else 'MusicBrainz'
            lines.append(_('Recognition: %s') % recognition)
            if decision.user_confirmed:
                lines.append(
                    _('Recording confirmed by user, not verified automatically: %s')
                    % '; '.join(_(REASON_LABELS[reason]) for reason in decision.notes)
                )
            labels = decision.details.get('source_labels', ())
            if labels:
                lines.append(_('Source description: %s') % ', '.join(labels))
            additional = sorted(
                {
                    name
                    for tag in file.dlmtags_tag_sources
                    if file._dlmtags_source_recordings.get(tag) == match.track['id']
                    for name in provider_names(source_for(file, tag))
                    if name in {'Discogs', 'Last.fm'}
                }
            )
            if additional:
                lines.append(_('Additional metadata: %s') % ', '.join(additional))
            if decision.release_verified:
                lines.append(_('Album: verified release'))
            else:
                lines.append(_('Recording identified. Album details were kept unchanged.'))
                album = album_search_status(file)
                if album is not None:
                    lines.append(
                        _('Album suggestions: %(status)s — %(message)s')
                        % {
                            'status': status_text(album[0]),
                            'message': album[1],
                        }
                    )
        elif decision.reasons:
            lines.append('; '.join(_(REASON_LABELS[reason]) for reason in decision.reasons))
        source = source_for(file, 'genre') if file.dlmtags_tag_sources.get('genre') else ''
        if set(provider_names(source)) & {'MusicBrainz', 'Discogs', 'Last.fm'}:
            lines.append(_('Genre source: %s') % source)
        return lines

    def _show_candidates(self, decision):
        # Retain full release/candidate information in the Decision, but show
        # alternatives only when the recording identity is genuinely ambiguous.
        model = self.table.model()
        model.removeRows(0, model.rowCount())
        self.table.hide()
        if (
            decision is None
            or decision.outcome != Outcome.NEEDS_REVIEW
            or not ({Reason.CLOSE_SCORES, Reason.SOURCE_DISAGREEMENT} & set(decision.reasons))
        ):
            return
        groups = {}
        for candidate in decision.candidates:
            if candidate.recording_id not in groups:
                groups[candidate.recording_id] = candidate
        if len(groups) < 2:
            return
        for candidate in sorted(groups.values(), key=lambda c: c.similarity, reverse=True):
            evidence = [_(label) for label in getattr(candidate, 'evidence', ())]
            evidence.extend(_(REASON_LABELS[reason]) for reason in candidate.reasons)
            values = (candidate.artist, candidate.title, '; '.join(evidence))
            row = []
            for value in values:
                item = QtGui.QStandardItem(value)
                item.setEditable(False)
                item.setToolTip(_('Recording MBID: %s') % candidate.recording_id)
                row.append(item)
            model.appendRow(row)
        self.table.show()

    def _update_elapsed(self):
        minutes, seconds = divmod(self._elapsed.elapsed() // 1000, 60)
        stages = {getattr(target, 'smart_scan_stage', None) for target in self._targets}
        if 'scan' in stages and 'lookup' in stages:
            stage = _('Scan / Metadata Lookup')
        elif 'scan' in stages:
            stage = _('Scan')
        elif 'lookup' in stages:
            stage = _('Metadata Lookup')
        elif any(isinstance(target, File) and target.smart_genre_pending for target in self._targets):
            stage = _('Genre Lookup')
        elif any(isinstance(target, File) and target.tag_search_pending for target in self._targets):
            stage = _('Find Tag')
        else:
            stage = _('Searching...')
        self.elapsed_label.setText(
            _('%(stage)s %(elapsed)s')
            % {
                'stage': stage,
                'elapsed': f'{minutes:02d}:{seconds:02d}',
            }
        )


# Artwork panel narrower than this stacks the current cover above the suggestions.
ARTWORK_STACK_WIDTH = 380
ARTWORK_COLUMN_MIN_WIDTH = 130
ARTWORK_COLUMN_MAX_WIDTH = 200
ARTWORK_STACKED_HEIGHT = 190
QWIDGETSIZE_MAX = 16777215
TOOLBAR_ICON_SIZE = QtCore.QSize(26, 26)


def _toolbar_icon(name):
    """Load a bundled DLMtags SVG independent of the process working directory."""
    path = os.path.join(os.path.dirname(__file__), 'icons', name)
    return QtGui.QIcon(path)


class DLMtagsWorkspace(QtWidgets.QWidget):
    """Edit Tags on top; Song Info and Artwork side by side below."""

    def __init__(self, window, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 0)
        layout.setSpacing(6)
        # QToolBar moves actions into its extension menu instead of making the
        # entire window as wide as all translated button labels combined.
        actions = QtWidgets.QToolBar(self)
        actions.setMovable(False)
        actions.setFloatable(False)
        actions.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._window = window
        self._artwork_stacked = None
        toolbar_action_ids = {MainAction.ANALYZE, MainAction.SAVE}
        for action_id, action in window.action_map.items():
            if action is not None and action_id not in toolbar_action_ids:
                action.setShortcut(QtGui.QKeySequence())
        bulk_action = QtGui.QAction(_('Bulk Auto-Tag'), actions)
        bulk_action.setIcon(_toolbar_icon('bulk-auto-tag.svg'))
        bulk_action.setObjectName('dlmtags_bulk_auto_tag_action')
        bulk_action.setToolTip(_('Scan selected songs and review proposed tags'))
        bulk_action.triggered.connect(lambda: BulkAutoTagDialog(window).exec())
        self.bulk_action = bulk_action
        window.file_sidebar.selected_songs_changed.connect(self._update_bulk_action)
        self._update_bulk_action()
        actions.addAction(bulk_action)
        bulk_button = actions.widgetForAction(bulk_action)
        if bulk_button is not None:
            style_component(bulk_button, 'actionButton')
            bulk_button.setIconSize(TOOLBAR_ICON_SIZE)
        for action_id in (MainAction.ANALYZE, MainAction.SAVE):
            action = window.action_map[action_id]
            if action_id == MainAction.ANALYZE:
                action.setIcon(_toolbar_icon('scan.svg'))
            elif action_id == MainAction.SAVE:
                action.setIcon(_toolbar_icon('save.svg'))
            actions.addAction(action)
            button = actions.widgetForAction(action)
            style_component(button, 'actionButton')
            if action_id in {MainAction.ANALYZE, MainAction.SAVE}:
                button.setIconSize(TOOLBAR_ICON_SIZE)
        # Keep settings reachable from the DLMtags toolbar.
        settings_action = QtGui.QAction(_('Settings'), actions)
        settings_action.setIcon(_toolbar_icon('settings.svg'))
        settings_action.setObjectName('dlmtags_settings_action')
        settings_action.setToolTip(_('Configure music sources and other settings'))
        settings_action.triggered.connect(lambda checked=False: window.show_options())
        actions.addAction(settings_action)
        settings_button = actions.widgetForAction(settings_action)
        style_component(settings_button, 'actionButton')
        settings_button.setIconSize(TOOLBAR_ICON_SIZE)
        layout.addWidget(actions)

        self.sections = Splitter(QtCore.Qt.Orientation.Vertical, self)
        self.sections.setObjectName('dlmtags_editor_results_splitter')
        layout.addWidget(self.sections, 1)

        editor = Card(shadow=True)
        editor.setMinimumHeight(140)
        editor.content_layout.addWidget(SectionHeader(_('EDIT TAGS'), editor))
        # The MetadataBox remains the backend-facing bridge, not the visible UI.
        window.metadata_box.hide()
        self.tag_editor = TagEditor(window.metadata_box, editor)
        editor.content_layout.addWidget(self.tag_editor, 1)
        # "Show metadata view" toggles the tag editor; Picard checks its
        # visibility before updating the selection.
        window.metadata_view = editor
        self.sections.addWidget(editor)

        self.bottom = Splitter(QtCore.Qt.Orientation.Horizontal)
        self.bottom.setObjectName('dlmtags_results_artwork_splitter')
        self.results = MatchingResults(window)
        self.tag_editor.tag_search_started.connect(self.results.watch_file)
        # No fixed minimum widths here: both panels scroll and wrap their own
        # content, so the window can shrink (e.g. half of a 1366 px screen).
        self.song_info = SongInfoPanel(window.tagger, self.results)
        self.bottom.addWidget(self.song_info)
        self.bottom.addWidget(self._make_artwork_panel(window))
        self.bottom.setStretchFactor(0, 55)
        self.bottom.setStretchFactor(1, 45)
        self.bottom.setSizes([550, 450])
        self.sections.addWidget(self.bottom)
        self.sections.setStretchFactor(0, 45)
        self.sections.setStretchFactor(1, 55)
        self.sections.setSizes([450, 550])
        window.metadata_box.presentation_changed.connect(self._selection_changed)
        self._selection_changed()

    def _update_bulk_action(self):
        self.bulk_action.setEnabled(bool(self._window.file_sidebar._selected_paths()))

    def _make_artwork_panel(self, window):
        self.artwork_panel = Card(shadow=True)
        self.artwork_panel.content_layout.addWidget(SectionHeader(_('ARTWORK'), self.artwork_panel))
        # Side by side when there is room, stacked when the panel is narrow.
        row = self._artwork_row = QtWidgets.QBoxLayout(QtWidgets.QBoxLayout.Direction.LeftToRight)
        row.setSpacing(10)
        # Current New/Original artwork: Picard's own CoverArtBox with all its
        # handlers. The scroll area isolates its pixmap minimum hints.
        artwork_scroll = self._artwork_scroll = QtWidgets.QScrollArea(self.artwork_panel)
        artwork_scroll.setWidgetResizable(True)
        artwork_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        artwork_scroll.setWidget(window.cover_art_box)
        style_component(artwork_scroll, 'scrollArea')
        row.addWidget(artwork_scroll, 2)
        for thumbnail in (window.cover_art_box.cover_art, window.cover_art_box.orig_cover_art):
            thumbnail.setMaximumSize(160, 160)
            window.cover_art_box.layout.setAlignment(thumbnail, QtCore.Qt.AlignmentFlag.AlignHCenter)
            thumbnail.installEventFilter(self)
        self.artwork = ArtworkSuggestions(window.tagger, self.artwork_panel)
        suggestions_scroll = QtWidgets.QScrollArea(self.artwork_panel)
        suggestions_scroll.setWidgetResizable(True)
        suggestions_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        suggestions_scroll.setWidget(self.artwork)
        style_component(suggestions_scroll, 'scrollArea')
        row.addWidget(suggestions_scroll, 3)
        self.artwork_panel.content_layout.addLayout(row, 1)
        self.artwork_panel.installEventFilter(self)
        self._arrange_artwork()
        return self.artwork_panel

    def _arrange_artwork(self):
        stacked = self.artwork_panel.width() < ARTWORK_STACK_WIDTH
        if stacked is self._artwork_stacked:
            return
        self._artwork_stacked = stacked
        self._artwork_row.setDirection(
            QtWidgets.QBoxLayout.Direction.TopToBottom if stacked else QtWidgets.QBoxLayout.Direction.LeftToRight
        )
        if stacked:
            # Current artwork on top, suggestions below; both keep scrolling.
            self._artwork_scroll.setMinimumWidth(0)
            self._artwork_scroll.setMaximumSize(QWIDGETSIZE_MAX, ARTWORK_STACKED_HEIGHT)
        else:
            self._artwork_scroll.setMinimumWidth(ARTWORK_COLUMN_MIN_WIDTH)
            self._artwork_scroll.setMaximumSize(ARTWORK_COLUMN_MAX_WIDTH, QWIDGETSIZE_MAX)

    def _selection_changed(self):
        files = tuple(self._window.metadata_box.files)
        self.results.set_files(files)
        self.song_info.set_file(files[0] if len(files) == 1 else None)
        self.artwork.set_file(files[0] if len(files) == 1 else None)

    def eventFilter(self, watched, event):
        if watched is self.artwork_panel and event.type() == QtCore.QEvent.Type.Resize:
            self._arrange_artwork()
            return False
        thumbnails = (self._window.cover_art_box.cover_art, self._window.cover_art_box.orig_cover_art)
        if (
            any(watched is thumbnail for thumbnail in thumbnails)
            and event.type() == QtCore.QEvent.Type.MouseButtonRelease
            and event.button() == QtCore.Qt.MouseButton.LeftButton
            and watched.rect().contains(event.position().toPoint())
            and watched.data
        ):
            pixmap = QtGui.QPixmap()
            if pixmap.loadFromData(watched.data[0].data):
                dialog = QtWidgets.QDialog(self._window)
                dialog.setWindowTitle(_('Artwork preview'))
                layout = QtWidgets.QVBoxLayout(dialog)
                layout.addWidget(CoverPreview(pixmap, dialog))
                dialog.resize(640, 560)
                dialog.exec()
                dialog.deleteLater()
                return True
        return super().eventFilter(watched, event)


class CoverPreview(QtWidgets.QLabel):
    """Resize an existing decoded cover for inspection without changing artwork."""

    def __init__(self, pixmap, parent=None):
        super().__init__(parent)
        self._original = pixmap
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(100, 100)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Ignored)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setPixmap(
            self._original.scaled(
                self.size(),
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
        )
