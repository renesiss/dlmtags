# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""Recording-first policy using synthetic MusicBrainz nodes and mocked services."""

from unittest.mock import (
    MagicMock,
    patch,
)

from PyQt6 import QtGui

from test import (
    test_smartgenre as genre_tests,
    test_smartscan as scan_tests,
)
from test.picardtestcase import (
    PicardTestCase,
    create_image,
    subtest_cases,
)
from test.test_smartscan import (
    BASE_SETTINGS,
    acoustid_recording,
    file_metadata,
    recording,
    release,
)

from picard.file import (
    FILE_COMPARISON_WEIGHTS,
    File,
)
from picard.metadata import Metadata
from picard.util import tracknum_and_title_from_filename
from picard.smartscan import (
    Outcome,
    Reason,
    Source,
    decide,
    file_hints,
    split_title,
)

from picard.ui.dlmtags.workspace import MatchingResults


def smile_releases():
    return [
        release('mayhem', 'MAYHEM', '2025-03-07', status='Official'),
        release('single', 'Die With a Smile', '2024-08-16', primary='Single', status='Official', tracks=1),
    ]


class RecordingFirstTest(scan_tests.SmartScanPolicyTestCase):
    @subtest_cases('filename,title,artists', [
        ('002 - Rag_n_Bone_Man_P_nk_Anywhere_Away_From_Here_Official_Video - P_NK [IiuE9TzMfxs].mp4',
         'Anywhere Away From Here', ("Rag’n’Bone Man", 'P!nk')),
        ('Lady_Gaga_Bruno_Mars_Die_With_a_Smile_Official_Music_Video - Lady Gaga [AbcdEFgh123].mp4',
         'Die With a Smile', ('Lady Gaga', 'Bruno Mars')),
        ('David_Guetta_Alok_Stick_Figure_Run_Run_River_(Angels_Above_Me)_Official_Lyric_Video.mp4',
         'Run Run River (Angels Above Me)', ('David Guetta', 'Alok', 'Stick Figure')),
    ])
    def test_collaboration_filename_uses_returned_credit_context(self, filename, title, artists):
        node = acoustid_recording('rec', title, artists, 232000, 0.95)
        decision = self.decide(Source.ACOUSTID, [node], file_metadata(), filename=filename)
        self.assertEqual(Outcome.MATCHED, decision.outcome)
        self.assertNotIn(Reason.ARTIST_CONFLICT, decision.candidates[0].reasons)
        self.assertNotIn(Reason.TITLE_CONFLICT, decision.candidates[0].reasons)
        guessed = file_metadata(title=tracknum_and_title_from_filename(filename).title)
        decision = self.decide(Source.ACOUSTID, [node], guessed, filename=filename)
        self.assertEqual(Outcome.MATCHED, decision.outcome)

    def test_channel_suffix_and_video_id_do_not_become_song_title(self):
        hints = file_hints(file_metadata(), 'Rag_n_Bone_Man - Anywhere_Away_From_Here_Official_Video - P_NK [IiuE9TzMfxs].mp4')
        self.assertEqual('Anywhere Away From Here', hints.title)

    @subtest_cases('filename,title', [
        ('Artist - Song [Ab_dEF-h123].mp4', 'Song'),
        ('Artist - Song - Artist [IiuE9TzMfxs].mp4', 'Song'),
        ('Artist - Song (Acoustic) [IiuE9TzMfxs].mp4', 'Song (Acoustic)'),
    ])
    def test_video_id_and_verified_repeated_uploader_are_not_title(self, filename, title):
        self.assertEqual(title, file_hints(file_metadata(), filename).title)

    def test_candidate_context_does_not_invent_a_pink_alias(self):
        node = acoustid_recording('rec', 'Song', 'P!nk', 232000, 0.95)
        # Force a meaningful caption; Pink is not interchangeable with P!nk
        # unless that spelling is supplied by the artist's MusicBrainz aliases.
        filename = 'Pink_Some_Long_Song_Official_Video.mp4'
        node['title'] = 'Some Long Song'
        decision = self.decide(Source.ACOUSTID, [node], file_metadata(), filename=filename)
        self.assertEqual(Outcome.NEEDS_REVIEW, decision.outcome)
        node['artist-credit'][0]['artist']['aliases'] = [{'name': 'Pink'}]
        decision = self.decide(Source.ACOUSTID, [node], file_metadata(), filename=filename)
        self.assertEqual(Outcome.MATCHED, decision.outcome)

    @subtest_cases('version', ['Live', 'Acoustic', 'Remix', 'Instrumental'])
    def test_flattened_caption_preserves_genuine_versions(self, version):
        filename = 'Rag_n_Bone_Man_P_nk_Anywhere_Away_From_Here_(' + version + ')_Official_Video.mp4'
        node = acoustid_recording('rec', 'Anywhere Away From Here', ("Rag’n’Bone Man", 'P!nk'), 232000, 0.95)
        decision = self.decide(Source.ACOUSTID, [node], file_metadata(), filename=filename)
        self.assertEqual(Outcome.NEEDS_REVIEW, decision.outcome)
        self.assertIn(Reason.VERSION_MISMATCH, decision.reasons)

    def test_unrelated_artist_and_ambiguous_recordings_remain_reviewable(self):
        filename = 'Rag_n_Bone_Man_P_nk_Anywhere_Away_From_Here_Official_Video.mp4'
        wrong = acoustid_recording('wrong', 'Anywhere Away From Here', 'Other Artist', 232000, 0.95)
        decision = self.decide(Source.ACOUSTID, [wrong], file_metadata(), filename=filename)
        self.assertEqual(Outcome.NEEDS_REVIEW, decision.outcome)
        self.assertIn(Reason.ARTIST_CONFLICT, decision.reasons)
        original = acoustid_recording('a', 'Anywhere Away From Here', ("Rag’n’Bone Man", 'P!nk'), 232000, 0.95)
        other = dict(original, id='b')
        decision = self.decide(Source.ACOUSTID, [original, other], file_metadata(), filename=filename)
        self.assertEqual(Outcome.NEEDS_REVIEW, decision.outcome)
        self.assertIn(Reason.CLOSE_SCORES, decision.reasons)
        weak = dict(original, acoustid_score=0.2)
        decision = self.decide(Source.ACOUSTID, [weak], file_metadata(), filename=filename)
        self.assertEqual(Outcome.NO_MATCH, decision.outcome)
        self.assertIn(Reason.LOW_FINGERPRINT_SCORE, decision.reasons)

    def smile(self, releases=None, **tags):
        node = acoustid_recording(
            'smile', 'Die With a Smile', ('Lady Gaga', 'Bruno Mars'), 251000, 0.95,
            smile_releases() if releases is None else releases,
        )
        metadata = file_metadata(
            length=251000, title='Die With a Smile', artist='Lady Gaga & Bruno Mars', **tags,
        )
        return self.decide(Source.ACOUSTID, [node], metadata)

    def test_original_official_single_before_later_album(self):
        decision = self.smile()
        self.assertEqual(Outcome.MATCHED, decision.outcome)
        self.assertEqual('smile', decision.recording_id)
        self.assertEqual('single', decision.release_id)
        self.assertTrue(decision.release_verified)

    def test_credible_existing_album_is_preserved(self):
        decision = self.smile(album='MAYHEM', date='2025', tracknumber='14')
        self.assertEqual('mayhem', decision.release_id)
        self.assertTrue(decision.release_verified)

    def test_album_conflict_does_not_reject_confirmed_recording(self):
        decision = self.smile(album='My Old Album')
        self.assertEqual(Outcome.MATCHED, decision.outcome)
        self.assertFalse(decision.release_verified)
        self.assertIn(Reason.RELEASE_UNVERIFIED, decision.notes)

    def test_release_preferences_cannot_change_recording(self):
        self.set_config_values({'preferred_release_types': ['Album'], 'discouraged_release_types': ['Single']})
        decision = self.smile()
        self.assertEqual('smile', decision.recording_id)
        self.assertEqual('mayhem', decision.release_id)

    def test_same_recording_multiple_entries_retains_all_releases(self):
        releases = smile_releases()
        nodes = [
            acoustid_recording('smile', 'Die With a Smile', 'Lady Gaga', 251000, 0.95, [r])
            for r in releases
        ]
        decision = self.decide(
            Source.ACOUSTID, nodes, file_metadata(251000, title='Die With a Smile', artist='Lady Gaga'),
        )
        self.assertEqual(Outcome.MATCHED, decision.outcome)
        self.assertEqual(1, len(decision.candidates))
        self.assertEqual({'mayhem', 'single'}, {r.release_id for r in decision.candidates[0].releases})

    @subtest_cases('version', ['Acoustic', 'Instrumental', 'Live', 'Remix'])
    def test_original_and_musical_versions_stay_distinct(self, version):
        tracks = [
            acoustid_recording('other', 'Song (' + version + ')', 'Artist', 232000, 0.98),
            acoustid_recording('original', 'Song', 'Artist', 232000, 0.95),
        ]
        decision = self.decide(Source.ACOUSTID, tracks, file_metadata(title='Song', artist='Artist'))
        self.assertEqual(Outcome.MATCHED, decision.outcome)
        self.assertEqual('original', decision.recording_id)
        self.assertEqual('original', decision.candidates[0].recording_id)

    @subtest_cases('label', ['Official Music Video', 'Official Video', 'Lyric Video', 'HD', '4K'])
    def test_filename_source_labels_are_not_versions(self, label):
        hints = file_hints(file_metadata(), 'Artist - Song (' + label + ').mp4')
        self.assertEqual('Song', hints.title)
        self.assertEqual(frozenset(), split_title(hints.title).versions)
        decision = self.decide(
            Source.ACOUSTID, [acoustid_recording('rec', 'Song', 'Artist', 232000, 0.95)],
            file_metadata(), filename='Artist - Song (' + label + ').mp4',
        )
        self.assertEqual(Outcome.MATCHED, decision.outcome)

    @subtest_cases('filename,title', [
        ('Artist - Song - Official Video.mp4', 'Song'),
        ('Artist - Song [Official Lyric Video] HD.mp4', 'Song'),
        ('Artist - Song - Acoustic.mp3', 'Song - Acoustic'),
        ('Artist - HD.mp3', 'HD'),
        ('Artist - Video Games.mp3', 'Video Games'),
    ])
    def test_trailing_source_labels_preserve_actual_titles(self, filename, title):
        self.assertEqual(title, file_hints(file_metadata(), filename).title)

    def test_source_label_does_not_remove_acoustic_evidence(self):
        hints = file_hints(file_metadata(), 'Artist - Song (Official Video Acoustic 4K).mp4')
        self.assertEqual(frozenset({'acoustic'}), split_title(hints.title).versions)
        decision = self.decide(
            Source.ACOUSTID, [acoustid_recording('rec', 'Song', 'Artist', 232000, 0.95)],
            file_metadata(), filename='Artist - Song (Official Video Acoustic 4K).mp4',
        )
        self.assertEqual(Outcome.NEEDS_REVIEW, decision.outcome)

    def test_source_label_does_not_override_duration(self):
        decision = self.decide(
            Source.ACOUSTID, [acoustid_recording('rec', 'Song', 'Artist', 180000, 0.95)],
            file_metadata(232000), filename='Artist - Song (Official Video).mp4',
        )
        self.assertEqual(Outcome.NEEDS_REVIEW, decision.outcome)
        self.assertIn(Reason.LENGTH_MISMATCH, decision.reasons)

    def test_release_cannot_break_recording_ambiguity(self):
        tracks = [
            recording('a', 'Song', 'Artist', 232000, releases=smile_releases()),
            recording('b', 'Song', 'Artist', 232000),
        ]
        decision = self.decide(Source.METADATA, tracks, file_metadata(title='Song', artist='Artist'))
        self.assertEqual(Outcome.NEEDS_REVIEW, decision.outcome)
        self.assertIn(Reason.CLOSE_SCORES, decision.reasons)

    def test_recording_preference_is_independent_of_release_preferences(self):
        nodes = [
            acoustid_recording('correct', 'Song', 'Artist', 232000, 0.95),
            acoustid_recording('other', 'Song', 'Artist', 232000, 0.8, smile_releases()),
        ]
        for preferred in (['Single'], ['Album']):
            self.set_config_values({'preferred_release_types': preferred})
            decision = self.decide(Source.ACOUSTID, nodes, file_metadata(title='Song', artist='Artist'))
            self.assertEqual(Outcome.MATCHED, decision.outcome)
            self.assertEqual('correct', decision.recording_id)

    def test_identical_recordings_with_different_ids_need_review(self):
        nodes = [
            acoustid_recording(rid, 'Song', 'Artist', 232000, 0.95, releases)
            for rid, releases in (('a', smile_releases()), ('b', []))
        ]
        decision = self.decide(Source.ACOUSTID, nodes, file_metadata(title='Song', artist='Artist'))
        self.assertEqual(Outcome.NEEDS_REVIEW, decision.outcome)
        self.assertIn(Reason.CLOSE_SCORES, decision.reasons)

    def test_recording_identifier_breaks_textual_tie(self):
        nodes = [
            recording(rid, 'Song', 'Artist', 232000) for rid in ('a', 'b')
        ]
        decision = self.decide(
            Source.METADATA, nodes,
            file_metadata(title='Song', artist='Artist', musicbrainz_recordingid='b'),
        )
        self.assertEqual(Outcome.MATCHED, decision.outcome)
        self.assertEqual('b', decision.recording_id)

    def test_partial_dates_do_not_claim_an_original_single(self):
        releases = smile_releases()
        releases[1]['date'] = '2025'
        decision = self.smile(releases)
        self.assertEqual(Outcome.MATCHED, decision.outcome)
        self.assertFalse(decision.release_verified)

    def test_unknown_album_date_cannot_establish_original_single(self):
        releases = smile_releases()
        releases[0]['date'] = ''
        decision = self.smile(releases)
        self.assertEqual(Outcome.MATCHED, decision.outcome)
        self.assertFalse(decision.release_verified)

    def test_two_equivalent_single_editions_remain_unverified(self):
        releases = smile_releases()
        releases.append(dict(releases[1], id='single-other'))
        decision = self.smile(releases)
        self.assertFalse(decision.release_verified)
        self.assertEqual(Outcome.MATCHED, decision.outcome)

    def test_unmatched_existing_album_identifier_is_not_replaced(self):
        decision = self.smile(musicbrainz_albumid='existing-not-in-results')
        self.assertEqual(Outcome.MATCHED, decision.outcome)
        self.assertFalse(decision.release_verified)

    def test_compilation_alone_is_unverified(self):
        decision = self.smile([
            release('comp', 'Greatest Hits', '2024', secondary=['Compilation'], status='Official'),
        ])
        self.assertEqual(Outcome.MATCHED, decision.outcome)
        self.assertFalse(decision.release_verified)


