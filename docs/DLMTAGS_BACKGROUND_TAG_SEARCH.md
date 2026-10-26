# Simplified Scan and background field search

Scan keeps the existing AcoustID/MusicBrainz recognition, thresholds and
Scan → Lookup fallback. Only a confirmed recording automatically supplies
recording metadata to New Tags. Artist/Title can appear before release loading
finishes. An uncertain release does not block recording metadata or Smart Genre;
its Album/Date/track details remain unchanged. Existing manual edits and tags
without replacement values survive subsequent metadata loading. Nothing saves
automatically.

Matching Results displays status, recognized Artist — Title and a short reason.
Only genuine recording ambiguity exposes alternative recordings. Underlying
recording/release candidates remain intact. This workflow opens no browser;
explicit Picard browser commands elsewhere remain available.

## Find Tag

Each editable New Tags field has a search button; other editable tags are
reachable through the additional-fields search menu. Select one file and click
the button. The dialog (resizable, minimum 750×500) shows **FIND <FIELD>** and
the song, one status chip per provider, a "Search within results" box, a
source filter (All / MusicBrainz / Discogs / Last.fm), result cards and a
details panel. Each card shows the value prominently, then a quiet line such as
`Single · 2024` and the providers that returned it. Selecting a card shows the
full value and every provider's evidence; links open only when clicked.

Apply changes only that field in `File.metadata` and records the providers of
the selected value(s) as its source (e.g. `MusicBrainz + Discogs`). Genre
permits multiple selections and honors Picard's join setting. Nothing is saved.
Cancel leaves the file unchanged.

| Field | MusicBrainz | Discogs | Last.fm |
| --- | --- | --- | --- |
| Artist, Title | recording (lookup or search) | — | track.getInfo |
| Album | releases containing the recording | release search (artist + track) | track.getInfo album |
| Date | recording first release + releases | release year (search) | — |
| Label, Catalog number, Barcode | releases | release search | — |
| Genre | recording; matched release / release group (album-level) | verified single-track releases | track.getTopTags |
| Style | — | verified single-track releases | — |
| Other release/track fields, ISRC, IDs, Version | as before | — | — |

All providers that support the field and are configured are queried in
parallel; Find Tag does not stop at the first provider with results. Each chip
reports Searching…, results, No results, Provider not configured, Not available
for this field, or Network error. Discogs needs the opt-in and token, Last.fm
the opt-in and API key (and is skipped for Genre with "only my genres").

Results are merged by normalized value; every supporting provider and its
evidence are kept. Compilation releases never merge with original releases.
Exact identity matches (Recording MBID, verified Discogs release, exact
artist/title) rank before loose matches, which are labelled. No confidence
percentages are shown. Discogs values outside verified releases come from an
artist + track search and are marked as such; a mismatched artist is dropped.

### Load More and limits

| Provider | Page | Bound |
| --- | --- | --- |
| MusicBrainz release browse (with MBID) | 25 releases | 150 releases |
| MusicBrainz recording search (no MBID) | 10 recordings | 50 recordings |
| Discogs search | 25 results, ≤3 release verifications per round | 4 pages |
| Last.fm | single response | no pagination |

Load More appears only while a provider reports another page and requests one
page per provider. All requests use Picard's web service with its per-host rate
limiting; the Discogs token is sent as a header, never in URLs or logs. When no
provider offers another page, the dialog says so; it never claims the
search is exhaustive.

## Asynchronous safety and limits

Every search round has a 30-second deadline; providers still pending are marked
as a network error while results already returned remain. One request runs per
file/field. Closing the dialog, changing selected files, removing a file,
starting Scan or stopping network requests cancels pending queries. Responses,
Load More and Apply verify the target field and recording context have not
changed; a manual edit during a search discards it. Applying while Load More
runs stops the remaining requests.

Automatic Genre still fills only missing Genre on confirmed matches through the
existing Smart Genre pipeline. An explicit Genre search waits for automatic
genre lookup to finish. Network integration is covered with mocked responses;
live service availability and coverage are not guaranteed.

Implementation files changed for this workflow:

- `picard/file.py`, `picard/smartgenre.py`, `picard/tagsearch.py`, `picard/tagprovenance.py`,
  `picard/webservice/api_helpers/musicbrainz.py` (search offset, browse inc)
- `picard/ui/dlmtags/find_tag.py`, `tag_form.py`, `workspace.py`
- `picard/ui/mainwindow/__init__.py`, `picard/ui/metadatabox/__init__.py`
- `test/test_background_tag_search.py`, `test/test_find_tag.py`, `test/test_smartscan.py`,
  `test/test_smartscan_recording_first.py`
- This document and `docs/DLMTAGS_RECORDING_FIRST.md`
