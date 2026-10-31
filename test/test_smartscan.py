# Picard, the next-generation MusicBrainz tagger
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <https://www.gnu.org/licenses/>.

"""Smart Scan Phase 2: matching decisions and metadata protection.

All candidates are synthetic; no AcoustID or MusicBrainz access is needed.
"""

from types import SimpleNamespace
from typing import ClassVar
import unittest
from unittest.mock import (
    MagicMock,
    patch,
)

from PyQt6 import QtGui
from PyQt6.QtNetwork import QNetworkReply

from test.picardtestcase import (
    PicardTestCase,
    create_image,
    subtest_cases,
)

from picard import smartscan
from picard.acoustid import (
    AcoustIDClient,
    AcoustIDTask,
    annotate_fingerprint_scores,
)
from picard.acoustid.json_helpers import parse_recording
from picard.acoustid.recordings import RecordingResolver
from picard.file import (
    FILE_COMPARISON_WEIGHTS,
    File,
)
from picard.metadata import Metadata
from picard.smartscan import (
    Evidence,
    Outcome,
    Reason,
    SmartScanThresholds,
    Source,
    artist_evidence,
    combine,
    decide,
    file_hints,
    is_protected_tag,
    split_artist_names,
    split_title,
)
from picard.tagprovenance import source_for
from picard.track import Track
from picard.util import tracknum_and_title_from_filename
from picard.util.imagelist import ImageList

from picard.ui.dlmtags.artwork import recording_for_artwork
from picard.ui.dlmtags.workspace import MatchingResults


SECOND = 1000

BASE_SETTINGS = {
    'preferred_release_countries': [],
    'preferred_release_formats': [],
    'preferred_release_types': [],
    'discouraged_release_types': [],
    'standardize_artist_names': False,
    'translate_artist_names': False,
    'va_name': 'Various Artists',
    'match_min_margin': 0.02,
    'match_min_similarity': 0.25,
    'query_limit': 50,
    'clear_existing_tags': False,
    'preserved_tags': [],
    'compare_ignore_tags': [],
    'enable_tagger_scripts': False,
    'save_acoustid_fingerprints': True,
    'ignore_existing_acoustid_fingerprints': False,
    'guess_tracknumber_and_title': True,
    'dont_write_tags': False,
}


def release(rid, title, date='', primary='Album', secondary=None, country='XW', status=None, rgid=None, tracks=12):
    node = {
        'id': rid,
        'title': title,
        'date': date,
        'country': country,
        'release-group': {'id': rgid or 'rg-' + rid, 'primary-type': primary},
        'media': [{'format': 'CD', 'track-count': tracks}],
        'track-count': tracks,
    }
    if secondary:
        node['release-group']['secondary-types'] = list(secondary)
    if status:
        node['status'] = status
    return node


def credit(*names, joinphrase='; '):
    result = []
    for i, name in enumerate(names):
        node = {'name': name, 'artist': {'id': 'artist-' + name, 'name': name, 'sort-name': name}}
        if i < len(names) - 1:
            node['joinphrase'] = joinphrase
        result.append(node)
    return result


def recording(
    rid,
    title,
    artists,
    length,
    releases=(),
    score=100,
    acoustid=None,
    acoustid_score=None,
    isrcs=None,
    disambiguation=None,
):
    """A recording node as Picard receives it (MB search or parsed AcoustID)."""
    node = {
        'id': rid,
        'title': title,
        'length': length,
        'score': score,
        'artist-credit': credit(*artists) if isinstance(artists, tuple) else credit(artists),
        'releases': list(releases),
    }
    if acoustid:
        node['acoustid'] = acoustid
    if acoustid_score is not None:
        node['acoustid_score'] = acoustid_score
    if isrcs:
        node['isrcs'] = list(isrcs)
    if disambiguation:
        node['disambiguation'] = disambiguation
    return node


def acoustid_recording(rid, title, artists, length, fp_score, releases=(), sources_ratio=1.0, acoustid='aid-1'):
    """AcoustID candidate: Picard 'score' = sources ratio * result score * 100."""
    return recording(
        rid,
        title,
        artists,
        length,
        releases=releases,
        score=sources_ratio * fp_score * 100,
        acoustid=acoustid,
        acoustid_score=fp_score,
    )


def file_metadata(length=232 * SECOND, **tags):
    metadata = Metadata(**tags)
    metadata.length = length
    return metadata


BSB_RELEASES = (
    # Listed first on purpose: Picard keeps the first of equal scores.
    release('pop-party', 'Pop Party! Volume 1', '2000-10', secondary=['Compilation'], tracks=20),
    release('millennium', 'Millennium', '1999-05-18'),
    release('ltl-single', 'Larger Than Life', '1999-09-13', primary='Single', tracks=3),
)


class SmartScanPolicyTestCase(PicardTestCase):
    def setUp(self):
        super().setUp()
        self.set_config_values(BASE_SETTINGS)
        self.thresholds = SmartScanThresholds()

    def decide(self, source, tracks, metadata, filename='track.mp3', error=None, document=None):
        if document is None:
            document = {'recordings': tracks}
        return decide(
            source,
            document,
            error,
            metadata,
            filename,
            FILE_COMPARISON_WEIGHTS,
            thresholds=self.thresholds,
        )


class TextEvidenceTest(SmartScanPolicyTestCase):
    def test_split_title_versions(self):
        self.assertEqual(frozenset({'live'}), split_title('Fever (Live)').versions)
        self.assertEqual(
            frozenset({'remix', 'extended'}), split_title('Larger Than Life (extended video mix)').versions
        )
        self.assertEqual(frozenset({'edit'}), split_title('Song - Radio Edit').versions)
        self.assertEqual(frozenset(), split_title('Song (2011 Remaster)').versions)
        self.assertEqual(frozenset(), split_title('Song (Album Version)').versions)
        self.assertEqual(frozenset(), split_title('Song (Original Mix)').versions)
        # "Live" in the title proper is not a qualifier
        self.assertEqual(frozenset(), split_title('Live and Let Die').versions)
        self.assertEqual('Live and Let Die', split_title('Live and Let Die').base)

    def test_split_title_featured(self):
        parts = split_title('BAND4BAND (feat. Lil Baby)')
        self.assertEqual('BAND4BAND', parts.base)
        self.assertEqual(('lil baby',), parts.featured)
        self.assertEqual(frozenset(), parts.versions)

    @subtest_cases(
        'title,base',
        [
            ('Stay With Me', 'Stay With Me'),
            ('With or Without You', 'With or Without You'),
            ('Dancing with Myself', 'Dancing with Myself'),
            ('Die With a Smile', 'Die With a Smile'),
            ('Stay With Me (Live)', 'Stay With Me'),
            ('Die With a Smile (feat. Someone)', 'Die With a Smile'),
        ],
    )
    def test_split_title_keeps_with_in_title_proper(self, title, base):
        # "with" is an ordinary word in a title; only a qualifier credits a guest.
        self.assertEqual(base, split_title(title).base)

    def test_split_title_credits_are_still_recognized(self):
        parts = split_title('Something Just Like This (with Coldplay)')
        self.assertEqual(('Something Just Like This', ('coldplay',)), (parts.base, parts.featured))
        self.assertEqual('Song', split_title('Song feat. Other').base)
        self.assertEqual('Song', split_title('Song ft. Other').base)
        self.assertEqual('Song', split_title('Song featuring Other').base)
        parts = split_title('Song - feat. Other')
        self.assertEqual(('Song', ('other',)), (parts.base, parts.featured))
        parts = split_title('Stay With Me (with Mary J. Blige) [Live]')
        self.assertEqual(
            ('Stay With Me', ('mary j blige',), frozenset({'live'})), (parts.base, parts.featured, parts.versions)
        )

    def test_filename_title_starting_with_with_is_not_a_qualifier(self):
        self.assertEqual(
            ('U2', 'With or Without You'),
            smartscan.filename_artist_title('U2 - The Joshua Tree - With or Without You.mp3'),
        )
        self.assertEqual(('Artist', 'Song - Live'), smartscan.filename_artist_title('Artist - Song - Live.mp3'))

    def test_split_artist_names(self):
        self.assertEqual(['central cee', 'lil baby'], split_artist_names('Central Cee ft. Lil Baby'))
        self.assertEqual(['simon', 'garfunkel'], split_artist_names('Simon & Garfunkel'))
        self.assertEqual(['beyonce'], split_artist_names('Beyoncé'))

    def test_artist_collaboration_is_not_a_conflict(self):
        track = recording('r', 'BAND4BAND', ('Central Cee', 'Lil Baby'), 140 * SECOND)
        hints = file_hints(file_metadata(artist='Central Cee ft. Lil Baby', title='BAND4BAND'))
        self.assertEqual(Evidence.MATCH, artist_evidence(hints.artist_names, hints.artist, track))
        hints = file_hints(file_metadata(artist='Central Cee', title='BAND4BAND'))
        self.assertEqual(Evidence.MATCH, artist_evidence(hints.artist_names, hints.artist, track))
        # Featured artist tagged as the main artist
        hints = file_hints(file_metadata(artist='Lil Baby & Central Cee', title='BAND4BAND'))
        self.assertEqual(Evidence.MATCH, artist_evidence(hints.artist_names, hints.artist, track))
        hints = file_hints(file_metadata(artist='Somebody & Lil Baby', title='BAND4BAND'))
        self.assertEqual(Evidence.PARTIAL, artist_evidence(hints.artist_names, hints.artist, track))

    def test_artist_alias_and_sort_name(self):
        track = recording('r', 'Fever', 'Joe Cocker', 200 * SECOND)
        track['artist-credit'][0]['artist']['sort-name'] = 'Cocker, Joe'
        track['artist-credit'][0]['artist']['aliases'] = [{'name': 'J. Cocker'}]
        for name in ('Joe Cocker', 'Cocker, Joe', 'J. Cocker', 'joe cocker', 'The Joe Cocker'):
            hints = file_hints(file_metadata(artist=name, title='Fever'))
            self.assertEqual(Evidence.MATCH, artist_evidence(hints.artist_names, hints.artist, track), name)

    def test_artist_conflict(self):
        track = recording('r', 'Pretty Little Baby', 'Modest Clifton', 150 * SECOND)
        hints = file_hints(file_metadata(artist='Eloise', title='Pretty Little Baby'))
        self.assertEqual(Evidence.MISMATCH, artist_evidence(hints.artist_names, hints.artist, track))

    def test_filename_hints_only_when_tags_missing(self):
        # Title is Picard's filename guess, artist missing: use the filename
        hints = file_hints(file_metadata(title='Eloise - Pretty Little Baby'), 'Eloise - Pretty Little Baby.mp3')
        self.assertEqual(('Eloise', 'filename'), (hints.artist, hints.artist_source))
        self.assertEqual(('Pretty Little Baby', 'filename'), (hints.title, hints.title_source))
        # Real tags win over the filename
        hints = file_hints(file_metadata(artist='Real', title='Real Title'), 'Eloise - Pretty Little Baby.mp3')
        self.assertEqual(
            ('Real', 'tag', 'Real Title', 'tag'), (hints.artist, hints.artist_source, hints.title, hints.title_source)
        )
        # Track number prefix is stripped
        hints = file_hints(file_metadata(), '03 - Eloise - Pretty Little Baby.flac')
        self.assertEqual(('Eloise', 'Pretty Little Baby'), (hints.artist, hints.title))

    def test_various_artists_is_unknown(self):
        hints = file_hints(file_metadata(artist='Various Artists', title='x'))
        self.assertEqual((), hints.artist_names)

    def test_protected_tag_classification(self):
        for name in (
            'bpm',
            '~rating',
            'comment',
            'comment:iTunNORM',
            'genre',
            'isrc',
            'replaygain_track_gain',
            'mycustomtag',
        ):
            self.assertTrue(is_protected_tag(name), name)
        for name in ('title', 'album', 'date', 'tracknumber', 'musicbrainz_recordingid', 'albumartist', '~length'):
            self.assertFalse(is_protected_tag(name), name)

    def test_thresholds_from_config(self):
        self.set_config_values({'smart_scan_min_margin': 0.01, 'match_min_margin': 0.03})
        self.assertEqual(0.03, SmartScanThresholds.from_config().min_margin)
        self.set_config_values({'smart_scan_acoustid_min_score': 0.6})
        self.assertEqual(0.6, SmartScanThresholds.from_config().acoustid_min_score)