class RecordingOnlyWorkflowTest(PicardTestCase):
    def setUp(self):
        super().setUp()
        self.set_config_values(
            dict(
                BASE_SETTINGS,
                use_genres=True,
                min_genre_usage=50,
                max_genres=5,
                genres_filter='',
                join_genres='',
                folksonomy_tags=False,
                only_my_genres=False,
                smart_genre_lastfm_enabled=False,
                lastfm_api_key='',
            )
        )
        self.patch_tagger_instance('picard.item')
        self.tagger.window = MagicMock()
        self.tagger.acoustidmanager = MagicMock()
        self.tagger.isrc_submit_manager = MagicMock()
        self.tagger._acoustid = MagicMock()
        self.tagger.mb_api = MagicMock()
        self.tagger.mb_api.get_track_by_id.return_value = object()
        self.tagger.mb_api.get_release_by_id.return_value = object()
        self.tagger.webservice = MagicMock()
        self.tagger.webservice.get_url.return_value = object()
        self.tagger.albums = {}
        self.file = File('/music/Artist - Song.mp3')
        self.file.state = File.State.NORMAL
        self.file.orig_metadata = file_metadata(title='Song', artist='Artist')
        self.file.metadata = Metadata(self.file.orig_metadata)

    def tearDown(self):
        self.file.cancel_smart_genre()
        super().tearDown()

    match = genre_tests.SmartGenreTest.match
    respond = genre_tests.SmartGenreTest.respond

    def test_recording_without_any_release_still_loads_metadata_and_genre(self):
        self.file.start_smart_scan()
        node = acoustid_recording('rec', 'Song', 'Artist', 232000, 0.95)
        self.tagger._acoustid.analyze.call_args.args[1]({'recordings': [node]}, None, None)
        decision = self.file.smart_scan_decision
        self.assertEqual(Outcome.MATCHED, decision.outcome)
        self.assertIsNone(decision.release)
        self.assertFalse(decision.release_verified)
        self.tagger.move_file_to_nat.assert_called_once_with(self.file, 'rec')
        self.file.copy_metadata(Metadata(title='Confirmed Song', artist='Artist', musicbrainz_recordingid='rec'))
        self.assertEqual('Confirmed Song', self.file.metadata['title'])
        self.assertEqual('', self.file.metadata['album'])
        callback = self.tagger.mb_api.get_track_by_id.call_args.args[1]
        self.respond(callback, [{'name': 'rock', 'count': 10}])
        self.assertEqual('Rock', self.file.metadata['genre'])

    def test_unverified_release_preserves_release_fields_and_updates_recording(self):
        self.file.metadata['album'] = 'My Album'
        self.file.metadata['date'] = '1998'
        self.file.metadata['tracknumber'] = '7'
        callback = self.match()
        self.assertFalse(self.file.smart_scan_decision.release_verified)
        self.tagger.move_file_to_track.assert_not_called()
        self.tagger.move_file_to_nat.assert_called_once_with(self.file, 'rec')
        self.file.copy_metadata(Metadata(
            title='Confirmed Recording', artist='Artist', musicbrainz_recordingid='rec',
            album='[non-album tracks]', date='2099', tracknumber='99',
        ))
        self.assertEqual('Confirmed Recording', self.file.metadata['title'])
        self.assertEqual('rec', self.file.metadata['musicbrainz_recordingid'])
        self.assertEqual('My Album', self.file.metadata['album'])
        self.assertEqual('1998', self.file.metadata['date'])
        self.assertEqual('7', self.file.metadata['tracknumber'])
        self.respond(callback, [{'name': 'rock', 'count': 10}])
        self.assertEqual('Rock', self.file.metadata['genre'])
        self.assertEqual('MusicBrainz recording', self.file.smart_genre_source)

    def test_artwork_and_protected_edits_survive_recording_only_reparenting(self):
        self.file.metadata['bpm'] = '128'
        self.file.metadata.images.append(create_image(b'own', types=['front']))
        # Picard removes a file from its old Track before attaching the new one.
        self.tagger.move_file_to_nat.side_effect = lambda file, rid: file.copy_metadata(file.orig_metadata)
        self.match()
        self.assertEqual('128', self.file.metadata['bpm'])
        self.assertEqual(1, len(self.file.metadata.images))
        replacement = create_image(b'manually-edited', types=['front'])
        self.file.metadata.images.clear()
        self.file.metadata.images.append(replacement)
        self.file.copy_metadata(Metadata(title='Song', musicbrainz_recordingid='rec'))
        self.assertEqual([replacement], list(self.file.metadata.images))

    def test_fallback_query_is_not_restricted_to_stale_release_fields(self):
        self.file.metadata['album'] = 'Stale Album'
        self.file.metadata['tracknumber'] = '99'
        self.file.start_smart_scan()
        self.tagger._acoustid.analyze.call_args.args[1]({'recordings': []}, None, None)
        query = self.tagger.mb_api.find_tracks.call_args.kwargs
        self.assertEqual('', query['release'])
        self.assertEqual('', query['tnum'])
        self.assertEqual('Song', query['track'])
        self.assertEqual('Artist', query['artist'])

    def test_empty_release_fields_stay_empty_and_manual_edits_survive(self):
        self.match()
        self.file.copy_metadata(Metadata(
            title='Song', musicbrainz_recordingid='rec', album='Unknown Album', date='2099', tracknumber='5',
        ))
        for key in ('album', 'tracknumber'):
            self.assertEqual('', self.file.metadata[key])
        # Year is the recording's first release, not the unverified release's 2099.
        self.assertEqual('1999', self.file.metadata['date'])
        self.file.metadata['album'] = 'User Album'
        self.file.metadata['tracknumber'] = '2'
        self.file.copy_metadata(Metadata(title='Song', musicbrainz_recordingid='rec', album='Wrong'))
        self.assertEqual('User Album', self.file.metadata['album'])
        self.assertEqual('2', self.file.metadata['tracknumber'])

    def test_compact_results_retain_releases_without_showing_them_or_opening_browser(self):
        tracks = [acoustid_recording('rec', 'Song', 'Artist', 232000, 0.95, smile_releases())]
        self.file.smart_scan_decision = decide(
            Source.ACOUSTID, {'recordings': tracks}, None, self.file.metadata, '', FILE_COMPARISON_WEIGHTS,
        )
        results = MagicMock()
        model = QtGui.QStandardItemModel()
        results.table.model.return_value = model
        with patch('picard.util.webbrowser2.open') as browser:
            MatchingResults._show_candidates(results, self.file.smart_scan_decision)
            lines = MatchingResults._describe(results, self.file)
        self.assertEqual(0, model.rowCount())
        self.assertIn('Artist — Song', lines)
        self.assertEqual(2, len(self.file.smart_scan_decision.candidates[0].releases))
        results.table.hide.assert_called()
        browser.assert_not_called()
