# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""The DLMtags workspace resizes freely: no panel imposes a large minimum width."""

from types import SimpleNamespace
from unittest.mock import (
    MagicMock,
    patch,
)

from PyQt6 import (
    QtCore,
    QtGui,
    QtWidgets,
)

from test.test_dlmtags_inline_suggestions import InlineTestCase

from picard.config import get_config

from picard.ui.coverartbox import CoverArtBox
from picard.ui.dlmtags.tag_editor import PRIMARY_TAGS
from picard.ui.dlmtags.tag_form import FORM_VALUE_MIN_WIDTH
from picard.ui.dlmtags.workspace import (
    ARTWORK_STACK_WIDTH,
    DLMtagsWorkspace,
)
from picard.ui.enums import MainAction


# Half of a 1366 px screen, minus the 220 px library sidebar and a splitter handle.
HALF_1366_WORKSPACE = 1366 // 2 - 220 - 6


class Window(QtWidgets.QMainWindow):
    """The parts of MainWindow the workspace uses, with real Qt widgets."""

    def __init__(self, tagger, file):
        super().__init__()
        self.tagger = tagger
        self.action_map = {
            action: QtGui.QAction(name, self) for action, name in (
                (MainAction.AUTOTAG, 'Lookup'), (MainAction.ANALYZE, 'Scan'),
                (MainAction.SAVE, 'Save'), (MainAction.SHOW_COVER_ART, 'Cover Art'),
            )
        }
        self.search_edit = QtWidgets.QLineEdit()
        self.search_combo = QtWidgets.QComboBox()
        self.selected_objects = []
        box = MagicMock()
        box.files = {file}
        box.tag_diff.tag_names = list(PRIMARY_TAGS)
        box.tag_diff.new.__getitem__.side_effect = lambda tag: file.metadata.getall(tag)
        box.tag_diff.new.status.return_value = SimpleNamespace(is_grouped=False)
        box._tag_is_editable.side_effect = lambda tag: not tag.startswith('~')
        box.item.side_effect = lambda row, column: QtWidgets.QTableWidgetItem(
            (file.orig_metadata if column == 1 else file.metadata)[PRIMARY_TAGS[row]]
        )
        self.metadata_box = box
        self.cover_art_box = CoverArtBox(self)

    def stop_network_requests(self):
        pass

    def show_setup_wizard(self, force=False):
        pass


class WorkspaceResizeTest(InlineTestCase):
    def setUp(self):
        super().setUp()
        self.patch_tagger_instance(
            'picard.ui', 'picard.ui.metadatabox', 'picard.ui.coverartbox', 'picard.ui.coverartbox.coverartthumbnail',
        )
        patcher = patch.object(type(get_config().setting), 'setting_changed', MagicMock(), create=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tagger.webservice.num_pending_web_requests = 0
        self.window = Window(self.tagger, self.file)
        self.addCleanup(self.delete_window)
        self.workspace = DLMtagsWorkspace(self.window)
        self.window.setCentralWidget(self.workspace)
        self.scan(releases=[])
        self.file.metadata['title'] = 'A rather long song title that should wrap or elide, not widen the window'
        self.workspace.tag_editor.refresh()
        self.workspace.results.set_files({self.file})
        self.workspace.song_info.set_file(self.file)

    def delete_window(self):
        # Delete now: pending workspace timers must not fire in later tests.
        self.window.close()
        self.window.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete.value)

    def show(self, width, height):
        self.window.resize(width, height)
        self.window.show()
        for unused in range(3):
            QtWidgets.QApplication.processEvents()

    def test_minimum_width_fits_half_of_a_1366_screen(self):
        self.show(1100, 700)
        minimum = self.window.minimumSizeHint()
        self.assertLessEqual(minimum.width(), HALF_1366_WORKSPACE)
        self.assertLessEqual(minimum.height(), 768 - 100)
        # The panels themselves no longer carry fixed minimum widths.
        self.assertEqual(0, self.workspace.song_info.minimumWidth())
        self.assertEqual(0, self.workspace.artwork_panel.minimumWidth())

    def test_resizes_to_common_screen_sizes_and_back(self):
        for width, height in ((1366 - 226, 768 - 60), (1920 - 226, 1080 - 60), (HALF_1366_WORKSPACE, 600), (900, 600)):
            self.show(width, height)
            self.assertEqual(QtCore.QSize(width, height), self.window.size())
            for panel in (self.workspace.tag_editor, self.workspace.song_info, self.workspace.artwork_panel):
                self.assertTrue(panel.isVisible())
                self.assertGreater(panel.width(), 0)
                self.assertGreater(panel.height(), 0)

    def test_initial_splitter_proportions_and_manual_sizes(self):
        self.show(1400, 900)
        top, bottom = self.workspace.sections.sizes()
        self.assertAlmostEqual(0.45, top / (top + bottom), delta=0.05)
        left, right = self.workspace.bottom.sizes()
        self.assertAlmostEqual(0.55, left / (left + right), delta=0.05)
        # The user can still set other proportions, and panes never collapse.
        self.workspace.bottom.setSizes([300, 700])
        left, right = self.workspace.bottom.sizes()
        self.assertAlmostEqual(0.3, left / (left + right), delta=0.01)
        self.workspace.bottom.setSizes([0, 1000])
        self.assertGreater(self.workspace.bottom.sizes()[0], 0)
        self.assertFalse(self.workspace.bottom.childrenCollapsible())
        self.assertFalse(self.workspace.sections.childrenCollapsible())

    def test_narrow_edit_tags_scrolls_instead_of_hiding_values(self):
        self.show(HALF_1366_WORKSPACE, 600)
        editor = self.workspace.tag_editor
        self.assertTrue(editor.horizontalScrollBar().maximum() > 0)
        control = editor._rows['title'][2]
        self.assertGreaterEqual(control.width(), FORM_VALUE_MIN_WIDTH)
        find = editor._find_buttons['album']
        self.assertGreaterEqual(find.width(), find.minimumWidth())

    def test_artwork_stacks_when_narrow_and_returns_side_by_side(self):
        self.show(1400, 800)
        self.workspace.bottom.setSizes([1400 - ARTWORK_STACK_WIDTH - 100, ARTWORK_STACK_WIDTH + 100])
        QtWidgets.QApplication.processEvents()
        self.assertEqual(QtWidgets.QBoxLayout.Direction.LeftToRight, self.workspace._artwork_row.direction())
        self.workspace.bottom.setSizes([1400 - 250, 250])
        QtWidgets.QApplication.processEvents()
        self.assertLess(self.workspace.artwork_panel.width(), ARTWORK_STACK_WIDTH)
        self.assertEqual(QtWidgets.QBoxLayout.Direction.TopToBottom, self.workspace._artwork_row.direction())
        self.workspace.bottom.setSizes([700, 700])
        QtWidgets.QApplication.processEvents()
        self.assertEqual(QtWidgets.QBoxLayout.Direction.LeftToRight, self.workspace._artwork_row.direction())

    def test_song_info_buttons_stay_whole_when_narrow(self):
        self.show(HALF_1366_WORKSPACE, 600)
        panel = self.workspace.song_info
        for button in (panel.recognition.details_toggle, panel.open_button):
            if button.isVisible():
                self.assertGreaterEqual(button.width(), button.minimumSizeHint().width())
        # Long text wraps inside the panel's own scroll area.
        self.assertTrue(panel.title_label.wordWrap())
        self.assertLessEqual(panel.scroll.widget().width(), panel.scroll.viewport().width())
