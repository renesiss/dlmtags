# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""Wikipedia Song Info with mocked MusicBrainz, Wikidata and Wikipedia responses."""

from types import SimpleNamespace
from unittest.mock import (
    MagicMock,
    patch,
)

from PyQt6 import (
    QtCore,
    QtWidgets,
)
from PyQt6.QtNetwork import QNetworkReply

from test.test_dlmtags_inline_suggestions import InlineTestCase
from test.test_smartscan import release

from picard.file import File
from picard.metadata import Metadata
from picard.songinfo import (
    USER_AGENT,
    SongInfoLookup,
    base_title,
    collect_links,
    excerpt,
    recording_facts,
    release_year,
)

from picard.ui.dlmtags.song_info import SongInfoPanel


SONG = {
    'title': 'Song', 'index': 1, 'fullurl': 'https://en.wikipedia.org/wiki/Song_(Artist_song)',
    'description': '2024 single by Artist',
    'extract': '"Song" is a song by Artist, released in 2024 as the lead single from The Album.\nIt was written by A.',
}
SAME_TITLE_OTHER_ARTIST = {
    'title': 'Song (Someone Else song)', 'index': 1, 'fullurl': 'https://en.wikipedia.org/wiki/Other',
    'description': '1999 song by Someone Else', 'extract': '"Song" is a song by Someone Else.',
}
ARTIST_PAGE = {
    'title': 'Artist', 'index': 1, 'fullurl': 'https://en.wikipedia.org/wiki/Artist',
    'description': 'American singer', 'extract': 'Artist is an American singer known for Song.',
}
DISAMBIGUATION = {
    'title': 'Song', 'index': 1, 'pageprops': {'disambiguation': ''},
    'description': 'Topics referred to by the same term', 'extract': 'Song may refer to: a song by Artist.',
}


def wikidata_recording(qid='Q1'):
    return {'id': 'rec', 'relations': [{
        'target-type': 'work', 'type': 'performance',
        'work': {'id': 'work', 'relations': [{
            'target-type': 'url', 'type': 'wikidata',
            'url': {'resource': 'https://www.wikidata.org/wiki/%s' % qid},
        }]},
    }]}


class SongInfoTestCase(InlineTestCase):
    def setUp(self):
        super().setUp()
        self.scan(releases=[])

    def lookup(self):
        lookup = SongInfoLookup(self.tagger, 'rec', 'Song', ['Artist'])
        self.addCleanup(lookup.deleteLater)
        lookup.start()
        return lookup

    def recording(self, document, error=None, call=None):
        call = call or self.tagger.mb_api.get_track_by_id.call_args
        call.args[1](document, None, error)

    def web(self, host, document=None, error=None, http=None):
        for call in reversed(self.tagger.webservice.get_url.call_args_list):
            if host in call.kwargs['url'].host():
                call.kwargs['handler'](document, http, error)
                return call.kwargs
        raise AssertionError('no request for ' + host)

    def wikipedia(self, *pages, error=None, http=None):
        return self.web('wikipedia.org', {'query': {'pages': list(pages)}}, error, http)