class AcoustIDDecisionTest(SmartScanPolicyTestCase):
    def test_1_high_confidence_exact_match(self):
        tracks = [
            acoustid_recording('rec-ltl', 'Larger Than Life', 'Backstreet Boys', 232 * SECOND, 0.95, BSB_RELEASES)
        ]
        metadata = file_metadata(title='Larger Than Life', artist='Backstreet Boys', album='Millennium')
        decision = self.decide(Source.ACOUSTID, tracks, metadata)
        self.assertEqual(Outcome.MATCHED, decision.outcome)
        self.assertEqual('rec-ltl', decision.recording_id)
        self.assertEqual('millennium', decision.release_id)
        self.assertTrue(decision.release_verified)
        self.assertEqual((), decision.reasons)

    def test_title_containing_with_matches_itself(self):
        tracks = [acoustid_recording('wowy', 'With or Without You', 'U2', 296 * SECOND, 0.97, BSB_RELEASES[1:2])]
        metadata = file_metadata(296 * SECOND, title='With or Without You', artist='U2')
        decision = self.decide(Source.ACOUSTID, tracks, metadata)
        self.assertEqual((Outcome.MATCHED, 'wowy', ()), (decision.outcome, decision.recording_id, decision.reasons))

    def test_title_prefix_before_with_is_a_different_title(self):
        tracks = [recording('stay', 'Stay', 'Sam Smith', 172 * SECOND, BSB_RELEASES[1:2])]
        metadata = file_metadata(172 * SECOND, title='Stay With Me', artist='Sam Smith')
        decision = self.decide(Source.METADATA, tracks, metadata)
        self.assertEqual(Outcome.NEEDS_REVIEW, decision.outcome)
        self.assertIn(Reason.TITLE_CONFLICT, decision.reasons)

    def test_2_low_score_single_candidate(self):
        # Case E: one candidate, Picard similarity ~0.39. The fingerprint score
        # itself is below the floor, so it is not usable evidence.
        tracks = [acoustid_recording('f8e53575', 'BAND4BAND', ('Central Cee', 'Lil Baby'), 140 * SECOND, 0.42)]
        metadata = file_metadata(140 * SECOND, title='BAND4BAND', artist='Central Cee ft. Lil Baby')
        decision = self.decide(Source.ACOUSTID, tracks, metadata)
        self.assertEqual(Outcome.NO_MATCH, decision.outcome)
        self.assertEqual((Reason.LOW_FINGERPRINT_SCORE,), decision.reasons)
        self.assertIsNotNone(decision.weak, 'candidate must be preserved, not discarded')
        self.assertIsNone(decision.match)

    def test_2_moderate_score_single_candidate_needs_full_confirmation(self):
        track = acoustid_recording('f8e53575', 'BAND4BAND', ('Central Cee', 'Lil Baby'), 140 * SECOND, 0.65)
        # Title, artist and length all confirm -> accepted
        metadata = file_metadata(140 * SECOND, title='BAND4BAND', artist='Central Cee ft. Lil Baby')
        self.assertEqual(Outcome.MATCHED, self.decide(Source.ACOUSTID, [track], metadata).outcome)
        # Untagged file with the same moderate fingerprint -> not enough
        decision = self.decide(Source.ACOUSTID, [track], file_metadata(140 * SECOND), filename='a.mp3')
        self.assertEqual(Outcome.NO_MATCH, decision.outcome)
        self.assertEqual((Reason.INSUFFICIENT_EVIDENCE,), decision.reasons)
        # Length only "close" (10 s) with a moderate fingerprint -> not enough
        metadata = file_metadata(150 * SECOND, title='BAND4BAND', artist='Central Cee')
        self.assertEqual(Outcome.NO_MATCH, self.decide(Source.ACOUSTID, [track], metadata).outcome)

    def test_untagged_file_with_unstructured_filename(self):
        # Picard copied "track01" into the title tag; that must not count as a
        # title conflict against a strong fingerprint.
        track = acoustid_recording('rec', 'Song', 'Artist', 200 * SECOND, 0.93)
        metadata = file_metadata(200 * SECOND, title='track01')
        decision = self.decide(Source.ACOUSTID, [track], metadata, filename='track01.mp3')
        self.assertEqual(Outcome.MATCHED, decision.outcome)
        # A real title tag (artist present) still conflicts
        metadata = file_metadata(200 * SECOND, title='track01', artist='Artist')
        decision = self.decide(Source.ACOUSTID, [track], metadata, filename='track01.mp3')
        self.assertEqual((Reason.TITLE_CONFLICT,), decision.reasons)

    def test_untagged_file_strong_fingerprint(self):
        track = acoustid_recording('rec', 'Song', 'Artist', 200 * SECOND, 0.93)
        decision = self.decide(Source.ACOUSTID, [track], file_metadata(201 * SECOND), filename='x.mp3')
        self.assertEqual(Outcome.MATCHED, decision.outcome)

    def test_3_artist_conflict_with_matching_title(self):
        tracks = [acoustid_recording('rec', 'Pretty Little Baby', 'Modest Clifton', 150 * SECOND, 0.9)]
        metadata = file_metadata(150 * SECOND, title='Pretty Little Baby', artist='Eloise')
        decision = self.decide(Source.ACOUSTID, tracks, metadata)
        self.assertEqual(Outcome.NEEDS_REVIEW, decision.outcome)
        self.assertEqual((Reason.ARTIST_CONFLICT,), decision.reasons)
        self.assertEqual(1, len(decision.candidates))

    def test_identifier_exception_for_artist_conflict(self):
        track = acoustid_recording('rec', 'Song', 'New Name', 150 * SECOND, 0.9)
        track['isrcs'] = ['USABC1234567']
        metadata = file_metadata(150 * SECOND, title='Song', artist='Old Name', isrc='USABC1234567')
        decision = self.decide(Source.ACOUSTID, [track], metadata)
        self.assertEqual(Outcome.MATCHED, decision.outcome)
        self.assertIn(Reason.CONFIRMED_BY_IDENTIFIER, decision.notes)

    def test_4_live_vs_studio(self):
        live = acoustid_recording('live', 'Fever (Live)', 'Joe Cocker', 230 * SECOND, 0.85)
        decision = self.decide(Source.ACOUSTID, [live], file_metadata(230 * SECOND, title='Fever', artist='Joe Cocker'))
        self.assertEqual(Outcome.NEEDS_REVIEW, decision.outcome)
        self.assertIn(Reason.VERSION_MISMATCH, decision.reasons)
        # Version from the MusicBrainz disambiguation comment
        live = acoustid_recording('live', 'Fever', 'Joe Cocker', 230 * SECOND, 0.85)
        live['disambiguation'] = 'live, 1999-05-01: Royal Albert Hall'
        decision = self.decide(Source.ACOUSTID, [live], file_metadata(230 * SECOND, title='Fever', artist='Joe Cocker'))
        self.assertIn(Reason.VERSION_MISMATCH, decision.reasons)
        # File says live, candidate is studio
        studio = acoustid_recording('studio', 'Fever', 'Joe Cocker', 230 * SECOND, 0.85)
        metadata = file_metadata(230 * SECOND, title='Fever (Live)', artist='Joe Cocker')
        self.assertEqual(Outcome.NEEDS_REVIEW, self.decide(Source.ACOUSTID, [studio], metadata).outcome)

    def test_5_remix_vs_original(self):
        original = acoustid_recording('orig', 'Song', 'Artist', 200 * SECOND, 0.9, sources_ratio=1.0)
        remix = acoustid_recording('remix', 'Song (Club Remix)', 'Artist', 200 * SECOND, 0.9, sources_ratio=0.5)
        # File is the original: the original ranks first and the remix is not
        # a plausible competitor, so no ambiguity
        metadata = file_metadata(200 * SECOND, title='Song', artist='Artist')
        decision = self.decide(Source.ACOUSTID, [original, remix], metadata)
        self.assertEqual(Outcome.MATCHED, decision.outcome)
        self.assertEqual('orig', decision.recording_id)
        # File is the remix but the original ranks first: never silently
        # applied
        metadata = file_metadata(200 * SECOND, title='Song (Club Remix)', artist='Artist')
        decision = self.decide(Source.ACOUSTID, [original, remix], metadata)
        self.assertEqual(Outcome.NEEDS_REVIEW, decision.outcome)
        self.assertIn(Reason.VERSION_MISMATCH, decision.reasons)
        # Remixes by the same name: length decides (extended mix shares the
        # first two minutes the fingerprint covers)
        extended = acoustid_recording('ext', 'Song', 'Artist', 380 * SECOND, 0.9)
        decision = self.decide(Source.ACOUSTID, [extended], file_metadata(200 * SECOND, title='Song', artist='Artist'))
        self.assertEqual((Reason.LENGTH_MISMATCH,), decision.reasons)

    def test_5_case_a_extended_mix_alternative_does_not_block(self):
        tracks = [
            acoustid_recording('f4e8ddd2', 'Larger Than Life', 'Backstreet Boys', 232 * SECOND, 0.79, BSB_RELEASES),
            acoustid_recording(
                'ext', 'Larger Than Life (extended video mix)', 'Backstreet Boys', 300 * SECOND, 0.33, acoustid='aid-2'
            ),
        ]
        metadata = file_metadata(title='Larger Than Life', artist='Backstreet Boys', date='1995')
        decision = self.decide(Source.ACOUSTID, tracks, metadata)
        self.assertEqual(Outcome.MATCHED, decision.outcome)
        self.assertEqual('f4e8ddd2', decision.recording_id)
        # Release: not the compilation listed first
        self.assertEqual('millennium', decision.release_id)
        self.assertFalse(decision.release_verified)
        self.assertIn(Reason.RELEASE_UNVERIFIED, decision.notes)
        self.assertIn(Reason.DATE_DIFFERS, decision.notes)

    def test_6_duplicate_candidate_entries(self):
        # Case B: the same recording via two AcoustIDs with different scores
        releases = (release('one-night', 'One Night of Sin', '1989'),)
        tracks = [
            acoustid_recording('fever', 'Fever', 'Joe Cocker', 230 * SECOND, 0.85, releases, acoustid='aid-1'),
            acoustid_recording('fever', 'Fever', 'Joe Cocker', 230 * SECOND, 0.84, releases, acoustid='aid-2'),
        ]
        metadata = file_metadata(230 * SECOND, title='Fever', artist='Joe Cocker')
        decision = self.decide(Source.ACOUSTID, tracks, metadata)
        self.assertEqual(Outcome.MATCHED, decision.outcome, 'a duplicate must not count as a competitor')
        self.assertEqual(1, len(decision.candidates))
        self.assertEqual(0.85, decision.candidates[0].fingerprint_score)

    def test_8_two_close_candidates(self):
        tracks = [
            acoustid_recording('a', 'Song', 'Artist', 200 * SECOND, 0.90),
            acoustid_recording('b', 'Song', 'Artist', 200 * SECOND, 0.89, acoustid='aid-2'),
        ]
        decision = self.decide(Source.ACOUSTID, tracks, file_metadata(200 * SECOND, title='Song', artist='Artist'))
        self.assertEqual(Outcome.NEEDS_REVIEW, decision.outcome)
        self.assertEqual((Reason.CLOSE_SCORES,), decision.reasons)
        self.assertEqual(2, len(decision.candidates))

    def test_no_candidates_and_errors(self):
        decision = self.decide(Source.ACOUSTID, [], file_metadata())
        self.assertEqual((Outcome.NO_MATCH, (Reason.NO_CANDIDATES,)), (decision.outcome, decision.reasons))
        # fpcalc failure or AcoustID status != ok: callback gets {} without error
        decision = self.decide(Source.ACOUSTID, None, file_metadata(), document={})
        self.assertEqual((Outcome.ERROR, (Reason.FINGERPRINT_ERROR,)), (decision.outcome, decision.reasons))
        decision = self.decide(
            Source.ACOUSTID, None, file_metadata(), document={}, error=QNetworkReply.NetworkError.HostNotFoundError
        )
        self.assertEqual((Outcome.ERROR, (Reason.NETWORK_ERROR,)), (decision.outcome, decision.reasons))


class MetadataDecisionTest(SmartScanPolicyTestCase):
    def test_3_case_d_filename_artist_conflict(self):
        # Case D: no artist tag, title guessed from the filename "Eloise - ..."
        tracks = [
            recording(
                'c92a6058',
                'Pretty Little Baby',
                'Modest Clifton',
                150 * SECOND,
                (
                    release(
                        'bootleg',
                        "The 'Must' for Dancing, Volume 2",
                        '2001',
                        status='Bootleg',
                        secondary=['Compilation'],
                    ),
                ),
                score=100,
            )
        ]
        metadata = file_metadata(150 * SECOND, title='Eloise - Pretty Little Baby')
        decision = self.decide(Source.METADATA, tracks, metadata, filename='Eloise - Pretty Little Baby.mp3')
        self.assertEqual(Outcome.NEEDS_REVIEW, decision.outcome)
        self.assertEqual((Reason.ARTIST_CONFLICT,), decision.reasons)
        self.assertIsNone(decision.match)

    def test_title_only_metadata_match_is_insufficient(self):
        tracks = [recording('r', 'Pretty Little Baby', 'Connie Francis', 150 * SECOND, score=100)]
        decision = self.decide(
            Source.METADATA, tracks, file_metadata(150 * SECOND, title='Pretty Little Baby'), 'x.mp3'
        )
        self.assertEqual(Outcome.NO_MATCH, decision.outcome)
        self.assertEqual((Reason.INSUFFICIENT_EVIDENCE,), decision.reasons)

    def test_8_case_c_close_scores(self):
        # Case C: metadata lookup, two recordings 0.8497 / 0.8400
        tracks = [
            recording('uw1', 'Underwater Love', 'Smoke City', 248 * SECOND, score=93),
            recording('uw2', 'Underwater Love', 'Smoke City', 249 * SECOND, score=92),
        ]
        metadata = file_metadata(248 * SECOND, title='Underwater Love', artist='Smoke City')
        decision = self.decide(Source.METADATA, tracks, metadata)
        self.assertEqual(Outcome.NEEDS_REVIEW, decision.outcome)
        self.assertEqual((Reason.CLOSE_SCORES,), decision.reasons)

    def test_metadata_match(self):
        tracks = [
            recording('uw1', 'Underwater Love', 'Smoke City', 248 * SECOND, score=100),
            recording('other', 'Underwater Love', 'Someone Else', 248 * SECOND, score=99),
        ]
        metadata = file_metadata(248 * SECOND, title='Underwater Love', artist='Smoke City')
        decision = self.decide(Source.METADATA, tracks, metadata)
        self.assertEqual(Outcome.MATCHED, decision.outcome)
        self.assertEqual('uw1', decision.recording_id)

    def test_metadata_length_unknown_or_close(self):
        tracks = [recording('uw1', 'Underwater Love', 'Smoke City', None, score=100)]
        metadata = file_metadata(248 * SECOND, title='Underwater Love', artist='Smoke City')
        # Without a length Picard's score drops below the metadata floor; the
        # candidate is never applied either way.
        decision = self.decide(Source.METADATA, tracks, metadata)
        self.assertEqual((Outcome.NO_MATCH, (Reason.LOW_SIMILARITY,)), (decision.outcome, decision.reasons))
        self.assertIsNone(decision.match)
        tracks = [recording('uw1', 'Underwater Love', 'Smoke City', 258 * SECOND, score=100)]
        decision = self.decide(Source.METADATA, tracks, metadata)
        self.assertEqual((Outcome.NEEDS_REVIEW, (Reason.LENGTH_MISMATCH,)), (decision.outcome, decision.reasons))


