# Smart Genre (Phase 3)

After a confirmed Smart Scan match, DLMtags queries MusicBrainz recording
genres. If none pass Picard's genre policy, it tries existing release and
release-group data, then requests that release's genre data if needed.
Needs Review, No Match and unconfirmed recordings are never enriched.

MusicBrainz genre retrieval defaults to enabled in DLMtags. An explicitly stored
disabled preference, including a profile preference, still takes precedence.
Picard's folksonomy/personal-genre settings, filters, relative minimum usage,
maximum count, joining and multiple-value handling are reused. Common personal
tags such as "seen live", "favorites" and "awesome" are excluded before filtering.

Only an empty New Tags Genre is populated. Existing values, edits made during
retrieval and explicitly deleted genres are preserved, including during later
album metadata updates. No media files are saved.

The optional Last.fm fallback is disabled by default. The advanced configuration
setting `smart_genre_lastfm_enabled` enables it only when a `lastfm_api_key`
is already configured. No key is bundled or obtained automatically, and the key
is excluded from shared settings. There is no new credentials UI. The fallback
uses the official HTTPS [`track.getTopTags` API](https://www.last.fm/api/show/track.getTopTags)
with the confirmed recording MBID; it does not search by a guessed name or scrape websites.
It is skipped when "Use only my genres" is enabled.

Requests have a 30-second total deadline, including queue time. Cancel network
requests, removing/reverting a file, switching recording or starting another
Smart Scan invalidates late responses. A genre failure does not change the
confirmed match. The progress bar includes the enrichment lifetime and stops
after completion, cancellation or timeout.

The applied source appears in the existing Matching Results description.
Per-file `smart_genre_diagnostics` and information logs retain provider/status,
raw selected-collection result count, accepted count, filtered count and an
empty-result reason, without API keys. Filtered count is the difference between
returned entries and accepted distinct values after count validation, musical-tag
filtering, Picard filters, minimum usage, deduplication and maximum-genre limits.
Debug logs also trace request starts and ignored stale responses. Timeout logs
identify the pending provider. Last.fm and MusicBrainz responses may legitimately contain
no usable genres; in that case Genre stays empty.

Find Tag distinguishes No genres available, Filtered out, Provider not configured
and Network error (including invalid responses), plus cancellation/timeouts.
An empty MusicBrainz result and an unavailable Last.fm fallback are reported
together. Actual provider failures take precedence over filtering, which takes
precedence over unavailable fallback configuration. Invalid/suspended Last.fm
keys count as configuration failures rather than an empty tag response.

## Run Run River diagnosis

Live public MusicBrainz API inspection found empty `genres` and `tags` arrays on:

- Recording `e1007af2-c7c0-44a0-aa15-1fcf3ad017b0`
- Release `3f1bc7e6-1d16-42e9-a83d-5d76c43b41de`
- Release group `5f7e7a02-7892-476f-8b2f-3546155eecfa`

All identify Run Run River (Angels Above Me). These successful empty responses
explain the reproduced missing Genre before filtering. Older local traces do
not retain provider payloads, so they cannot establish exactly what an earlier
request returned. Regression tests verify empty recording/release/group responses
complete normally, with no lost callback or metadata write.

MusicBrainz and the optional configured Last.fm track-tags API are the connected
sources. Other sources were investigated but not added: Spotify's
[track response](https://developer.spotify.com/documentation/web-api/reference/get-track)
has no Genre field, and [AcousticBrainz](https://acousticbrainz.org/) stopped
collecting new data in 2022. Artist-wide genres are not treated as verified song
genres. No new keys, scraping, provider dependencies or browser interactions
were introduced. Last.fm coverage for this song has not been verified with a
configured key.
