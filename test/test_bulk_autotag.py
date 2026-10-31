"""Bulk Smart Scan must never treat a score as identification proof."""

from types import SimpleNamespace
import unittest
from unittest.mock import (
    Mock,
    patch,
)

from picard.bulk_autotag import (
    Proposal,
    apply,
    classify,
    propose,
)
from picard.file import File
from picard.matching import SimMatchTrack
from picard.metadata import Metadata
from picard.smartscan import (
    Decision,
    Outcome,
    Reason,
)


class BulkAutoTagTest(unittest.TestCase):
    def decision(self, score=1.0, outcome=Outcome.MATCHED):
        match = SimMatchTrack(
            score,
            None,
            None,
            {
                'id': 'recording-id',
                'title': 'Verified title',
            },
        )
        return Decision(outcome, match=match, reasons=(Reason.CLOSE_SCORES,) if outcome == Outcome.NEEDS_REVIEW else ())

    def test_smart_scan_acceptance_is_authoritative(self):
        self.assertEqual(classify(self.decision(0.80))[0], 'matched')
        self.assertEqual(classify(self.decision(0.999))[0], 'matched')

    def test_ambiguity_wins_over_score(self):
        self.assertEqual(classify(self.decision(1.0, Outcome.NEEDS_REVIEW))[0], 'ambiguous')

    def test_missing_replacement_preserves_tags(self):
        original = Metadata()
        original['title'] = 'Old title'
        original['album'] = 'My album'
        original['comment'] = 'Keep me'
        original['genre'] = 'Keep genre'
        original['date'] = '1999'
        original['tracknumber'] = '7'

        def fill(_track, metadata):
            metadata['title'] = 'Verified title'
            metadata['artist'] = 'Verified artist'
            metadata['album'] = 'Wrong album'
            metadata['genre'] = 'Wrong genre'
            metadata['date'] = '2025'
            metadata['tracknumber'] = '1'

        with patch('picard.bulk_autotag.recording_to_metadata', side_effect=fill):
            proposal = propose(original, self.decision())
        self.assertEqual(proposal.changes['title'], (['Old title'], ['Verified title']))
        self.assertEqual(proposal.changes['artist'], ([], ['Verified artist']))
        self.assertNotIn('album', proposal.changes)
        self.assertNotIn('comment', proposal.changes)
        self.assertNotIn('genre', proposal.changes)
        self.assertNotIn('date', proposal.changes)
        self.assertNotIn('tracknumber', proposal.changes)

    def test_recording_first_release_wins_over_verified_reissue(self):
        original = Metadata()
        original['date'] = '1999'
        decision = self.decision()
        decision.release = {'id': 'release-id', 'date': '2001-05-02'}
        decision.release_verified = True
        decision.match.track['first-release-date'] = '1980-06-01'
        with patch('picard.bulk_autotag.recording_to_metadata'):
            proposal = propose(original, decision)
        self.assertEqual(proposal.changes['date'], (['1999'], ['1980-06-01']))

    def test_missing_year_uses_original_recording_year(self):
        original = Metadata()
        decision = self.decision()
        decision.match.track['first-release-date'] = '1980-06-01'
        decision.release = {'id': 'reissue', 'date': '2020'}
        decision.release_verified = True
        with patch('picard.bulk_autotag.recording_to_metadata'):
            proposal = propose(original, decision)
        self.assertEqual(proposal.changes['date'], ([], ['1980-06-01']))

    def test_compilation_without_recording_first_date_does_not_supply_year(self):
        original = Metadata()
        decision = self.decision()
        decision.release = {'id': 'hits', 'date': '2020', 'release-group': {'secondary-types': ['Compilation']}}
        decision.release_verified = True
        with patch('picard.bulk_autotag.recording_to_metadata'):
            proposal = propose(original, decision)
        self.assertNotIn('date', proposal.changes)

    def test_missing_year_and_genre_keep_existing_values(self):
        original = Metadata()
        original['date'] = '1999'
        original['genre'] = 'Jazz'
        with patch('picard.bulk_autotag.recording_to_metadata'):
            proposal = propose(original, self.decision())
        self.assertNotIn('date', proposal.changes)
        self.assertNotIn('genre', proposal.changes)

    def test_existing_genre_is_preserved_even_with_lookup_result(self):
        original = Metadata()
        original['genre'] = 'Jazz'
        decision = self.decision()
        decision.release = {'id': 'release-id'}
        decision.release_verified = True

        with patch('picard.bulk_autotag.recording_to_metadata'):
            proposal = propose(original, decision, genre_values=['Rock'])
        self.assertNotIn('genre', proposal.changes)

    def test_missing_genre_uses_filtered_provider_result(self):
        original = Metadata()
        with patch('picard.bulk_autotag.recording_to_metadata'):
            proposal = propose(original, self.decision(), genre_values=['Rock'])
        self.assertEqual(proposal.changes['genre'], ([], ['Rock']))

    def test_empty_provider_result_does_not_replace_genre(self):
        original = Metadata()
        with patch('picard.bulk_autotag.recording_to_metadata'):
            proposal = propose(original, self.decision(), genre_values=[])
        self.assertNotIn('genre', proposal.changes)

    def test_stale_preview_does_not_overwrite(self):
        original = Metadata()
        original['title'] = 'Before'
        current = Metadata(original)
        current['title'] = 'User edit'
        file = SimpleNamespace(metadata=current, copy_metadata=Mock(), update=Mock())
        proposal = Proposal({'title': (['Before'], ['Proposed'])}, 1.0)
        self.assertFalse(apply(file, proposal, original))
        file.copy_metadata.assert_not_called()

    def test_apply_changes_only_reviewed_fields(self):
        original = Metadata()
        original['title'] = 'Before'
        original['album'] = 'Keep album'
        original['comment'] = 'Keep comment'
        original['genre'] = 'Keep genre'
        original['date'] = '1999'
        original['tracknumber'] = '7'
        file = SimpleNamespace(metadata=Metadata(original), update=Mock())
        proposal = Proposal({'title': (['Before'], ['Proposed'])}, 1.0)
        self.assertTrue(apply(file, proposal, original))
        self.assertEqual(file.metadata['title'], 'Proposed')
        self.assertEqual(file.metadata['album'], 'Keep album')
        self.assertEqual(file.metadata['comment'], 'Keep comment')
        self.assertEqual(file.metadata['genre'], 'Keep genre')
        self.assertEqual(file.metadata['date'], '1999')
        self.assertEqual(file.metadata['tracknumber'], '7')
        file.update.assert_called_once_with()

    def test_apply_rejects_unrelated_tag_injected_into_proposal(self):
        original = Metadata(title='Before', album='Keep album')
        file = SimpleNamespace(metadata=Metadata(original), update=Mock())
        proposal = Proposal(
            {'title': (['Before'], ['After']), 'album': (['Keep album'], ['Unexpected album'])},
            1.0,
        )
        self.assertFalse(apply(file, proposal, original))
        self.assertEqual(file.metadata['title'], 'Before')
        self.assertEqual(file.metadata['album'], 'Keep album')
        file.update.assert_not_called()

    def test_ambiguous_result_stages_nothing(self):
        original = Metadata()
        original['title'] = 'Original title'
        original['artist'] = 'Original artist'
        file = SimpleNamespace(metadata=Metadata(original), update=Mock())
        decision = self.decision(1.0, Outcome.NEEDS_REVIEW)
        status, _ = classify(decision)
        if status == 'matched':
            apply(file, propose(original, decision), original)
        self.assertEqual(file.metadata['title'], 'Original title')
        self.assertEqual(file.metadata['artist'], 'Original artist')
        file.update.assert_not_called()

    def test_cancel_scan_reports_cancellation_and_clears_callback(self):
        callback = Mock()
        file = SimpleNamespace()
        file._smart_scan_token = object()
        file._bulk_scan_callback = callback
        file._lookup_task = object()
        file.tagger = SimpleNamespace(
            _acoustid=SimpleNamespace(stop_analyze=Mock()),
            webservice=SimpleNamespace(abort_task=Mock()),
        )
        file.clear_pending = Mock()
        File.cancel_smart_scan(file)
        self.assertEqual(callback.call_args.args[1].outcome, Outcome.CANCELLED)
        self.assertIsNone(file._bulk_scan_callback)
        self.assertIsNone(file._lookup_task)
        file.tagger.webservice.abort_task.assert_called_once()

    def test_deferred_match_does_not_change_metadata(self):
        callback = Mock()
        file = SimpleNamespace(
            _smart_scan_token=object(),
            smart_scan_stage='scan',
            _smart_scan_acoustid_decision=None,
            _bulk_scan_callback=callback,
            smart_scan_decision='previous result',
            metadata=Metadata(),
            clear_pending=Mock(),
            _apply_smart_scan_match=Mock(),
        )
        decision = self.decision()
        File._finish_smart_scan(file, decision)
        callback.assert_called_once_with(file, decision)
        file._apply_smart_scan_match.assert_not_called()
        self.assertEqual(dict(file.metadata), {})
        self.assertEqual(file.smart_scan_decision, 'previous result')

    def test_ambiguous_scan_is_available_to_manual_review_without_tag_changes(self):
        original = Metadata()
        original['title'] = 'Keep title'
        original['artist'] = 'Keep artist'
        callback = Mock()
        file = SimpleNamespace(
            _smart_scan_token=object(),
            smart_scan_stage='scan',
            _smart_scan_acoustid_decision=None,
            _bulk_scan_callback=callback,
            smart_scan_decision=None,
            metadata=Metadata(original),
            orig_metadata=Metadata(original),
            _manually_edited_tags=set(),
            clear_pending=Mock(),
            cancel_credit_lookup=Mock(),
            _set_tag_suggestions=Mock(return_value=None),
            _start_credit_lookup=Mock(),
        )
        decision = self.decision(0.91, Outcome.NEEDS_REVIEW)
        File._finish_smart_scan(file, decision)
        self.assertIs(file.smart_scan_decision, decision)
        self.assertEqual(file.metadata, original)
        self.assertEqual(file._smart_scan_expected, original)
        callback.assert_called_once_with(file, decision)


if __name__ == '__main__':
    unittest.main()