class ReleaseSelectionTest(SmartScanPolicyTestCase):
    def matched_release(self, releases, **tags):
        track = acoustid_recording('rec', 'Larger Than Life', 'Backstreet Boys', 232 * SECOND, 0.95, releases)
        tags.setdefault('title', 'Larger Than Life')
        tags.setdefault('artist', 'Backstreet Boys')
        decision = self.decide(Source.ACOUSTID, [track], file_metadata(**tags))
        return decision

    def test_7_same_recording_multiple_releases(self):
        decision = self.matched_release(BSB_RELEASES)
        self.assertEqual('millennium', decision.release_id, 'original album over compilation and single')
        self.assertIn(Reason.COMPETING_RELEASES, decision.notes)
        # Order of candidates must not matter
        decision = self.matched_release(tuple(reversed(BSB_RELEASES)))
        self.assertEqual('millennium', decision.release_id)

    def test_existing_album_is_preferred(self):
        decision = self.matched_release(BSB_RELEASES, album='Pop Party! Volume 1')
        self.assertEqual('pop-party', decision.release_id)
        self.assertTrue(decision.release_verified)
        for album in ('Millennium (Deluxe Edition)', 'Millennium - 20th Anniversary Edition', 'Millenium'):
            decision = self.matched_release(BSB_RELEASES, album=album)
            self.assertEqual(('millennium', True), (decision.release_id, decision.release_verified), album)

    def test_compilation_file_accepts_compilation(self):
        decision = self.matched_release(BSB_RELEASES[:1] + BSB_RELEASES[2:], albumartist='Various Artists')
        self.assertEqual('pop-party', decision.release_id)

    def test_official_over_bootleg(self):
        releases = (
            release('boot', 'Live Bootleg', '1998', status='Bootleg'),
            release('official', 'Millennium', '1999', status='Official'),
        )
        self.assertEqual('official', self.matched_release(releases).release_id)

    def test_preferred_country_and_discouraged_type(self):
        releases = (
            release('us', 'Millennium', '1999-05-18', country='US'),
            release('gb', 'Millennium', '1999-05-18', country='GB'),
        )
        self.set_config_values({'preferred_release_countries': ['GB']})
        self.assertEqual('gb', self.matched_release(releases).release_id)
        self.set_config_values({'preferred_release_countries': [], 'discouraged_release_types': ['Album']})
        self.assertEqual('ltl-single', self.matched_release(BSB_RELEASES).release_id)

    def test_album_conflict_keeps_recording_but_not_release(self):
        decision = self.matched_release(BSB_RELEASES, album='Black & Blue')
        self.assertEqual(Outcome.MATCHED, decision.outcome)
        self.assertFalse(decision.release_verified)
        self.assertIn(Reason.ALBUM_CONFLICT, decision.notes)
        self.assertIn(Reason.RELEASE_UNVERIFIED, decision.notes)

    def test_date_matching_original_release_is_not_flagged(self):
        decision = self.matched_release(BSB_RELEASES, date='1999')
        self.assertNotIn(Reason.DATE_DIFFERS, decision.notes)


class CombineTest(SmartScanPolicyTestCase):
    def test_failed_lookup_does_not_turn_weak_fingerprint_into_conflict(self):
        weak = self.decide(
            Source.ACOUSTID,
            [acoustid_recording('rec', 'Song', 'Artist', 200000, 0.4)],
            file_metadata(200000, title='Song', artist='Artist'),
        )
        failed = self.decide(
            Source.METADATA,
            [],
            file_metadata(),
            error=QNetworkReply.NetworkError.ServiceUnavailableError,
        )
        final = combine(weak, failed, self.thresholds)
        self.assertEqual(Outcome.ERROR, final.outcome)
        self.assertEqual((Reason.NETWORK_ERROR,), final.reasons)
        self.assertEqual(1, len(final.candidates))

    def test_weak_fingerprint_and_metadata_agree(self):
        weak = self.decide(
            Source.ACOUSTID,
            [acoustid_recording('rec', 'Song', 'Artist', 200 * SECOND, 0.4)],
            file_metadata(200 * SECOND, title='Song', artist='Artist'),
        )
        meta = self.decide(
            Source.METADATA,
            [recording('rec', 'Song', 'Artist', 200 * SECOND)],
            file_metadata(200 * SECOND, title='Song', artist='Artist'),
        )
        final = combine(weak, meta, self.thresholds)
        self.assertEqual(Outcome.MATCHED, final.outcome)
        self.assertEqual(2, len(final.candidates))

    def test_weak_fingerprint_and_metadata_disagree(self):
        weak = self.decide(
            Source.ACOUSTID,
            [acoustid_recording('fp-rec', 'Other Song', 'Other Artist', 200 * SECOND, 0.45)],
            file_metadata(200 * SECOND),
            filename='x.mp3',
        )
        meta = self.decide(
            Source.METADATA,
            [recording('meta-rec', 'Song', 'Artist', 200 * SECOND)],
            file_metadata(200 * SECOND, title='Song', artist='Artist'),
        )
        final = combine(weak, meta, self.thresholds)
        self.assertEqual((Outcome.NEEDS_REVIEW, (Reason.SOURCE_DISAGREEMENT,)), (final.outcome, final.reasons))

    def test_weak_fingerprint_kept_when_metadata_finds_nothing(self):
        weak = self.decide(
            Source.ACOUSTID,
            [acoustid_recording('rec', 'Song', 'Artist', 200 * SECOND, 0.4)],
            file_metadata(200 * SECOND, title='Song', artist='Artist'),
        )
        meta = self.decide(Source.METADATA, [], file_metadata())
        final = combine(weak, meta, self.thresholds)
        self.assertEqual(Outcome.NEEDS_REVIEW, final.outcome)
        self.assertEqual(1, len(final.candidates))

    def test_error_is_not_reported_as_no_match(self):
        failed = self.decide(Source.ACOUSTID, None, file_metadata(), document={})
        meta = self.decide(Source.METADATA, [], file_metadata())
        self.assertEqual(Outcome.ERROR, combine(failed, meta, self.thresholds).outcome)
        nothing = self.decide(Source.ACOUSTID, [], file_metadata())
        self.assertEqual(Outcome.NO_MATCH, combine(nothing, meta, self.thresholds).outcome)


class AcoustIDClientTest(PicardTestCase):
    def test_annotate_fingerprint_scores(self):
        recordings = [{'id': 'a', 'acoustid': 'aid-1', 'score': 40.0}, {'id': 'b', 'acoustid': 'aid-2'}, None]
        document = {'results': [{'id': 'aid-1', 'score': 0.8}, {'id': 'aid-2', 'score': 0.3}]}
        annotate_fingerprint_scores(recordings, document)
        self.assertEqual(0.8, recordings[0]['acoustid_score'])
        self.assertEqual(0.3, recordings[1]['acoustid_score'])

    def test_release_events_are_separate_releases(self):
        parsed = parse_recording(
            {
                'id': 'rec',
                'title': 'Song',
                'releasegroups': [
                    {
                        'id': 'rg',
                        'title': 'Album',
                        'releases': [
                            {
                                'id': 'rel',
                                'mediums': [],
                                'releaseevents': [{'country': 'GB', 'date': {'year': 1999}}, {'country': 'US'}],
                            }
                        ],
                    }
                ],
            }
        )
        self.assertEqual(['GB', 'US'], [r['country'] for r in parsed['releases']])