class SongInfoLookupTest(SongInfoTestCase):
    def test_musicbrainz_wikidata_wikipedia_chain(self):
        lookup = self.lookup()
        self.assertEqual(
            ('url-rels', 'work-rels', 'work-level-rels', 'artist-rels', 'genres'),
            self.tagger.mb_api.get_track_by_id.call_args.kwargs['inc'],
        )
        self.recording(wikidata_recording('Q42'))
        request = self.web('wikidata.org', {'entities': {'Q42': {'sitelinks': {'enwiki': {'title': 'Song'}}}}})
        self.assertEqual('Q42', request['unencoded_queryargs']['ids'])
        query = self.wikipedia(SONG)
        self.assertEqual('Song', query['unencoded_queryargs']['titles'])
        self.assertEqual('1', query['unencoded_queryargs']['explaintext'])
        self.assertIn('DLMtags-SongInfo', query['headers']['User-Agent'])
        self.assertEqual(USER_AGENT, query['headers']['User-Agent'])
        self.assertEqual('found', lookup.state)
        self.assertEqual('musicbrainz', lookup.article.method)
        self.assertIn('lead single', lookup.article.summary)

    def test_linked_song_page_preferred_over_other_linked_items(self):
        lookup = self.lookup()
        self.recording(wikidata_recording())
        self.web('wikidata.org', {'entities': {
            'Q1': {'sitelinks': {'enwiki': {'title': 'Song'}}},
            'Q2': {'sitelinks': {'enwiki': {'title': 'List of Song cover versions'}}},
        }})
        cover_list = {
            'title': 'List of Song cover versions', 'description': 'list of cover versions of a single',
            'extract': 'This is a list of cover versions of the song.',
        }
        self.wikipedia(cover_list, dict(SONG, index=None))
        self.assertEqual('Song', lookup.article.title)

    def test_search_fallback_verifies_title_and_artist(self):
        lookup = self.lookup()
        self.recording({'id': 'rec', 'relations': []})
        query = self.wikipedia(SAME_TITLE_OTHER_ARTIST, dict(SONG, index=2))
        self.assertEqual('search', query['unencoded_queryargs']['generator'])
        self.assertIn('"Song" Artist', query['unencoded_queryargs']['gsrsearch'])
        self.assertEqual('found', lookup.state)
        self.assertEqual('Song', lookup.article.title)
        self.assertEqual('search', lookup.article.method)

    def test_same_title_by_another_artist_is_rejected(self):
        lookup = self.lookup()
        self.recording({'id': 'rec'})
        self.wikipedia(SAME_TITLE_OTHER_ARTIST)
        self.assertEqual('not_found', lookup.state)
        self.assertIsNone(lookup.article)

    def test_artist_page_and_disambiguation_are_never_song_articles(self):
        lookup = self.lookup()
        self.recording({'id': 'rec'})
        self.wikipedia(ARTIST_PAGE, DISAMBIGUATION)
        self.assertEqual('not_found', lookup.state)

    def test_linked_page_that_is_not_a_song_falls_back_to_search(self):
        lookup = self.lookup()
        self.recording(wikidata_recording())
        self.web('wikidata.org', {'entities': {'Q1': {'sitelinks': {'enwiki': {'title': 'Artist'}}}}})
        self.wikipedia(ARTIST_PAGE)
        self.assertEqual('loading', lookup.state)
        self.wikipedia(SONG)
        self.assertEqual('found', lookup.state)
        self.assertEqual('search', lookup.article.method)

    def test_no_article(self):
        lookup = self.lookup()
        self.recording({'id': 'rec'})
        self.wikipedia()
        self.assertEqual('not_found', lookup.state)

    def test_musicbrainz_failure_still_uses_verified_search(self):
        lookup = self.lookup()
        self.recording(None, error=QNetworkReply.NetworkError.HostNotFoundError)
        self.wikipedia(SONG)
        self.assertEqual('found', lookup.state)

    def test_network_error(self):
        lookup = self.lookup()
        self.recording({'id': 'rec'})
        http = MagicMock()
        http.errorString.return_value = 'Host not found'
        self.wikipedia(error=QNetworkReply.NetworkError.HostNotFoundError, http=http)
        self.assertEqual('error', lookup.state)
        self.assertIn('Host not found', lookup.message)

    def test_timeout_aborts_and_ignores_late_reply(self):
        lookup = self.lookup()
        lookup._deadline.timeout.emit()
        self.assertEqual('error', lookup.state)
        self.assertTrue(self.tagger.webservice.abort_task.called)
        self.recording({'id': 'rec'})
        self.tagger.webservice.get_url.assert_not_called()

    def test_short_lead_is_extended_from_the_same_article_only(self):
        lookup = self.lookup()
        self.recording({'id': 'rec'})
        self.wikipedia(dict(SONG, pageid=77))
        self.assertEqual('loading', lookup.state)
        body = self.web('wikipedia.org', {'query': {'pages': [{'pageid': 77, 'title': 'Song', 'extract': (
            'Lead paragraph.\n\n== Background ==\nWritten in a day.\n'
            '== Track listing ==\n1. Song – 3:00\n== Critical reception ==\nCritics liked it.'
        )}]}})
        self.assertEqual('77', body['unencoded_queryargs']['pageids'])
        self.assertNotIn('exintro', body['unencoded_queryargs'])
        self.assertEqual('found', lookup.state)
        self.assertEqual((
            ('paragraph', 'Lead paragraph.'), ('heading', 'Background'), ('paragraph', 'Written in a day.'),
            ('heading', 'Critical reception'), ('paragraph', 'Critics liked it.'),
        ), lookup.article.blocks)
        self.assertTrue(lookup.article.shortened)  # the track listing was left out
        self.assertEqual(SONG['fullurl'], lookup.article.url)

    def test_longer_text_of_a_different_page_is_ignored(self):
        lookup = self.lookup()
        self.recording({'id': 'rec'})
        self.wikipedia(dict(SONG, pageid=77))
        self.web('wikipedia.org', {'query': {'pages': [{'pageid': 99, 'title': 'Other', 'extract': 'x. ' * 500}]}})
        self.assertEqual('found', lookup.state)
        self.assertEqual('Song', lookup.article.title)
        self.assertIn('lead single', lookup.article.summary)

    def test_failed_extension_keeps_the_lead(self):
        lookup = self.lookup()
        self.recording({'id': 'rec'})
        self.wikipedia(dict(SONG, pageid=77))
        self.web('wikipedia.org', None, QNetworkReply.NetworkError.TimeoutError, MagicMock())
        self.assertEqual('found', lookup.state)
        self.assertIn('lead single', lookup.article.summary)

    def test_long_lead_needs_no_second_request_and_is_bounded(self):
        lookup = self.lookup()
        self.recording({'id': 'rec'})
        long_lead = SONG['extract'] + '\n' + ('More about the song. ' * 300)
        requests = self.tagger.webservice.get_url.call_count
        self.wikipedia(dict(SONG, pageid=77, extract=long_lead))
        self.assertEqual(requests, self.tagger.webservice.get_url.call_count)
        self.assertEqual('found', lookup.state)
        self.assertTrue(lookup.article.shortened)
        self.assertLessEqual(len(lookup.article.summary), 2500)

    def test_recording_facts_only_what_musicbrainz_states(self):
        facts = recording_facts({
            'id': 'rec', 'first-release-date': '2024-08-16', 'length': 251000,
            'genres': [{'name': 'pop', 'count': 3}, {'name': 'soul', 'count': 5}],
            'relations': [
                {'target-type': 'artist', 'type': 'producer', 'artist': {'name': 'P One'}},
                {'target-type': 'artist', 'type': 'vocal', 'artist': {'name': 'Singer'}},
                {'target-type': 'work', 'type': 'performance', 'work': {'relations': [
                    {'target-type': 'artist', 'type': 'composer', 'artist': {'name': 'W One'}},
                    {'target-type': 'artist', 'type': 'lyricist', 'artist': {'name': 'W Two'}},
                    {'target-type': 'artist', 'type': 'composer', 'artist': {'name': 'W One'}},
                ]}},
            ],
        })
        self.assertEqual('2024-08-16', facts.release_date)  # the full date is kept
        self.assertEqual('2024', facts.year)
        self.assertEqual(('W One', 'W Two'), facts.songwriters)
        self.assertEqual(('P One',), facts.producers)
        self.assertEqual(('soul', 'pop'), facts.genres)
        self.assertEqual(251000, facts.length_ms)
        empty = recording_facts({'id': 'rec'})
        self.assertEqual(('', (), (), (), 0), (
            empty.release_date, empty.songwriters, empty.producers, empty.genres, empty.length_ms,
        ))

    def test_helpers(self):
        self.assertEqual('die with a smile', base_title('Die With a Smile (Lady Gaga song)'))
        self.assertEqual(base_title('Don’t Stop'), base_title("Don't Stop"))
        self.assertEqual((['Q1'], []), collect_links(wikidata_recording()))
        blocks, shortened = excerpt('First sentence. ' * 400)
        self.assertTrue(shortened)
        self.assertTrue(blocks[-1][1].endswith('…'))
        self.assertLessEqual(sum(len(text) for kind, text in blocks), 2410)


