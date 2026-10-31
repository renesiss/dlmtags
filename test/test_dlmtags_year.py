# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""New Tags → Year: the native 'date' tag, filled from the first release only."""

from types import SimpleNamespace
from unittest.mock import (
    MagicMock,
    patch,
)

from PyQt6 import QtWidgets

from test.test_dlmtags_inline_suggestions import InlineTestCase
from test.test_smartscan import (
    acoustid_recording,
    release,
)

from picard.file import File
from picard.metadata import Metadata
from picard.smartscan import (
    Outcome,
    decide_year,
    first_release_date,
)
from picard.tagprovenance import source_for

from picard.ui.dlmtags.tag_editor import (
    PRIMARY_FIELDS,
    PRIMARY_TAGS,
)
from picard.ui.dlmtags.tag_form import TagEditor


class YearTest(InlineTestCase):
    def test_shared_year_policy_preserves_uncertain_and_invalid_existing_years(self):
        track = {'first-release-date': '2001-05-02'}
        self.assertEqual(decide_year(track, []).automatic, '2001-05-02')
        self.assertEqual(decide_year(track, ['1999']).suggestion, '2001-05-02')
        self.assertEqual(decide_year(track, ['circa 1999']).automatic, '')
        self.assertEqual(decide_year(track, ['circa 1999']).suggestion, '')
        self.assertEqual(decide_year(track, [], deleted=True).automatic, '')
        self.assertEqual(decide_year({}, []).automatic, '')

    def scan_with(self, first_release=None, releases=None):
        if releases is None:
            releases = (release('rel', 'Album', '1999'),)
        track = acoustid_recording('rec', 'Song', 'Artist', 232000, 0.95, list(releases))
        if first_release is not None:
            track['first-release-date'] = first_release
        with patch.object(File, 'save') as save:
            self.file.start_smart_scan()
            self.tagger._acoustid.analyze.call_args.args[1]({'recordings': [track]}, None, None)
        save.assert_not_called()
        self.assertEqual(Outcome.MATCHED, self.file.smart_scan_decision.outcome)

    def editor(self):
        box = MagicMock()
        box.files = {self.file}
        box.tag_diff.tag_names = list(PRIMARY_TAGS)
        box.tag_diff.new.__getitem__.side_effect = lambda tag: self.file.metadata.getall(tag)
        box.tag_diff.new.status.return_value = SimpleNamespace(is_grouped=False)
        box._tag_is_editable.side_effect = lambda tag: not tag.startswith('~')
        box.item.side_effect = lambda row, column: QtWidgets.QTableWidgetItem(
            (self.file.orig_metadata if column == 1 else self.file.metadata)[PRIMARY_TAGS[row]]
        )
        editor = TagEditor(box)
        self.addCleanup(editor.deleteLater)
        return editor

    def test_label_is_year_on_the_native_date_tag(self):
        self.assertIn(('date', 'Year'), PRIMARY_FIELDS)
        self.assertNotIn('year', PRIMARY_TAGS)

    def test_full_first_release_date_shows_year_and_keeps_full_date_for_save(self):
        self.scan_with('2021-04-09')
        self.assertEqual(['2021-04-09'], self.file.metadata.getall('date'))
        self.assertEqual('MusicBrainz', source_for(self.file, 'date'))
        self.assertEqual('https://musicbrainz.org/recording/rec', self.file.dlmtags_tag_urls['date'])
        editor = self.editor()
        control = editor._rows['date'][2]
        self.assertEqual('2021', control.text())
        self.assertIn('2021-04-09', control.toolTip())
        self.assertEqual('MusicBrainz', editor._source_labels['date'].text())

    def test_year_only_source(self):
        self.scan_with('2021')
        self.assertEqual(['2021'], self.file.metadata.getall('date'))
        self.assertEqual('2021', self.editor()._rows['date'][2].text())

    def test_unknown_date_stays_empty(self):
        self.scan_with(releases=())
        self.assertEqual('', self.file.metadata['date'])
        editor = self.editor()
        self.assertEqual('', editor._rows['date'][2].text())
        self.assertTrue(editor._source_labels['date'].isHidden())

    def test_compilations_and_unverified_album_never_set_the_year(self):
        self.assertEqual(
            '',
            first_release_date(
                {
                    'releases': [
                        release('hits', 'Greatest Hits', '1990', secondary=['Compilation']),
                    ]
                }
            ),
        )
        self.assertEqual(
            '2001-05',
            first_release_date(
                {
                    'releases': [
                        release('hits', 'Greatest Hits', '1990', secondary=['Compilation']),
                        release('later', 'Album', '2003'),
                        release('first', 'Single', '2001-05', primary='Single'),
                    ]
                }
            ),
        )
        self.scan_with(releases=(release('hits', 'Greatest Hits', '1990', secondary=['Compilation']),))
        self.assertEqual('', self.file.metadata['date'])
        # An unverified album suggestion with its own date changes nothing.
        self.browse([release('lp', 'The Album', '1980')])
        self.assertTrue(self.file.dlmtags_album_suggestions)
        self.assertEqual('', self.file.metadata['date'])

    def test_verified_compilation_release_does_not_replace_the_first_release_year(self):
        self.scan_with('2021-04-09')
        # The album pipeline copies a verified (compilation) release's metadata.
        self.file.copy_metadata(
            Metadata(
                title='Song',
                musicbrainz_recordingid='rec',
                date='2030-01-01',
                album='Greatest Hits',
            )
        )
        self.assertEqual(['2021-04-09'], self.file.metadata.getall('date'))

    def test_loaded_release_supplies_the_recording_first_release_when_unknown(self):
        self.scan_with(releases=())
        self.file.copy_metadata(
            Metadata(
                title='Song',
                musicbrainz_recordingid='rec',
                date='2030',
                **{'~recording_firstreleasedate': '2019-02-01'},
            )
        )
        self.assertEqual(['2019-02-01'], self.file.metadata.getall('date'))
        self.assertEqual('MusicBrainz', source_for(self.file, 'date'))

    def test_existing_date_is_kept_and_a_different_year_is_only_suggested(self):
        self.file.metadata['date'] = '2015'
        self.scan_with('2021-04-09')
        self.assertEqual(['2015'], self.file.metadata.getall('date'))
        self.assertEqual(
            ('2021-04-09', 'MusicBrainz', 'https://musicbrainz.org/recording/rec'), self.file.dlmtags_date_suggestion
        )
        editor = self.editor()
        self.assertEqual('2015', editor._rows['date'][2].text())
        label = editor._source_labels['date']
        self.assertEqual('Suggested 2021', label.text())
        self.assertIn('MusicBrainz, first release of the recording (2021-04-09)', label.toolTip())

    def test_same_year_is_not_suggested(self):
        self.file.metadata['date'] = '2021'
        self.scan_with('2021-04-09')
        self.assertEqual(['2021'], self.file.metadata.getall('date'))
        self.assertIsNone(self.file.dlmtags_date_suggestion)

    def test_manual_year_survives_later_copies(self):
        self.scan_with('2021-04-09')
        self.file.metadata['date'] = '2000'
        self.file.copy_metadata(Metadata(title='Song', musicbrainz_recordingid='rec', date='2030'))
        self.assertEqual(['2000'], self.file.metadata.getall('date'))

    def test_original_tags_show_the_year_with_full_date_tooltip(self):
        self.file.orig_metadata['date'] = '2019-03-02'
        editor = self.editor()
        original = editor._rows['date'][0]
        self.assertEqual('2019', original._full_text)
        self.assertEqual('2019-03-02', original.toolTip())

    def test_unparseable_date_is_shown_as_stored(self):
        self.file.metadata['date'] = 'circa 1970'
        self.assertEqual('circa 1970', self.editor()._rows['date'][2].text())

    def test_reverting_to_the_file_drops_the_year_plan(self):
        self.scan_with('2021-04-09')
        self.file.copy_metadata(self.file.orig_metadata)
        self.assertEqual('', self.file.metadata['date'])
        self.assertIsNone(self.file._smart_scan_date)