class SmartScanFileTest(PicardTestCase):
    """End-to-end Smart Scan state machine on a File with mocked services."""

    def setUp(self):
        super().setUp()
        self.set_config_values(BASE_SETTINGS)
        self.patch_tagger_instance('picard.item')
        self.tagger.window = MagicMock()
        self.tagger.acoustidmanager = MagicMock()
        self.tagger.isrc_submit_manager = MagicMock()
        self.tagger.webservice = MagicMock()
        self.tagger._acoustid = MagicMock()
        self.tagger.mb_api = MagicMock()
        self.tagger.mb_api.find_tracks.side_effect = lambda *args, **kwargs: object()
        self.tagger.move_file_to_track = MagicMock()
        self.tagger.move_file_to_nat = MagicMock()
        self.releasegroup = MagicMock()
        self.releasegroup.loaded_albums = set()
        self.tagger.get_release_group_by_id = MagicMock(return_value=self.releasegroup)
        self.file = File('/music/Backstreet Boys - Larger Than Life.mp3')
        self.file.state = File.State.NORMAL
        tags = {'title': 'Larger Than Life', 'artist': 'Backstreet Boys', 'genre': 'Pop', 'bpm': '120'}
        self.file.orig_metadata = file_metadata(**tags)
        self.file.metadata = file_metadata(**tags)

    # Helpers -------------------------------------------------------------
    def start(self):
        self.file.start_smart_scan()
        args, kwargs = self.tagger._acoustid.analyze.call_args
        self.assertFalse(kwargs.get('set_unmatched_acoustid', True))
        return args[1]

    def lookup_callback(self):
        return self.tagger.mb_api.find_tracks.call_args[0][0]

    def good_acoustid(self, score=0.95):
        return {
            'recordings': [
                acoustid_recording('rec', 'Larger Than Life', 'Backstreet Boys', 232 * SECOND, score, BSB_RELEASES)
            ]
        }

    # Tests ---------------------------------------------------------------
    def test_matched_applies_selected_release(self):
        self.file.metadata['album'] = 'Millennium'  # Credible existing release.
        callback = self.start()
        self.assertEqual(File.State.PENDING, self.file.state)
        callback(self.good_acoustid(), None, None)
        self.tagger.move_file_to_track.assert_called_once_with(self.file, 'millennium', 'rec')
        self.assertIn('millennium', self.releasegroup.loaded_albums)
        self.tagger.mb_api.find_tracks.assert_not_called()
        self.assertEqual(Outcome.MATCHED, self.file.smart_scan_decision.outcome)
        self.assertEqual('aid-1', self.file.metadata['acoustid_id'])
        self.assertEqual(File.State.CHANGED, self.file.state)  # New Tags differ; nothing saved
        self.assertIsNone(self.file.smart_scan_stage)

    def test_dictionary_release_date_completes_scan_without_fallback(self):
        self.file.metadata['album'] = 'Millennium'  # Credible existing release.
        releases = [dict(r, date={'year': 1999, 'month': 5, 'day': 18}) for r in BSB_RELEASES]
        track = acoustid_recording('rec', 'Larger Than Life', 'Backstreet Boys', 232 * SECOND, 0.95, releases)
        self.start()({'recordings': [track]}, None, None)
        self.assertEqual(Outcome.MATCHED, self.file.smart_scan_decision.outcome)
        self.assertEqual('1999-05-18', self.file.smart_scan_decision.candidates[0].date)
        self.assertIsNone(self.file.smart_scan_stage)
        self.tagger.move_file_to_track.assert_called_once_with(self.file, 'millennium', 'rec')
        self.tagger.mb_api.find_tracks.assert_not_called()

    def test_9_no_acoustid_result_then_metadata_lookup(self):
        self.file.metadata['album'] = 'Millennium'  # Credible existing release.
        callback = self.start()
        callback({'recordings': []}, None, None)
        self.assertEqual('lookup', self.file.smart_scan_stage)
        self.tagger.mb_api.find_tracks.assert_called_once()
        self.tagger.move_file_to_track.assert_not_called()
        self.lookup_callback()(
            {'recordings': [recording('rec', 'Larger Than Life', 'Backstreet Boys', 232 * SECOND, BSB_RELEASES)]},
            None,
            None,
        )
        self.tagger.move_file_to_track.assert_called_once_with(self.file, 'millennium', 'rec')
        self.assertEqual(Source.METADATA, self.file.smart_scan_decision.source)
        self.tagger.mb_api.find_tracks.assert_called_once()

    def test_filename_hints_used_for_fallback_query(self):
        self.file = File('/music/Eloise - Pretty Little Baby.mp3')
        self.file.state = File.State.NORMAL
        self.file.metadata = file_metadata(150 * SECOND, title='Eloise - Pretty Little Baby')
        self.start()({'recordings': []}, None, None)
        kwargs = self.tagger.mb_api.find_tracks.call_args.kwargs
        self.assertEqual(('Pretty Little Baby', 'Eloise'), (kwargs['track'], kwargs['artist']))

    def test_http_503_is_temporary_failure_even_with_a_recording_payload(self):
        self.start()({'recordings': []}, None, None)
        http = MagicMock()
        http.attribute.return_value = 503
        self.lookup_callback()(
            {
                'recordings': [recording('wrong', 'Wrong Song', 'Wrong Artist', 232000)],
            },
            http,
            None,
        )
        decision = self.file.smart_scan_decision
        self.assertEqual(Outcome.ERROR, decision.outcome)
        self.assertEqual((Reason.NETWORK_ERROR,), decision.reasons)
        self.assertEqual(503, decision.details['http_status'])
        self.assertTrue(decision.details['temporary'])
        self.tagger.move_file_to_track.assert_not_called()

    def test_cancelled_503_remains_cancellation(self):
        self.start()({'recordings': []}, None, None)
        http = MagicMock()
        http.attribute.return_value = 503
        self.lookup_callback()({}, http, QNetworkReply.NetworkError.OperationCanceledError)
        self.assertEqual(Outcome.CANCELLED, self.file.smart_scan_decision.outcome)

    def test_flattened_video_filename_uses_clean_background_search(self):
        self.file = File(
            '/music/002 - Rag_n_Bone_Man_P_nk_Anywhere_Away_From_Here_Official_Video - P_NK [IiuE9TzMfxs].mp4'
        )
        self.file.state = File.State.NORMAL
        self.file.metadata = file_metadata()
        self.start()({'recordings': []}, None, None)
        kwargs = self.tagger.mb_api.find_tracks.call_args.kwargs
        self.assertTrue(kwargs['search'])
        self.assertFalse(kwargs['advanced_search'])
        self.assertEqual('Rag n Bone Man P nk Anywhere Away From Here', kwargs['query'])

    def test_confirmed_video_caption_updates_new_tags_immediately(self):
        self.file = File(
            '/music/002 - Rag_n_Bone_Man_P_nk_Anywhere_Away_From_Here_Official_Video - P_NK [IiuE9TzMfxs].mp4'
        )
        self.file.state = File.State.NORMAL
        self.file.orig_metadata = file_metadata()
        self.file.metadata = Metadata(self.file.orig_metadata)
        node = acoustid_recording('rec', 'Anywhere Away From Here', ("Rag'n'Bone Man", 'P!nk'), 232000, 0.95)
        node['artist-credit'] = credit("Rag'n'Bone Man", 'P!nk', joinphrase=' & ')
        with patch.object(File, 'save') as save:
            self.start()({'recordings': [node]}, None, None)
        self.assertEqual(Outcome.MATCHED, self.file.smart_scan_decision.outcome)
        self.assertEqual("Rag'n'Bone Man & P!nk", self.file.metadata['artist'])
        self.assertEqual('Anywhere Away From Here', self.file.metadata['title'])
        self.assertEqual(('Official Video',), self.file.smart_scan_decision.details['source_labels'])
        self.assertEqual('', self.file.orig_metadata['title'])
        self.assertNotIn('Official Video', self.file.metadata['title'])
        self.tagger.mb_api.find_tracks.assert_not_called()
        save.assert_not_called()

    def test_video_caption_preserves_manual_edits_during_scan(self):
        self.file = File('/music/Rag_n_Bone_Man_P_nk_Anywhere_Away_From_Here_Official_Video.mp4')
        self.file.state = File.State.NORMAL
        self.file.metadata = file_metadata()
        callback = self.start()
        self.file.metadata['comment'] = 'My manual note'
        node = acoustid_recording('rec', 'Anywhere Away From Here', ("Rag'n'Bone Man", 'P!nk'), 232000, 0.95)
        callback({'recordings': [node]}, None, None)
        self.assertEqual('My manual note', self.file.metadata['comment'])

    def test_10_acoustid_failure_then_lookup(self):
        callback = self.start()
        callback({}, None, QNetworkReply.NetworkError.HostNotFoundError)
        self.tagger.mb_api.find_tracks.assert_called_once()
        self.lookup_callback()({'recordings': []}, None, None)
        decision = self.file.smart_scan_decision
        self.assertEqual(Outcome.ERROR, decision.outcome)
        self.assertIn(Reason.NETWORK_ERROR, decision.reasons)
        self.tagger.move_file_to_track.assert_not_called()
        self.assertEqual(File.State.NORMAL, self.file.state)

    def test_10_both_lookups_fail(self):
        self.start()(None, None, None)  # fpcalc produced nothing
        self.lookup_callback()({}, None, QNetworkReply.NetworkError.TimeoutError)
        self.assertEqual(Outcome.ERROR, self.file.smart_scan_decision.outcome)

    def test_needs_review_from_acoustid_searches_but_does_not_apply(self):
        document = {'recordings': [acoustid_recording('rec', 'Larger Than Life', 'Someone Else', 232 * SECOND, 0.95)]}
        before = Metadata(self.file.metadata)
        self.start()(document, None, None)
        # The file's own artist/title are searched; a perfect text match still
        # cannot overrule the fingerprint's recording.
        self.tagger.mb_api.find_tracks.assert_called_once()
        self.lookup_callback()(
            {'recordings': [recording('bsb', 'Larger Than Life', 'Backstreet Boys', 232 * SECOND, BSB_RELEASES)]},
            None,
            None,
        )
        self.tagger.move_file_to_track.assert_not_called()
        self.tagger.move_file_to_nat.assert_not_called()
        self.assertEqual(Outcome.NEEDS_REVIEW, self.file.smart_scan_decision.outcome)
        self.assertEqual(before, self.file.metadata)
        self.tagger.acoustidmanager.add.assert_not_called()

    def test_11_user_edits_during_scan_are_preserved_on_review(self):
        callback = self.start()
        # The user edits New Tags while the scan is running
        self.file.metadata['title'] = 'Larger Than Life (Live)'
        self.file.metadata['comment'] = 'my note'
        callback(self.good_acoustid(), None, None)
        # The edit is the evidence now: studio candidate vs live file. The
        # edited title is what the metadata lookup searches.
        self.assertEqual('Larger Than Life (Live)', self.tagger.mb_api.find_tracks.call_args.kwargs['track'])
        self.lookup_callback()({'recordings': []}, None, None)
        self.assertEqual(Outcome.NEEDS_REVIEW, self.file.smart_scan_decision.outcome)
        self.assertEqual('Larger Than Life (Live)', self.file.metadata['title'])
        self.assertEqual('my note', self.file.metadata['comment'])
        self.tagger.move_file_to_track.assert_not_called()

    def test_low_fingerprint_fallback_without_result_keeps_candidate(self):
        self.start()(self.good_acoustid(score=0.3), None, None)
        self.lookup_callback()({'recordings': []}, None, None)
        decision = self.file.smart_scan_decision
        self.assertEqual(Outcome.NEEDS_REVIEW, decision.outcome)
        self.assertEqual('rec', decision.candidates[0].recording_id)
        self.tagger.move_file_to_track.assert_not_called()

    def test_15_cancel_during_scan(self):
        callback = self.start()
        self.file.clear_lookup_task()
        self.assertEqual(Outcome.CANCELLED, self.file.smart_scan_decision.outcome)
        self.assertEqual(File.State.NORMAL, self.file.state)
        callback(self.good_acoustid(), None, None)  # late callback
        self.tagger.move_file_to_track.assert_not_called()
        self.tagger.mb_api.find_tracks.assert_not_called()

    def test_15_cancel_during_fallback_lookup(self):
        self.start()({'recordings': []}, None, None)
        lookup = self.lookup_callback()
        self.file.clear_lookup_task()
        self.tagger.webservice.abort_task.assert_called_once()
        lookup({}, None, QNetworkReply.NetworkError.OperationCanceledError)
        self.assertEqual(Outcome.CANCELLED, self.file.smart_scan_decision.outcome)
        self.tagger.move_file_to_track.assert_not_called()

    def test_15_remove_during_scan(self):
        callback = self.start()
        self.file.remove(from_parent_item=False)
        self.assertEqual(File.State.REMOVED, self.file.state)
        callback(self.good_acoustid(), None, None)
        self.tagger.move_file_to_track.assert_not_called()
        self.tagger.mb_api.find_tracks.assert_not_called()

    def test_stale_callback_from_superseded_scan(self):
        old_callback = self.start()
        self.file.clear_lookup_task()
        new_callback = self.start()
        old_callback(self.good_acoustid(), None, None)
        self.tagger.move_file_to_track.assert_not_called()
        self.assertEqual('scan', self.file.smart_scan_stage)
        new_callback({'recordings': []}, None, None)
        self.assertEqual('lookup', self.file.smart_scan_stage)
        # A stale AcoustID callback cannot re-trigger a second fallback
        new_callback({'recordings': []}, None, None)
        self.tagger.mb_api.find_tracks.assert_called_once()

    def test_no_double_start_and_manual_lookup_blocked(self):
        self.start()
        self.file.start_smart_scan()
        self.assertEqual(1, self.tagger._acoustid.analyze.call_count)
        self.file.lookup_metadata()
        self.tagger.mb_api.find_tracks.assert_not_called()

    def test_14_no_disk_write_and_orig_metadata_untouched(self):
        orig = Metadata(self.file.orig_metadata)
        with (
            patch.object(File, 'save') as save,
            patch.object(File, '_save') as _save,
            patch.object(File, '_save_and_rename') as save_and_rename,
        ):
            self.start()(self.good_acoustid(), None, None)
            self.start()({'recordings': []}, None, None)
            self.lookup_callback()({'recordings': []}, None, None)
        save.assert_not_called()
        _save.assert_not_called()
        save_and_rename.assert_not_called()
        self.assertEqual(orig, self.file.orig_metadata)


class SmartScanProtectionTest(PicardTestCase):
    """Protected tags through Picard's real Track.update_file_metadata."""

    def setUp(self):
        super().setUp()
        self.set_config_values(dict(BASE_SETTINGS, clear_existing_tags=True))
        self.patch_tagger_instance('picard.item')
        self.tagger.window = MagicMock()
        self.tagger.acoustidmanager = MagicMock()
        self.file = File('/music/song.mp3')
        self.file.state = File.State.NORMAL
        tags = {
            'title': 'Song',
            'artist': 'Artist',
            'date': '1995',
            'genre': ['Rock', 'Pop'],
            'bpm': '128',
            '~rating': '4',
            'comment': 'from the original CD',
            'mycustomtag': ['one', 'two'],
            'replaygain_track_gain': '-6.5 dB',
            'isrc': 'USABC1234567',
        }
        self.file.orig_metadata = file_metadata(**tags)
        self.file.metadata = file_metadata(**tags)
        self.track = Track('rec')
        self.track.orig_metadata = Metadata(
            title='Song', artist='Artist', date='1999-05-18', musicbrainz_recordingid='rec'
        )
        self.track.metadata = Metadata(self.track.orig_metadata)

    def apply(self, release_verified=True):
        decision = MagicMock()
        decision.source = Source.METADATA
        decision.match.track = {'id': 'rec'}
        decision.release = None
        decision.release_verified = release_verified
        decision.user_confirmed = False
        self.tagger.move_file_to_nat = MagicMock()
        self.file._apply_smart_scan_match(decision)
        self.track.add_file(self.file)

    def test_12_13_protected_tags_survive_clear_existing_tags(self):
        self.apply()
        metadata = self.file.metadata
        # The date the file already had stays: a release date never replaces it.
        self.assertEqual('1995', metadata['date'])
        self.assertEqual(['Rock', 'Pop'], metadata.getall('genre'))
        self.assertEqual('128', metadata['bpm'])
        self.assertEqual('4', metadata['~rating'])
        self.assertEqual('from the original CD', metadata['comment'])
        self.assertEqual(['one', 'two'], metadata.getall('mycustomtag'))
        self.assertEqual('-6.5 dB', metadata['replaygain_track_gain'])
        self.assertEqual('USABC1234567', metadata['isrc'])

    def test_12_musicbrainz_value_replaces_populated_tag(self):
        self.track.orig_metadata['genre'] = 'Dance'
        self.track.metadata = Metadata(self.track.orig_metadata)
        self.apply()
        self.assertEqual(['Dance'], self.file.metadata.getall('genre'))

    def test_without_smart_scan_picard_behaviour_is_unchanged(self):
        self.track.add_file(self.file)
        self.assertEqual('', self.file.metadata['bpm'])
        self.assertEqual('', self.file.metadata['mycustomtag'])

    def test_user_deleted_tag_is_not_restored(self):
        del self.file.metadata['bpm']
        self.apply()
        self.assertEqual('', self.file.metadata['bpm'])

    def test_reverting_to_original_drops_protection(self):
        self.apply()
        self.track.remove_file(self.file)
        self.assertIsNone(self.file._smart_scan_protected)
        self.assertEqual(self.file.orig_metadata, self.file.metadata)

    def test_artwork_kept_for_unverified_release(self):
        own = ImageList()
        own.append(create_image(b'own', types=['front']))
        self.file.metadata.images = own.copy()
        self.track.metadata.images = ImageList()
        self.track.metadata.images.append(create_image(b'compilation', types=['front']))
        self.apply(release_verified=False)
        self.assertEqual(own, self.file.metadata.images)

    def test_artwork_from_verified_release_is_used(self):
        self.file.metadata.images = ImageList()
        self.file.metadata.images.append(create_image(b'own', types=['front']))
        release_images = ImageList()
        release_images.append(create_image(b'release', types=['front']))
        self.track.metadata.images = release_images
        self.apply(release_verified=True)
        self.assertEqual(release_images, self.file.metadata.images)

    def test_orig_metadata_never_modified(self):
        orig = Metadata(self.file.orig_metadata)
        self.apply()
        self.assertEqual(orig, self.file.orig_metadata)


# MusicBrainz JSON strings and AcoustID release / release-event dictionaries.
DATE_CASES = [
    ('1999', '1999'),
    ('1999-05', '1999-05'),
    ('1999-05-18', '1999-05-18'),
    (' 1999-05-18 ', '1999-05-18'),
    ({'year': 1999}, '1999'),
    ({'year': 1999, 'month': 5}, '1999-05'),
    ({'year': 1999, 'month': 5, 'day': 18}, '1999-05-18'),
    ({'year': '1999', 'month': '05', 'day': '18'}, '1999-05-18'),
    ({'year': 1999, 'month': None, 'day': None}, '1999'),
    ({'year': 1999, 'month': 0, 'day': 0}, '1999'),
    ({'year': 1999, 'day': 18}, '1999-00-18'),
    ('1999-00-18', '1999-00-18'),
    (None, ''),
    ('', ''),
    ({}, ''),
    ({'month': 5}, ''),
    ('unknown', ''),
    ('1999-99-18', ''),
    ('1999-02-30', ''),
    ({'year': 1999, 'month': 13}, ''),
    ({'year': 1999, 'month': 2, 'day': 30}, ''),
    ({'year': {'value': 1999}}, ''),
    ({'year': True}, ''),
    ({'year': 1999.5}, ''),
    ({'year': 'unknown'}, ''),
    (1999, ''),
    (['1999'], ''),
]