class SongInfoPanelTest(SongInfoTestCase):
    def panel(self):
        panel = SongInfoPanel(self.tagger, recognition_stub())
        self.addCleanup(panel.deleteLater)
        return panel

    @staticmethod
    def about(panel):
        return [
            (label.property('songInfoRole'), label.text())
            for label in panel.about.findChildren(QtWidgets.QLabel) if not label.isHidden()
        ]

    def test_found_shows_plain_text_summary_and_attribution(self):
        before = Metadata(self.file.metadata)
        images = list(self.file.metadata.images)
        panel = self.panel()
        panel.set_file(self.file)
        self.assertEqual('Loading Wikipedia…', panel.state_label.text())
        self.recording({'id': 'rec'})
        with patch('PyQt6.QtGui.QDesktopServices.openUrl') as browser, patch.object(File, 'save') as save:
            self.wikipedia(dict(SONG, extract='<b>Song</b> is a song by Artist.'))
        browser.assert_not_called()
        save.assert_not_called()
        self.assertTrue(panel.state_label.isHidden())
        self.assertEqual('Song', panel.title_label.text())
        self.assertEqual('Artist', panel.artist_label.text())
        self.assertEqual('2024 single by Artist', panel.description_label.text())
        self.assertEqual([('body', '<b>Song</b> is a song by Artist.')], self.about(panel))
        for label in panel.about.findChildren(QtWidgets.QLabel):
            self.assertEqual(QtCore.Qt.TextFormat.PlainText, label.textFormat())
        self.assertIn('Source: Wikipedia, “Song”', panel.source_label.text())
        self.assertIn('Excerpt, plain text', panel.source_label.text())
        self.assertIn('CC BY-SA 4.0', panel.source_label.text())
        self.assertFalse(panel.footer.isHidden())
        self.assertTrue(panel.source_label.openExternalLinks())
        # Informational only.
        self.assertEqual(before, self.file.metadata)
        self.assertEqual(images, list(self.file.metadata.images))

    def test_details_show_documented_fields_with_their_source(self):
        panel = self.panel()
        panel.set_file(self.file)
        self.recording({
            'id': 'rec', 'first-release-date': '2024-08-16', 'length': 251000,
            'relations': [{'target-type': 'artist', 'type': 'producer', 'artist': {'name': 'P One'}}],
        })
        self.wikipedia(SAME_TITLE_OTHER_ARTIST)
        self.assertEqual([
            ('Year', '2024', 'MusicBrainz, first release'),
            ('Producers', 'P One', 'MusicBrainz'),
            ('Duration', '4:11', 'MusicBrainz'),
        ], panel.details.rows)
        self.assertFalse(panel.details.isHidden())
        # Facts stay even without a verified article; the article does not.
        self.assertEqual('No verified Wikipedia article found', panel.state_label.text())

    def test_year_only_with_its_source_never_a_full_date(self):
        panel = self.panel()
        panel.set_file(self.file)
        self.recording({'id': 'rec', 'first-release-date': '2021-04-09'})
        self.wikipedia(dict(SONG, description='2024 single by Artist'))
        self.assertIn(('Year', '2021', 'MusicBrainz, first release'), panel.details.rows)
        self.assertNotIn('2021-04-09', [row[1] for row in panel.details.rows])

    def test_year_from_verified_article_when_musicbrainz_has_none(self):
        panel = self.panel()
        panel.set_file(self.file)
        self.recording({'id': 'rec'})
        self.wikipedia(SONG)
        self.assertIn(('Year', '2024', 'Wikipedia'), panel.details.rows)

    def test_year_dash_without_a_source_and_not_from_album_or_rejected_article(self):
        self.scan(releases=[release('hits', 'Greatest Hits', '2030', secondary=['Compilation'])])
        self.file.smart_scan_decision.release = {'id': 'hits', 'title': 'Greatest Hits', 'date': '2030'}
        before = Metadata(self.file.metadata)
        panel = self.panel()
        panel.set_file(self.file)
        self.recording({'id': 'rec'})
        # Same title, other artist: rejected, so its "1999 song" is not used.
        self.wikipedia(SAME_TITLE_OTHER_ARTIST)
        self.assertIn(('Year', '—', ''), panel.details.rows)
        self.assertEqual(before, self.file.metadata)  # tags untouched

    def test_release_year_parsing(self):
        for value, expected in (
            ('2021-04-09', '2021'), ('2021-04', '2021'), ('2021', '2021'),
            ('', ''), ('0000', ''), ('April 2021', ''), (None, ''),
        ):
            self.assertEqual(expected, release_year(value), value)

    def test_suggested_album_is_never_shown_as_a_fact(self):
        self.scan(releases=[release('lp', 'The Album', '2024')])
        self.assertTrue(self.file.dlmtags_album_suggestions)
        panel = self.panel()
        panel.set_file(self.file)
        self.recording({'id': 'rec'})
        self.assertNotIn('Album', [row[0] for row in panel.details.rows])
        self.file.smart_scan_decision.release_verified = True
        self.file.smart_scan_decision.release = {'id': 'r', 'title': 'Verified Album'}
        panel.refresh()
        self.assertIn(('Album', 'Verified Album', 'MusicBrainz, verified release'), panel.details.rows)

    def test_details_grid_uses_one_column_when_narrow(self):
        panel = self.panel()
        panel.set_file(self.file)
        self.recording({'id': 'rec', 'first-release-date': '2024', 'length': 1000,
                        'genres': [{'name': 'pop', 'count': 1}]})
        panel.details.resize(600, 100)
        panel.details._arrange()
        self.assertEqual(2, panel.details._columns)
        panel.details.resize(250, 100)
        panel.details._arrange()
        self.assertEqual(1, panel.details._columns)

    def test_open_wikipedia_opens_only_the_verified_article_on_click(self):
        panel = self.panel()
        panel.set_file(self.file)
        self.recording({'id': 'rec'})
        with patch('PyQt6.QtGui.QDesktopServices.openUrl') as browser:
            self.wikipedia(SONG)
            browser.assert_not_called()
            panel.open_button.click()
        browser.assert_called_once()
        self.assertEqual(SONG['fullurl'], browser.call_args.args[0].toString())
        self.assertFalse(panel.open_button.icon().isNull())

    def test_open_wikipedia_does_nothing_without_an_article(self):
        panel = self.panel()
        panel.set_file(self.file)
        self.recording({'id': 'rec'})
        self.wikipedia(SAME_TITLE_OTHER_ARTIST)
        with patch('PyQt6.QtGui.QDesktopServices.openUrl') as browser:
            panel.open_article()
        browser.assert_not_called()

    def test_not_found_message(self):
        panel = self.panel()
        panel.set_file(self.file)
        self.recording({'id': 'rec'})
        self.wikipedia(SAME_TITLE_OTHER_ARTIST)
        self.assertEqual('No verified Wikipedia article found', panel.state_label.text())
        self.assertTrue(panel.about.isHidden())
        self.assertTrue(panel.footer.isHidden())

    def test_switching_song_during_lookup_shows_only_the_current_song(self):
        panel = self.panel()
        panel.set_file(self.file)
        first_call = self.tagger.mb_api.get_track_by_id.call_args
        other = self.make_file('/music/Other.mp3')
        panel.set_file(other)
        self.assertIn('Scan the song', panel.state_label.text())
        self.recording({'id': 'rec'}, call=first_call)
        self.wikipedia(SONG)
        self.assertTrue(panel.about.isHidden())
        self.assertEqual([], self.about(panel))
        # The finished lookup is cached for when the first song is selected again.
        calls = self.tagger.mb_api.get_track_by_id.call_count
        panel.set_file(self.file)
        self.assertEqual(calls, self.tagger.mb_api.get_track_by_id.call_count)
        self.assertIn('lead single', self.about(panel)[0][1])

    def test_new_scan_hides_the_old_result_until_it_finishes(self):
        panel = self.panel()
        panel.set_file(self.file)
        self.recording({'id': 'rec'})
        self.wikipedia(SONG)
        self.file.clear_pending()
        self.file.start_smart_scan()
        panel.refresh()
        self.assertEqual('Scanning…', panel.state_label.text())
        self.assertTrue(panel.about.isHidden())
        self.assertTrue(panel.details.isHidden())

    def test_error_offers_retry_without_looping(self):
        panel = self.panel()
        panel.set_file(self.file)
        self.recording({'id': 'rec'})
        self.wikipedia(error=QNetworkReply.NetworkError.TimeoutError, http=MagicMock())
        self.assertTrue(panel.state_label.text().startswith('Error'))
        calls = self.tagger.mb_api.get_track_by_id.call_count
        panel.set_file(self.file)
        self.assertEqual(calls, self.tagger.mb_api.get_track_by_id.call_count)
        self.assertFalse(panel.retry_button.isHidden())
        panel.retry_button.click()
        self.assertEqual(calls + 1, self.tagger.mb_api.get_track_by_id.call_count)

    def test_multiple_or_no_selection(self):
        panel = self.panel()
        panel.set_file(None)
        self.assertIn('Select one song', panel.state_label.text())
        self.tagger.mb_api.get_track_by_id.reset_mock()
        unmatched = self.make_file('/music/Unknown.mp3')
        unmatched.smart_scan_decision = SimpleNamespace(outcome='no_match', match=None)
        panel.set_file(unmatched)
        self.tagger.mb_api.get_track_by_id.assert_not_called()


def recognition_stub():
    widget = QtWidgets.QWidget()
    widget.status_label = QtWidgets.QLabel()
    widget.details_toggle = QtWidgets.QToolButton()
    return widget