class SmartScanDateTest(PicardTestCase):
    @subtest_cases('value,expected', DATE_CASES)
    def test_normalization(self, value, expected):
        self.assertEqual(expected, smartscan.normalize_release_date(value))

    @subtest_cases('value,expected', DATE_CASES)
    def test_sort_value(self, value, expected):
        rank = -int((expected.replace('-', '') + '0000')[:8]) if expected else -99999999
        self.assertEqual(rank, smartscan._date_sort_value(value))

    @subtest_cases('value,expected', DATE_CASES)
    def test_matching_results_year(self, value, expected):
        candidate = SimpleNamespace(
            artist='Artist',
            title='Song',
            release_title='Album',
            date=value,
            similarity=0.9,
            fingerprint_score=None,
            reasons=(),
        )
        target = MagicMock(spec=File)
        target.dlmtags_tag_sources = {}
        target.smart_scan_decision = smartscan.Decision(
            Outcome.MATCHED,
            candidates=(candidate,),
            match=SimpleNamespace(track={'id': 'rec', 'title': 'Song', 'artist-credit': credit('Artist')}),
        )
        results = MagicMock()
        model = QtGui.QStandardItemModel()
        results.table.model.return_value = model
        MatchingResults._show_candidates(results, target.smart_scan_decision)
        self.assertEqual(0, model.rowCount())  # Release details are retained, not displayed.
        self.assertIn('Artist — Song', MatchingResults._describe(results, target))


class SmartScanDictionaryReleaseTest(SmartScanPolicyTestCase):
    matched_release = ReleaseSelectionTest.matched_release

    def test_dictionary_dates_keep_original_release_order_and_precision(self):
        releases = (
            release('later', 'Millennium', {'year': 2000, 'month': 1, 'day': 1}),
            release('original', 'Millennium', {'year': 1999, 'month': 5, 'day': 18}),
            release('missing', 'Millennium', None),
        )
        for ordered in (releases, tuple(reversed(releases))):
            decision = self.matched_release(ordered, date='1999')
            self.assertEqual('original', decision.release_id)
            self.assertNotIn(Reason.DATE_DIFFERS, decision.notes)
            self.assertEqual('1999-05-18', decision.candidates[0].date)
        self.assertEqual({'year': 1999, 'month': 5, 'day': 18}, releases[1]['date'])


class AcoustIDExceptionBoundaryTest(PicardTestCase):
    def test_internal_callback_errors_are_not_response_or_network_errors(self):
        task = AcoustIDTask(MagicMock(), MagicMock())
        client = MagicMock()
        with patch('picard.acoustid.RecordingResolver') as resolver:
            for error in (AttributeError('internal'), KeyError('internal'), TypeError('internal')):
                resolver.return_value.resolve.side_effect = error
                with self.assertRaises(type(error)):
                    AcoustIDClient._on_lookup_finished(client, task, {'status': 'ok'}, None, None)
        task.next_func.assert_not_called()
        client.tagger.window.set_statusbar_message.assert_not_called()

    def test_synchronous_callback_exception_is_not_retried(self):
        task = AcoustIDTask(MagicMock(), MagicMock(side_effect=KeyError('internal')))
        with patch('picard.acoustid.RecordingResolver') as resolver:
            resolver.return_value.resolve.side_effect = lambda: task.next_func({'recordings': []}, None, None)
            with self.assertRaises(KeyError):
                AcoustIDClient._on_lookup_finished(MagicMock(), task, {'status': 'ok'}, None, None)
        task.next_func.assert_called_once_with({'recordings': []}, None, None)

    def test_malformed_response_is_still_reported(self):
        task = AcoustIDTask(MagicMock(), MagicMock())
        AcoustIDClient._on_lookup_finished(MagicMock(), task, {}, None, None)
        self.assertIsInstance(task.next_func.call_args.args[2], KeyError)


# Real-world case: a YouTube official video whose audio is longer than the
# MusicBrainz recording. Data trimmed from the AcoustID /v2/lookup response
# (fingerprint of the 156.5 s video) and the MusicBrainz recording search,
# both retrieved on 2026-10-22.
PINK_FILENAME = 'P_NK Willow Sage Hart - Cover Me In Sunshine Official Video - P_NK [vGZhMIXH62M].mp3'
PINK_LENGTH = 156526
PINK_RECORDING = 'a8acab82-3308-40a0-bbc4-eb390237b853'
PINK_CREDIT = [
    {'id': 'f4d5cc07-3bc9-4836-9b15-88a08359bc63', 'joinphrase': ' + ', 'name': 'P!nk'},
    {'id': '9193bb79-a196-4931-a461-68a3fa3cbeb3', 'name': 'Willow Sage Hart'},
]


def pink_acoustid_document():
    recording = {
        'id': PINK_RECORDING,
        'title': 'Cover Me in Sunshine',
        'duration': 141.05,
        'sources': 4,
        'artists': PINK_CREDIT,
        'releasegroups': [
            {
                'id': 'dc79d0fe-91b2-4999-bd1c-f7fd7755ca3f',
                'type': 'Single',
                'title': 'Cover Me in Sunshine',
                'artists': PINK_CREDIT,
                'releases': [
                    {
                        'id': '936a0a82-11b1-4d3b-8202-dde08daccd10',
                        'country': 'XW',
                        'date': {'year': 2021, 'month': 2, 'day': 12},
                        'medium_count': 1,
                        'track_count': 1,
                        'mediums': [
                            {
                                'format': 'Digital Media',
                                'position': 1,
                                'track_count': 1,
                                'tracks': [
                                    {
                                        'id': '9e4098f2-2989-41b8-b54f-32b023f7ef25',
                                        'position': 1,
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    return {
        'status': 'ok',
        'results': [
            {
                'id': '754c58ce-b827-4ae3-ae72-68bda67f31c9',
                'score': 0.9861551,
                'recordings': [recording],
            }
        ],
    }


def pink_acoustid_recordings():
    """The recordings as Picard's RecordingResolver hands them to Smart Scan."""
    document = pink_acoustid_document()
    recordings = []
    for result in document['results']:
        for node in result['recordings']:
            parsed = parse_recording(node)
            parsed['acoustid'] = result['id']
            parsed['score'] = result['score'] * 100  # single recording: sources ratio 1.0
            recordings.append(parsed)
    annotate_fingerprint_scores(recordings, document)
    return recordings


def pink_musicbrainz_recordings():
    credit_nodes = credit('P!nk', 'Willow Sage Hart', joinphrase=' + ')
    live = credit('P!nk', 'Willow Sage Hart', joinphrase=' & ')
    return [
        recording(
            PINK_RECORDING,
            'Cover Me in Sunshine',
            ('P!nk', 'Willow Sage Hart'),
            141050,
            [release('single', 'Cover Me in Sunshine', '2021-02-12', primary='Single', status='Official', tracks=1)],
            score=100,
        )
        | {'artist-credit': credit_nodes},
        recording(
            '4fd147cd-1ae1-4f51-b706-3e9da89f1555',
            'Cover Me in Sunshine (live)',
            ('P!nk', 'Willow Sage Hart'),
            171233,
            score=92,
        )
        | {'artist-credit': live},
        recording(
            'cba0da8a-174d-4b41-82bb-2bbdb8b1158c',
            'Cover Me in Sunshine (live)',
            ('P!nk', 'Willow Sage Hart'),
            171000,
            score=92,
            disambiguation='360 Reality Audio mix',
        )
        | {'artist-credit': live},
    ]


class OfficialVideoLengthTest(SmartScanPolicyTestCase):
    """P!nk + Willow Sage Hart, "Cover Me in Sunshine": Smart Scan vs Find Title."""

    def test_filename_parsing(self):
        hints = file_hints(file_metadata(PINK_LENGTH), PINK_FILENAME)
        # Video id and the uploader suffix after "Official Video" are removed;
        # the label is kept apart and is not part of the title.
        self.assertEqual(('P NK Willow Sage Hart', 'Cover Me In Sunshine'), (hints.artist, hints.title))
        self.assertEqual(('Official Video',), hints.source_labels)

    def test_correct_recording_is_found_and_only_duration_differs(self):
        decision = self.decide(Source.ACOUSTID, pink_acoustid_recordings(), file_metadata(PINK_LENGTH), PINK_FILENAME)
        top = decision.candidates[0]
        self.assertEqual(PINK_RECORDING, top.recording_id)
        self.assertAlmostEqual(0.986, top.fingerprint_score, places=3)
        self.assertEqual({'Artist matches', 'Title matches', 'No musical version conflict'}, set(top.evidence))
        self.assertGreater(PINK_LENGTH - top.length, self.thresholds.length_conflict_ms)

    @unittest.expectedFailure
    def test_official_video_with_strong_fingerprint_is_matched(self):
        # Fails today: Needs Review (length_mismatch), the video is 15.5 s longer
        # than the recording. Remove expectedFailure together with the fix.
        decision = self.decide(Source.ACOUSTID, pink_acoustid_recordings(), file_metadata(PINK_LENGTH), PINK_FILENAME)
        self.assertEqual((Outcome.MATCHED, PINK_RECORDING), (decision.outcome, decision.recording_id))

    def test_metadata_only_match_still_needs_review(self):
        # Find Title's first result is the same recording, but without a
        # fingerprint the duration difference must keep it out of Matched.
        acoustid = self.decide(Source.ACOUSTID, [], file_metadata(PINK_LENGTH), PINK_FILENAME)
        metadata = self.decide(
            Source.METADATA,
            pink_musicbrainz_recordings(),
            file_metadata(PINK_LENGTH),
            PINK_FILENAME,
        )
        final = combine(acoustid, metadata, self.thresholds)
        self.assertEqual(Outcome.NEEDS_REVIEW, final.outcome)
        self.assertEqual(PINK_RECORDING, metadata.candidates[0].recording_id)

    def test_same_duration_gap_without_video_label_still_needs_review(self):
        filename = 'P!nk & Willow Sage Hart - Cover Me in Sunshine.mp3'
        decision = self.decide(Source.ACOUSTID, pink_acoustid_recordings(), file_metadata(PINK_LENGTH), filename)
        self.assertEqual((Outcome.NEEDS_REVIEW, (Reason.LENGTH_MISMATCH,)), (decision.outcome, decision.reasons))


# yt-dlp playlist downloads are named "NNN-<uploader> - <video title> [<id>]".
# Real file names from the local collection; responses trimmed from AcoustID
# (fingerprint of the 308.4 s video) on 2026-10-22.
GAGA_FILENAME = '437-Lady Gaga - Lady Gaga - Bad Romance (Official Music Video) [qrO4YZeyl0I].mp4'
GAGA_LENGTH = 308360
GAGA_RECORDING = '1cd69228-2994-4016-8bd9-bb417da3d840'


def gaga_acoustid_document():
    credit = [{'id': '650e7db6-b795-4eb5-a702-5ea2fc46c848', 'name': 'Lady Gaga'}]

    def node(rid, duration, sources, group, release, date, tracks, position):
        return {
            'id': rid,
            'title': 'Bad Romance',
            'duration': duration,
            'sources': sources,
            'artists': credit,
            'releasegroups': [
                {
                    'id': group[0],
                    'type': group[1],
                    'title': group[2],
                    'artists': credit,
                    'releases': [
                        {
                            'id': release,
                            'country': 'XW',
                            'date': date,
                            'medium_count': 1,
                            'track_count': tracks,
                            'mediums': [
                                {
                                    'format': 'Digital Media',
                                    'position': 1,
                                    'track_count': tracks,
                                    'tracks': [{'id': release[:8] + '-track', 'position': position}],
                                }
                            ],
                        }
                    ],
                }
            ],
        }

    single = ('4535cbfb-a9a8-49ac-ac19-8f64d7af3bc7', 'Single', 'Bad Romance')
    ep = ('20b4c8b3-e922-44a3-90e8-fa00acad8ed4', 'EP', 'The Fame Monster')
    video = node(
        GAGA_RECORDING,
        315.0,
        61,
        single,
        '04f101f9-ec94-44e5-ada0-ad008e4ba143',
        {'year': 2009, 'month': 11, 'day': 19},
        5,
        5,
    )
    return {
        'status': 'ok',
        'results': [
            {'id': '3a3c45ec-c01d-4c92-b96f-554b0eb0f700', 'score': 0.9718486, 'recordings': [video]},
            {
                'id': '4c732921-0aa2-43db-a22e-e57a0e81dfe8',
                'score': 0.95747626,
                'recordings': [
                    node(
                        '02875e5a-101b-49a0-b5ec-c8d58dc39d24',
                        302.0,
                        1,
                        ep,
                        '6ce91498-f397-4261-a895-4529a0bcb974',
                        {'year': 2022, 'month': 7, 'day': 9},
                        9,
                        1,
                    ),
                    dict(video, sources=9),
                ],
            },
        ],
    }


def resolved_acoustid_recordings(document):
    """Recordings exactly as Picard's RecordingResolver passes them on."""
    result = []
    RecordingResolver(MagicMock(), document, lambda recordings, error=None: result.extend(recordings)).resolve()
    annotate_fingerprint_scores(result, document)
    return result


def loaded_metadata(filename, length=0, **tags):
    """New Tags of a loaded file: embedded tags, else Picard's filename guess."""
    metadata = file_metadata(length, **tags)
    if 'title' not in tags:
        metadata['title'] = tracknum_and_title_from_filename(filename).title
    return metadata


class FilenameTemplateTest(SmartScanPolicyTestCase):
    def hints(self, filename, **tags):
        return file_hints(loaded_metadata(filename, **tags), filename)

    @subtest_cases(
        'filename,artist,title',
        [
            (GAGA_FILENAME, 'Lady Gaga', 'Bad Romance'),
            (
                "173-RHINO - Sugar Ray - When It's Over (Official Music Video) [xmLv8rfunPo].mp4",
                'Sugar Ray',
                "When It's Over",
            ),
            (
                '075-Backstreet Boys - Backstreet Boys - Shape Of My Heart (Official HD Video) [OT5msu-dap8].mp4',
                'Backstreet Boys',
                'Shape Of My Heart',
            ),
            (
                '096-Usher - Usher, Alicia Keys - My Boo (Official Video) [fPgf2meEX1w].mp4',
                'Usher, Alicia Keys',
                'My Boo',
            ),
            (
                "124-Fergie - Fergie - Big Girls Don't Cry (Personal) (Extended Version) [E6sqA9QtV5I].mp4",
                'Fergie',
                "Big Girls Don't Cry (Personal) (Extended Version)",
            ),
            ('1086-Άννα Βίσση - Άννα Βίσση - Δέλτα (Official Video) [Xk3pQ9rT2bM].mp4', 'Άννα Βίσση', 'Δέλτα'),
        ],
    )
    def test_playlist_template_uploader_is_not_the_artist(self, filename, artist, title):
        hints = self.hints(filename)
        self.assertEqual((artist, title, 'filename'), (hints.artist, hints.title, hints.artist_source))

    def test_playlist_template_keeps_version_qualifiers(self):
        hints = self.hints("124-Fergie - Fergie - Big Girls Don't Cry (Personal) (Extended Version) [E6sqA9QtV5I].mp4")
        self.assertEqual(frozenset({'extended'}), split_title(hints.title).versions)

    def test_playlist_template_caption_without_artist_uses_caption_search(self):
        hints = self.hints("165-Rawish - It Wasn't Me by Shaggy feat RikRok [GH_StQ6KdW0].mp4")
        self.assertEqual(('', None), (hints.artist, hints.artist_source))
        self.assertEqual("It Wasn't Me by Shaggy feat RikRok", hints.filename_text)

    def test_embedded_tags_still_take_priority_over_the_template(self):
        hints = self.hints(GAGA_FILENAME, artist='Σάκης Ρουβάς', title='Σ’ έχω ερωτευτεί')
        self.assertEqual(
            ('Σάκης Ρουβάς', 'tag', 'Σ’ έχω ερωτευτεί', 'tag'),
            (hints.artist, hints.artist_source, hints.title, hints.title_source),
        )

    @subtest_cases(
        'filename,parsed',
        [
            # Spaced track numbers are handled as before.
            ('080 - Shaboozey - A Bar Song (Tipsy).mp3', ('Shaboozey', 'A Bar Song (Tipsy)')),
            # A title that starts with digits and a hyphen, without a video id.
            ('867-5309Jenny, LIVE.mp4', None),
            # Without a video id the template is not identified.
            ('437-Lady Gaga - Lady Gaga - Bad Romance.mp4', ('437-Lady Gaga', 'Bad Romance')),
            # A year is not a playlist index.
            ('2012-Adele - Skyfall [DeumyOzKqgI].mp4', ('2012-Adele', 'Skyfall')),
            # One- and two-digit prefixes are not the template.
            ('12-Two Door Cinema Club - What You Know [_ZAn9lB3pTU].mp4', ('12-Two Door Cinema Club', 'What You Know')),
        ],
    )
    def test_ordinary_numbered_names_are_unchanged(self, filename, parsed):
        self.assertEqual(parsed, smartscan.filename_artist_title(filename))

    def test_official_visualizer_is_a_source_label(self):
        hints = self.hints('080 - Shaboozey - A Bar Song (Tipsy) [Official Visualizer].mp3')
        self.assertEqual(('Shaboozey', 'A Bar Song (Tipsy)'), (hints.artist, hints.title))
        self.assertEqual(('Official Visualizer',), hints.source_labels)
        self.assertEqual('Song', split_title('Song (Official Visualiser)').base)
        # Song names and musical qualifiers are kept.
        self.assertEqual('Visualize Me', split_title('Visualize Me').base)
        self.assertEqual(frozenset({'live'}), split_title('Song (Live) [Official Visualizer]').versions)

    @subtest_cases(
        'filename,parsed',
        [
            (
                "Fergie - The Dutchess - Big Girls Don't Cry (Extended Version).mp3",
                ('Fergie', "Big Girls Don't Cry (Extended Version)"),
            ),
            ('Artist - Album - Song (Live).mp3', ('Artist', 'Song (Live)')),
            ('Artist - Album - Song (Radio Edit) [Remastered].mp3', ('Artist', 'Song (Radio Edit) (Remastered)')),
            # A part that only qualifies the title before it stays attached.
            ('Artist - Song - Live.mp3', ('Artist', 'Song - Live')),
            ('Artist - Song - (Radio Edit).mp3', ('Artist', 'Song - (Radio Edit)')),
            ('Artist - Song - Remastered 2011.mp3', ('Artist', 'Song - Remastered 2011')),
            ('Artist - Song - feat. Other.mp3', ('Artist', 'Song - feat. Other')),
            # A source label is not title text (real names from the local collection).
            (
                '060 - The Rolling Stones - Rain Fall Down - OFFICIAL PROMO (EXPLICIT).mp4',
                ('The Rolling Stones', 'Rain Fall Down - OFFICIAL PROMO (EXPLICIT)'),
            ),
            (
                '070 - The Rolling Stones - Streets Of Love - OFFICIAL PROMO.mp4',
                ('The Rolling Stones', 'Streets Of Love'),
            ),
        ],
    )
    def test_three_part_names_with_version_qualifiers(self, filename, parsed):
        self.assertEqual(parsed, smartscan.filename_artist_title(filename))

    def test_playlist_template_with_fingerprint_matches_the_recording(self):
        decision = self.decide(
            Source.ACOUSTID,
            resolved_acoustid_recordings(gaga_acoustid_document()),
            loaded_metadata(GAGA_FILENAME, GAGA_LENGTH),
            GAGA_FILENAME,
        )
        self.assertEqual((Outcome.MATCHED, GAGA_RECORDING), (decision.outcome, decision.recording_id))

    def test_playlist_template_does_not_match_a_different_song(self):
        filename = "173-RHINO - Sugar Ray - When It's Over (Official Music Video) [xmLv8rfunPo].mp4"
        tracks = [acoustid_recording('other', "It Ain't Over When It's Over", 'Kelley Hunt', 255 * SECOND, 0.95)]
        decision = self.decide(Source.ACOUSTID, tracks, loaded_metadata(filename, 255 * SECOND), filename)
        self.assertEqual(Outcome.NEEDS_REVIEW, decision.outcome)
        self.assertIn(Reason.TITLE_CONFLICT, decision.reasons)

    def test_uploader_name_is_no_longer_artist_evidence(self):
        # A recording credited to the uploader channel must not be confirmed
        # by the channel name in the file name.
        filename = "165-Rawish - It Wasn't Me by Shaggy feat RikRok [GH_StQ6KdW0].mp4"
        tracks = [acoustid_recording('rawish', "It Wasn't Me", 'Rawish', 230 * SECOND, 0.95)]
        decision = self.decide(Source.ACOUSTID, tracks, loaded_metadata(filename, 230 * SECOND), filename)
        self.assertNotEqual(Outcome.MATCHED, decision.outcome)

    def test_playlist_template_without_fingerprint_is_not_matched_on_a_different_duration(self):
        tracks = [recording('audio', 'Bad Romance', 'Lady Gaga', 294 * SECOND, score=100)]
        decision = self.decide(Source.METADATA, tracks, loaded_metadata(GAGA_FILENAME, GAGA_LENGTH), GAGA_FILENAME)
        self.assertEqual(Outcome.NEEDS_REVIEW, decision.outcome)


# Metadata lookup after an AcoustID review: the file's artist/title may find
# the recording the fingerprint did not propose, but never overrule it.


class ReviewLookupPolicyTest(SmartScanPolicyTestCase):
    BSB: ClassVar[dict[str, str]] = dict(title='Larger Than Life', artist='Backstreet Boys')

    def conflict(self):
        # Strong fingerprint for a different song than the file names.
        return self.decide(
            Source.ACOUSTID,
            [acoustid_recording('halo', 'Halo', 'Beyoncé', 232 * SECOND, 0.98)],
            file_metadata(232 * SECOND, **self.BSB),
        )

    def text_search(self, *tracks, error=None):
        return self.decide(Source.METADATA, list(tracks), file_metadata(232 * SECOND, **self.BSB), error=error)

    def test_lookup_runs_after_review_only_for_an_identity_disagreement(self):
        hints = file_hints(file_metadata(232 * SECOND, **self.BSB))
        self.assertTrue(smartscan.metadata_lookup_useful(self.conflict(), hints))
        pink = self.decide(Source.ACOUSTID, pink_acoustid_recordings(), file_metadata(PINK_LENGTH), PINK_FILENAME)
        self.assertEqual((Reason.LENGTH_MISMATCH,), pink.reasons)
        self.assertFalse(smartscan.metadata_lookup_useful(pink, file_hints(file_metadata(PINK_LENGTH), PINK_FILENAME)))
        caption = 'Something completely different from the song.mp3'
        unnamed = self.decide(
            Source.ACOUSTID,
            [acoustid_recording('halo', 'Halo', 'Beyoncé', 232 * SECOND, 0.98)],
            loaded_metadata(caption, 232 * SECOND),
            caption,
        )
        self.assertEqual(Outcome.NEEDS_REVIEW, unnamed.outcome)
        self.assertFalse(smartscan.metadata_lookup_useful(unnamed, file_hints(loaded_metadata(caption), caption)))
        # Unchanged: a fingerprint without a usable result is always followed by one lookup.
        for outcome in (Outcome.NO_MATCH, Outcome.ERROR):
            self.assertTrue(smartscan.metadata_lookup_useful(smartscan.Decision(outcome), smartscan.FileHints()))
        self.assertFalse(smartscan.metadata_lookup_useful(smartscan.Decision(Outcome.MATCHED), hints))

    def test_text_match_never_overrides_a_fingerprint_conflict(self):
        conflict = self.conflict()
        text = self.text_search(recording('bsb', 'Larger Than Life', 'Backstreet Boys', 232 * SECOND))
        self.assertEqual(Outcome.MATCHED, text.outcome)
        final = combine(conflict, text, self.thresholds)
        self.assertEqual((Outcome.NEEDS_REVIEW, Source.ACOUSTID), (final.outcome, final.source))
        self.assertEqual(conflict.reasons + (Reason.SOURCE_DISAGREEMENT,), final.reasons)
        self.assertIsNone(final.match)
        self.assertEqual(['halo', 'bsb'], [c.recording_id for c in final.candidates])

    def test_ambiguous_text_results_stay_in_review_with_alternatives(self):
        text = self.text_search(
            recording('bsb-1', 'Larger Than Life', 'Backstreet Boys', 232 * SECOND),
            recording('bsb-2', 'Larger Than Life', 'Backstreet Boys', 233 * SECOND),
        )
        self.assertEqual((Reason.CLOSE_SCORES,), text.reasons)
        final = combine(self.conflict(), text, self.thresholds)
        self.assertEqual(Outcome.NEEDS_REVIEW, final.outcome)
        self.assertIn(Reason.SOURCE_DISAGREEMENT, final.reasons)
        self.assertEqual(['halo', 'bsb-1', 'bsb-2'], [c.recording_id for c in final.candidates])

    def test_same_recording_from_both_stages_is_listed_once(self):
        acoustid = smartscan.Decision(Outcome.NEEDS_REVIEW, Source.ACOUSTID, reasons=(Reason.TITLE_CONFLICT,))
        acoustid.candidates = self.conflict().candidates
        text = self.text_search(recording('halo', 'Halo', 'Beyoncé', 232 * SECOND))
        final = combine(acoustid, text, self.thresholds)
        self.assertEqual(['halo'], [c.recording_id for c in final.candidates])
        self.assertEqual(Source.ACOUSTID, final.candidates[0].source)
        self.assertNotIn(Reason.SOURCE_DISAGREEMENT, final.reasons)

    def test_failed_or_cancelled_text_search_keeps_the_fingerprint_review(self):
        conflict = self.conflict()
        failed = self.text_search(error=QNetworkReply.NetworkError.ServiceUnavailableError)
        final = combine(conflict, failed, self.thresholds)
        self.assertEqual((Outcome.NEEDS_REVIEW, conflict.reasons), (final.outcome, final.reasons))
        cancelled = smartscan.Decision(Outcome.CANCELLED, Source.METADATA)
        self.assertEqual(Outcome.CANCELLED, combine(conflict, cancelled, self.thresholds).outcome)


class SmartScanFileTestCase(PicardTestCase):
    """The Smart Scan state machine on a File, with mocked services only."""

    def setUp(self):
        super().setUp()
        self.set_config_values(BASE_SETTINGS)
        self.patch_tagger_instance('picard.item')
        for name in (
            'window',
            'acoustidmanager',
            'isrc_submit_manager',
            'webservice',
            '_acoustid',
            'mb_api',
            'move_file_to_track',
            'move_file_to_nat',
        ):
            setattr(self.tagger, name, MagicMock())
        self.tasks = []

        def find_tracks(*args, **kwargs):
            self.tasks.append(object())
            return self.tasks[-1]

        self.tagger.mb_api.find_tracks.side_effect = find_tracks
        self.tagger.get_release_group_by_id = MagicMock(return_value=MagicMock(loaded_albums=set()))
        save = patch.object(File, 'save')
        self.save = save.start()
        self.addCleanup(save.stop)
        self.use_file('Backstreet Boys - Larger Than Life.mp3', title='Larger Than Life', artist='Backstreet Boys')

    def tearDown(self):
        self.save.assert_not_called()
        super().tearDown()

    def use_file(self, name, length=232 * SECOND, **tags):
        self.file = File('/music/' + name)
        self.file.state = File.State.NORMAL
        self.file.orig_metadata = loaded_metadata(name, length, **tags)
        self.file.metadata = Metadata(self.file.orig_metadata)

    def start(self):
        self.file.start_smart_scan()
        return self.tagger._acoustid.analyze.call_args.args[1]

    def lookup_reply(self, *tracks, call=None):
        call = call or self.tagger.mb_api.find_tracks.call_args
        call.args[0]({'recordings': list(tracks)}, None, None)

    def halo(self):
        return {'recordings': [acoustid_recording('halo', 'Halo', 'Beyoncé', 232 * SECOND, 0.98)]}

    def suggested(self):
        return {tag: (s.value, s.provenance) for tag, s in self.file.dlmtags_tag_suggestions.items()}

    def credit_lookup(self):
        call = self.tagger.mb_api.get_track_by_id.call_args
        return call.args[0], call.args[1]


class ReviewLookupFileTest(SmartScanFileTestCase):
    def test_fingerprint_disagreement_searches_the_files_artist_and_title(self):
        before = Metadata(self.file.metadata)
        self.start()(self.halo(), None, None)
        self.assertEqual('lookup', self.file.smart_scan_stage)
        kwargs = self.tagger.mb_api.find_tracks.call_args.kwargs
        self.assertEqual(('Larger Than Life', 'Backstreet Boys'), (kwargs['track'], kwargs['artist']))
        self.lookup_reply(recording('bsb', 'Larger Than Life', 'Backstreet Boys', 232 * SECOND, BSB_RELEASES))
        decision = self.file.smart_scan_decision
        self.assertEqual(Outcome.NEEDS_REVIEW, decision.outcome)
        self.assertIn(Reason.SOURCE_DISAGREEMENT, decision.reasons)
        self.assertEqual({'halo', 'bsb'}, {c.recording_id for c in decision.candidates})
        self.assertEqual(before, self.file.metadata)
        self.tagger.move_file_to_track.assert_not_called()
        self.tagger.move_file_to_nat.assert_not_called()
        # Embedded tags are already New Tags: nothing to suggest.
        self.assertEqual({}, self.suggested())

    def test_missing_fingerprint_uses_the_file_name(self):
        self.use_file('Backstreet Boys - Larger Than Life.mp3')
        self.start()({}, None, None)  # fpcalc or AcoustID failed
        kwargs = self.tagger.mb_api.find_tracks.call_args.kwargs
        self.assertEqual(('Larger Than Life', 'Backstreet Boys'), (kwargs['track'], kwargs['artist']))
        self.lookup_reply(recording('bsb', 'Larger Than Life', 'Backstreet Boys', 232 * SECOND, BSB_RELEASES))
        self.assertEqual(Outcome.MATCHED, self.file.smart_scan_decision.outcome)
        self.assertEqual('Backstreet Boys', self.file.metadata['artist'])

    def test_wrong_file_name_is_not_matched_by_text_search(self):
        # The file name is wrong; the fingerprint is right. Neither wins automatically.
        self.use_file('Beyoncé - Larger Than Life.mp3')
        self.start()(self.halo(), None, None)
        self.lookup_reply(recording('other', 'Larger Than Life', 'Beyoncé', 232 * SECOND))
        self.assertEqual(Outcome.NEEDS_REVIEW, self.file.smart_scan_decision.outcome)
        self.assertEqual('', self.file.metadata['artist'])
        # The fingerprint contradicts the text: only the file name is offered.
        self.assertEqual(
            {'artist': ('Beyoncé', 'Filename'), 'title': ('Larger Than Life', 'Filename')},
            self.suggested(),
        )

    def test_duration_conflict_does_not_search_and_offers_unverified_file_name_values(self):
        self.use_file(PINK_FILENAME, PINK_LENGTH)
        before = Metadata(self.file.metadata)
        self.start()({'recordings': pink_acoustid_recordings()}, None, None)
        decision = self.file.smart_scan_decision
        self.assertEqual((Outcome.NEEDS_REVIEW, (Reason.LENGTH_MISMATCH,)), (decision.outcome, decision.reasons))
        self.tagger.mb_api.find_tracks.assert_not_called()
        self.assertEqual(before, self.file.metadata)
        self.assertEqual(tuple(before.getall('title')), self.file.dlmtags_tag_suggestions['title'].values_then)

    def test_caption_without_artist_and_title_is_not_searched_after_review(self):
        self.use_file('Something completely different from the song.mp3')
        self.start()(self.halo(), None, None)
        self.assertEqual(Outcome.NEEDS_REVIEW, self.file.smart_scan_decision.outcome)
        self.tagger.mb_api.find_tracks.assert_not_called()

    def test_cancel_during_the_review_lookup_aborts_and_ignores_the_late_reply(self):
        self.start()(self.halo(), None, None)
        call = self.tagger.mb_api.find_tracks.call_args
        self.file.clear_lookup_task()
        self.tagger.webservice.abort_task.assert_called_once_with(self.tasks[0])
        self.assertEqual(Outcome.CANCELLED, self.file.smart_scan_decision.outcome)
        self.lookup_reply(recording('bsb', 'Larger Than Life', 'Backstreet Boys', 232 * SECOND), call=call)
        self.assertEqual(Outcome.CANCELLED, self.file.smart_scan_decision.outcome)
        self.assertIsNone(self.file.smart_scan_stage)
        self.start()
        self.assertEqual(2, self.tagger._acoustid.analyze.call_count)

    def test_late_replies_of_a_previous_scan_are_ignored(self):
        first_acoustid = self.start()
        first_acoustid(self.halo(), None, None)
        first_lookup = self.tagger.mb_api.find_tracks.call_args
        self.file.clear_lookup_task()
        second = self.start()
        second(
            {
                'recordings': [
                    acoustid_recording(
                        'bsb',
                        'Larger Than Life',
                        'Backstreet Boys',
                        232 * SECOND,
                        0.95,
                        BSB_RELEASES,
                    )
                ]
            },
            None,
            None,
        )
        decision = self.file.smart_scan_decision
        self.assertEqual(Outcome.MATCHED, decision.outcome)
        self.lookup_reply(recording('x', 'Larger Than Life', 'Backstreet Boys', 232 * SECOND), call=first_lookup)
        first_acoustid(self.halo(), None, None)
        self.assertIs(decision, self.file.smart_scan_decision)
        self.assertEqual({}, self.suggested())

    def test_suggestions_are_replaced_by_a_new_scan(self):
        self.use_file(PINK_FILENAME, PINK_LENGTH)
        self.start()({'recordings': pink_acoustid_recordings()}, None, None)
        self.assertTrue(self.suggested())
        self.file.start_smart_scan()
        self.assertEqual({}, self.suggested())


class TagSuggestionFileTest(SmartScanFileTestCase):
    """Unverified Artist/Title suggestions after Smart Scan; never applied."""

    def test_pink_suggests_canonical_title_and_musicbrainz_artist_credit(self):
        self.use_file(PINK_FILENAME, PINK_LENGTH)
        before = Metadata(self.file.metadata)
        self.start()({'recordings': pink_acoustid_recordings()}, None, None)
        decision = self.file.smart_scan_decision
        self.assertEqual((Outcome.NEEDS_REVIEW, (Reason.LENGTH_MISMATCH,)), (decision.outcome, decision.reasons))
        # The title is canonical at once. AcoustID drops credit join phrases,
        # so the artist credit comes from one MusicBrainz recording lookup.
        self.assertEqual(
            {'title': ('Cover Me in Sunshine', 'MusicBrainz'), 'artist': ('P NK Willow Sage Hart', 'Filename')},
            self.suggested(),
        )
        recording_id, reply = self.credit_lookup()
        self.assertEqual(PINK_RECORDING, recording_id)
        reply(pink_musicbrainz_recordings()[0], None, None)
        self.assertEqual(
            {'title': ('Cover Me in Sunshine', 'MusicBrainz'), 'artist': ('P!nk + Willow Sage Hart', 'MusicBrainz')},
            self.suggested(),
        )
        suggestions = self.file.dlmtags_tag_suggestions
        self.assertTrue(suggestions['title'].replaces_guess)
        self.assertFalse(suggestions['artist'].replaces_guess)
        self.assertEqual({PINK_RECORDING}, {s.recording_id for s in suggestions.values()})
        # Suggestions only: New Tags, the decision and the file are untouched.
        self.assertEqual(before, self.file.metadata)
        self.assertIs(decision, self.file.smart_scan_decision)
        self.tagger.mb_api.find_tracks.assert_not_called()
        self.tagger.move_file_to_nat.assert_not_called()
        self.tagger.move_file_to_track.assert_not_called()

    def test_musicbrainz_candidates_need_no_extra_request(self):
        self.use_file(PINK_FILENAME, PINK_LENGTH)
        self.start()({'recordings': []}, None, None)
        self.lookup_reply(*pink_musicbrainz_recordings())
        self.assertEqual(Outcome.NEEDS_REVIEW, self.file.smart_scan_decision.outcome)
        self.assertEqual(
            {'title': ('Cover Me in Sunshine', 'MusicBrainz'), 'artist': ('P!nk + Willow Sage Hart', 'MusicBrainz')},
            self.suggested(),
        )
        self.tagger.mb_api.get_track_by_id.assert_not_called()

    def test_incorrect_acoustid_recording_gives_no_canonical_suggestions(self):
        self.use_file('Backstreet Boys - Larger Than Life.mp3')
        self.start()(self.halo(), None, None)
        self.lookup_reply(recording('bsb', 'Larger Than Life', 'Backstreet Boys', 232 * SECOND))
        self.assertIn(Reason.SOURCE_DISAGREEMENT, self.file.smart_scan_decision.reasons)
        expected = {'artist': ('Backstreet Boys', 'Filename'), 'title': ('Larger Than Life', 'Filename')}
        self.assertEqual(expected, self.suggested())
        # Without the text result the conflicting fingerprint candidate is still not used.
        self.start()(self.halo(), None, None)
        self.lookup_reply()
        self.assertEqual(expected, self.suggested())
        self.tagger.mb_api.get_track_by_id.assert_not_called()

    def test_disagreeing_candidates_are_not_resolved(self):
        self.use_file('Artist - Song.mp3', 216 * SECOND)
        self.start()({'recordings': []}, None, None)
        self.lookup_reply(
            recording('solo', 'Song', 'Artist', 200 * SECOND),
            recording('duet', 'Song', ('Artist', 'Friend'), 200 * SECOND),
        )
        self.assertEqual(Outcome.NEEDS_REVIEW, self.file.smart_scan_decision.outcome)
        # Both recordings fit; their credits differ, their titles agree.
        self.assertEqual({'artist': ('Artist', 'Filename'), 'title': ('Song', 'MusicBrainz')}, self.suggested())
        self.start()({'recordings': []}, None, None)
        self.lookup_reply(
            recording('a', 'Song', 'Artist', 200 * SECOND),
            recording('b', 'Song (2011 Remaster)', 'Artist', 200 * SECOND),
        )
        self.assertEqual({'artist': ('Artist', 'MusicBrainz'), 'title': ('Song', 'Filename')}, self.suggested())
        self.tagger.mb_api.get_track_by_id.assert_not_called()

    def test_file_name_suggestions_without_musicbrainz_candidates(self):
        self.use_file('Backstreet Boys - Larger Than Life.mp3')
        self.start()({'recordings': []}, None, None)
        self.lookup_reply()
        self.assertEqual(Outcome.NO_MATCH, self.file.smart_scan_decision.outcome)
        self.assertEqual(
            {'artist': ('Backstreet Boys', 'Filename'), 'title': ('Larger Than Life', 'Filename')},
            self.suggested(),
        )

    def test_embedded_tags_take_priority(self):
        self.use_file(PINK_FILENAME, PINK_LENGTH, artist='P!nk', title='Cover Me in Sunshine')
        self.start()({'recordings': pink_acoustid_recordings()}, None, None)
        self.assertEqual(Outcome.NEEDS_REVIEW, self.file.smart_scan_decision.outcome)
        self.assertEqual({}, self.suggested())
        self.tagger.mb_api.get_track_by_id.assert_not_called()

    def test_manual_edits_take_priority(self):
        self.use_file(PINK_FILENAME, PINK_LENGTH)
        self.file.metadata['title'] = 'My Title'  # edited before Scan
        self.start()({'recordings': pink_acoustid_recordings()}, None, None)
        self.lookup_reply()
        self.assertNotIn('title', self.suggested())
        self.assertEqual('My Title', self.file.metadata['title'])

    def test_artist_entered_while_the_credit_lookup_runs_is_kept(self):
        self.use_file(PINK_FILENAME, PINK_LENGTH)
        self.start()({'recordings': pink_acoustid_recordings()}, None, None)
        _recording_id, reply = self.credit_lookup()
        self.file.metadata['artist'] = 'Mine'
        reply(pink_musicbrainz_recordings()[0], None, None)
        self.assertNotIn('artist', self.suggested())
        self.assertEqual('Mine', self.file.metadata['artist'])

    def test_new_scan_aborts_the_credit_lookup_and_ignores_its_late_reply(self):
        self.use_file(PINK_FILENAME, PINK_LENGTH)
        self.start()({'recordings': pink_acoustid_recordings()}, None, None)
        task = self.tagger.mb_api.get_track_by_id.return_value
        _recording_id, reply = self.credit_lookup()
        self.file.start_smart_scan()
        self.tagger.webservice.abort_task.assert_called_once_with(task)
        reply(pink_musicbrainz_recordings()[0], None, None)
        self.assertEqual({}, self.suggested())

    def test_removal_aborts_the_credit_lookup(self):
        self.use_file(PINK_FILENAME, PINK_LENGTH)
        self.start()({'recordings': pink_acoustid_recordings()}, None, None)
        task = self.tagger.mb_api.get_track_by_id.return_value
        self.file.clear_lookup_task()
        self.tagger.webservice.abort_task.assert_any_call(task)

    def test_failed_or_mismatched_credit_reply_keeps_the_file_name_artist(self):
        self.use_file(PINK_FILENAME, PINK_LENGTH)
        self.start()({'recordings': pink_acoustid_recordings()}, None, None)
        _recording_id, reply = self.credit_lookup()
        reply({'id': 'another-recording', 'title': 'X', 'artist-credit': credit('Somebody')}, None, None)
        self.assertEqual(('P NK Willow Sage Hart', 'Filename'), self.suggested()['artist'])
        reply(b'', None, QNetworkReply.NetworkError.ServiceUnavailableError)
        self.assertEqual(('P NK Willow Sage Hart', 'Filename'), self.suggested()['artist'])


class ConfirmResultsTest(SmartScanFileTestCase):
    """User confirmation of a reviewed recording, then the remaining tags."""

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
                dlmtags_discogs_enabled=False,
                dlmtags_discogs_token='',
            )
        )
        self.tagger.albums = {}

    def pink_review(self):
        self.use_file(PINK_FILENAME, PINK_LENGTH)
        self.start()({'recordings': pink_acoustid_recordings()}, None, None)
        _recording_id, reply = self.credit_lookup()
        reply(pink_musicbrainz_recordings()[0], None, None)
        decision = self.file.smart_scan_decision
        self.assertEqual((Outcome.NEEDS_REVIEW, (Reason.LENGTH_MISMATCH,)), (decision.outcome, decision.reasons))
        return decision

    def lookups(self, inc):
        return [c for c in self.tagger.mb_api.get_track_by_id.call_args_list if set(c.kwargs.get('inc') or ()) == inc]

    def genre_lookups(self):
        genre_incs = {'genres', 'tags', 'user-genres', 'user-tags'}
        calls = self.tagger.mb_api.get_track_by_id.call_args_list
        return [c for c in calls if genre_incs & set(c.kwargs.get('inc') or ())]

    def genre_reply(self, document=None, error=None):
        call = self.genre_lookups()[-1]
        call.args[1](document if document is not None else {'id': call.args[0]}, None, error)

    def test_pink_confirmation_applies_canonical_artist_and_title(self):
        review = self.pink_review()
        recordings = self.file.confirmable_recordings()
        self.assertEqual(
            [(PINK_RECORDING, 'P!nk + Willow Sage Hart', 'Cover Me in Sunshine', (Reason.LENGTH_MISMATCH,), True)],
            [(r.recording_id, r.artist, r.title, r.reasons, r.plausible) for r in recordings],
        )
        self.assertEqual(PINK_RECORDING, smartscan.preselected_recording(recordings))
        self.assertTrue(self.file.confirm_recording(review, PINK_RECORDING))
        decision = self.file.smart_scan_decision
        self.assertEqual(
            (Outcome.MATCHED, True, PINK_RECORDING),
            (decision.outcome, decision.user_confirmed, decision.recording_id),
        )
        # The duration conflict is kept, not resolved.
        self.assertIn(Reason.LENGTH_MISMATCH, decision.notes)
        self.assertTrue(decision.describe().startswith('Confirmed by user'))
        metadata = self.file.metadata
        self.assertEqual(
            ('P!nk + Willow Sage Hart', 'Cover Me in Sunshine', ''),
            (metadata['artist'], metadata['title'], metadata['musicbrainz_recordingid']),
        )
        self.assertEqual('MusicBrainz', source_for(self.file, 'artist'))
        self.assertFalse({'artist', 'title'} & set(self.suggested()))
        self.tagger.move_file_to_nat.assert_not_called()
        self.tagger.move_file_to_track.assert_not_called()

    def test_confirmed_four_field_review_applies_only_explicit_choices(self):
        review = self.pink_review()
        before = {tag: tuple(self.file.metadata.getall(tag)) for tag in ('date', 'genre')}
        original_title = self.file.metadata.getall('title')
        self.assertFalse(self.file.confirm_recording(review, PINK_RECORDING, approved_identity_tags={'album'}))
        self.assertIs(review, self.file.smart_scan_decision)
        self.assertTrue(self.file.confirm_recording(review, PINK_RECORDING, approved_identity_tags={'artist'}))
        self.assertEqual(original_title, self.file.metadata.getall('title'))
        self.assertEqual('P!nk + Willow Sage Hart', self.file.metadata['artist'])
        decision = self.file.smart_scan_decision
        self.genre_reply({'id': PINK_RECORDING, 'genres': [{'name': 'pop', 'count': 4}]})
        self.assertFalse(self.file.apply_confirmed_suggestions(decision, {'album'}, before))
        self.assertEqual(before['date'], tuple(self.file.metadata.getall('date')))
        self.assertEqual(before['genre'], tuple(self.file.metadata.getall('genre')))
        self.assertTrue(self.file.apply_confirmed_suggestions(decision, {'date', 'genre'}, before))
        self.assertEqual('2021-02-12', self.file.metadata['date'])
        self.assertEqual(['Pop'], self.file.metadata.getall('genre'))

    def test_confirmation_application_rejects_unrelated_recording_tags(self):
        review = self.pink_review()
        self.file.metadata['album'] = 'Keep album'
        self.file.metadata['comment'] = 'Keep comment'
        self.file.metadata['date'] = '2000'
        self.file.metadata['genre'] = 'Keep genre'
        self.file.metadata.images.append(create_image(b'own', types=['front']))
        before_images = self.file.metadata.images.copy()

        def polluted_recording(_track, metadata):
            metadata['artist'] = 'Verified artist'
            metadata['title'] = 'Verified title'
            metadata['album'] = 'Unexpected album'
            metadata['comment'] = 'Unexpected comment'
            metadata['date'] = '2030'
            metadata['genre'] = 'Unexpected genre'
            metadata['musicbrainz_recordingid'] = PINK_RECORDING

        with patch('picard.file.recording_to_metadata', side_effect=polluted_recording):
            self.assertTrue(self.file.confirm_recording(review, PINK_RECORDING))
        self.assertEqual(['Keep album'], self.file.metadata.getall('album'))
        self.assertEqual(['Keep comment'], self.file.metadata.getall('comment'))
        self.assertEqual(['2000'], self.file.metadata.getall('date'))
        self.assertEqual(['Keep genre'], self.file.metadata.getall('genre'))
        self.assertEqual('', self.file.metadata['musicbrainz_recordingid'])
        self.assertEqual(before_images, self.file.metadata.images)
        self.assertEqual('Verified artist', self.file.metadata['artist'])
        self.assertEqual('Verified title', self.file.metadata['title'])
        self.tagger.move_file_to_nat.assert_not_called()
        self.tagger.move_file_to_track.assert_not_called()

    def test_no_repeated_discovery_or_credit_lookup(self):
        review = self.pink_review()
        self.file.confirm_recording(review, PINK_RECORDING)
        self.tagger.mb_api.find_tracks.assert_not_called()
        self.assertEqual(1, self.tagger._acoustid.analyze.call_count)
        self.assertEqual(1, len(self.lookups({'artist-credits'})))

    def test_only_a_listed_recording_can_be_confirmed(self):
        review = self.pink_review()
        before = Metadata(self.file.metadata)
        self.assertFalse(self.file.confirm_recording(review, 'not-a-candidate'))
        self.assertIs(review, self.file.smart_scan_decision)
        self.assertEqual(before, self.file.metadata)

    def test_multiple_plausible_recordings_need_a_choice(self):
        self.use_file('Artist - Song.mp3', 216 * SECOND)
        self.start()({'recordings': []}, None, None)
        self.lookup_reply(
            recording('solo', 'Song', 'Artist', 200 * SECOND),
            recording('duet', 'Song', ('Artist', 'Friend'), 200 * SECOND),
        )
        review = self.file.smart_scan_decision
        recordings = self.file.confirmable_recordings()
        self.assertEqual({'solo', 'duet'}, {r.recording_id for r in recordings if r.plausible})
        self.assertIsNone(smartscan.preselected_recording(recordings))
        self.assertTrue(self.file.confirm_recording(review, 'duet'))
        self.assertEqual(
            ('Artist; Friend', 'duet'),
            (self.file.metadata['artist'], self.file.smart_scan_decision.recording_id),
        )

    def test_manual_edits_are_kept_unless_replacing_is_approved(self):
        review = self.pink_review()
        self.file.metadata['title'] = 'My Title'  # edited after Scan
        self.assertEqual(['title'], self.file.manual_identity_edits())
        self.assertTrue(self.file.confirm_recording(review, PINK_RECORDING))
        self.assertEqual(
            ('P!nk + Willow Sage Hart', 'My Title'),
            (self.file.metadata['artist'], self.file.metadata['title']),
        )
        review = self.pink_review()
        self.file.metadata['title'] = 'My Title'
        self.assertTrue(self.file.confirm_recording(review, PINK_RECORDING, replace_manual=True))
        self.assertEqual('Cover Me in Sunshine', self.file.metadata['title'])

    def test_uncertain_release_is_not_selected_and_album_stays_a_suggestion(self):
        review = self.pink_review()
        self.file.confirm_recording(review, PINK_RECORDING)
        decision = self.file.smart_scan_decision
        # The AcoustID releases include a live setlist album; nothing is chosen.
        self.assertIsNone(decision.release)
        self.assertFalse(decision.release_verified)
        self.assertEqual('', self.file.metadata['album'])
        browse = self.tagger.mb_api.browse_releases.call_args
        self.assertEqual(PINK_RECORDING, browse.kwargs['recording'])
        browse.args[0](
            {
                'releases': [
                    release(
                        'single', 'Cover Me in Sunshine', '2021-02-12', primary='Single', status='Official', tracks=1
                    ),
                    release('setlist', 'All I Know So Far: Setlist', '2021', secondary=['Live'], status='Official'),
                ],
                'release-count': 2,
            },
            None,
            None,
        )
        self.assertEqual(
            {'Cover Me in Sunshine', 'All I Know So Far: Setlist'},
            {c.values[0] for c in self.file.dlmtags_album_suggestions},
        )
        self.assertEqual('', self.file.metadata['album'])
        # The genre fallback never reads an unverified release.
        self.genre_reply()
        self.tagger.mb_api.get_release_by_id.assert_not_called()

    def test_year_and_genre_are_suggestions_with_provenance(self):
        review = self.pink_review()
        self.file.confirm_recording(review, PINK_RECORDING)
        self.genre_reply({'id': PINK_RECORDING, 'genres': [{'name': 'pop', 'count': 4}]})
        suggested = self.suggested()
        self.assertEqual(('2021-02-12', 'MusicBrainz'), suggested['date'])
        self.assertEqual('MusicBrainz recording', suggested['genre'][1])
        self.assertIn('pop', suggested['genre'][0].casefold())
        self.assertEqual(('', ''), (self.file.metadata['date'], self.file.metadata['genre']))
        self.assertEqual(PINK_RECORDING, recording_for_artwork(self.file))

    def test_discogs_genre_cannot_suggest_style_after_confirmation(self):
        self.set_config_values({'dlmtags_discogs_enabled': True, 'dlmtags_discogs_token': 'test-token'})
        review = self.pink_review()
        with patch('picard.smartgenre.DiscogsGenreRequest') as factory:
            factory.return_value.styles = ['Ballad']
            self.file.confirm_recording(review, PINK_RECORDING)
            self.genre_reply()
            factory.return_value.finished.connect.call_args.args[0](['Pop'], 'https://www.discogs.com/release/1')
        suggested = self.suggested()
        self.assertNotIn('style', suggested)
        self.assertEqual('Discogs verified single-track release', suggested['genre'][1])
        self.assertEqual(('', ''), (self.file.metadata['genre'], self.file.metadata['style']))

    def test_network_errors_and_missing_results_suggest_nothing(self):
        review = self.pink_review()
        self.file.confirm_recording(review, PINK_RECORDING)
        self.genre_reply(b'', QNetworkReply.NetworkError.ServiceUnavailableError)
        self.assertNotIn('genre', self.suggested())
        self.tagger.mb_api.browse_releases.call_args.args[0](
            b'',
            None,
            QNetworkReply.NetworkError.ServiceUnavailableError,
        )
        self.assertEqual([], self.file.dlmtags_album_suggestions)
        self.assertEqual('', self.file.metadata['album'])

    def test_artist_credit_must_be_known_before_confirming(self):
        self.use_file(PINK_FILENAME, PINK_LENGTH)
        self.start()({'recordings': pink_acoustid_recordings()}, None, None)
        _recording_id, reply = self.credit_lookup()
        reply(b'', None, QNetworkReply.NetworkError.ServiceUnavailableError)
        review = self.file.smart_scan_decision
        (recording_,) = self.file.confirmable_recordings()
        self.assertEqual('', recording_.artist)
        self.assertFalse(recording_.confirmable)
        self.assertFalse(self.file.confirm_recording(review, PINK_RECORDING))

    def test_stale_and_repeated_confirmations_are_refused(self):
        review = self.pink_review()
        self.file.start_smart_scan()
        self.assertFalse(self.file.confirm_recording(review, PINK_RECORDING))  # a new scan is running
        self.start()({'recordings': pink_acoustid_recordings()}, None, None)
        self.assertFalse(self.file.confirm_recording(review, PINK_RECORDING))  # an older scan's review
        review = self.file.smart_scan_decision
        self.credit_lookup()[1](pink_musicbrainz_recordings()[0], None, None)
        self.assertTrue(self.file.confirm_recording(review, PINK_RECORDING))
        confirmed = self.file.smart_scan_decision
        self.assertFalse(self.file.confirm_recording(review, PINK_RECORDING))
        self.assertIs(confirmed, self.file.smart_scan_decision)

    def test_late_genre_reply_after_a_new_scan_is_ignored(self):
        review = self.pink_review()
        self.file.confirm_recording(review, PINK_RECORDING)
        call = self.genre_lookups()[-1]
        self.file.start_smart_scan()
        call.args[1]({'id': PINK_RECORDING, 'genres': [{'name': 'pop', 'count': 4}]}, None, None)
        self.assertEqual({}, self.suggested())

    def test_confirmation_is_per_file(self):
        review = self.pink_review()
        first = self.file
        self.use_file('Other - Song.mp3')
        self.assertFalse(self.file.confirm_recording(review, PINK_RECORDING))
        self.assertTrue(first.confirm_recording(review, PINK_RECORDING))
        self.assertEqual('', self.file.metadata['artist'])
