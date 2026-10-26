# Tag sources and optional API settings

New Tags shows a compact source beside each displayed field. Source records
belong to the File object, contain the exact multivalue snapshot and optional
provider URL, and never enter audio Metadata. Switching files reads that file's
own records. Unknown or changed values cannot inherit an old provider label.
Unchanged original values show Existing file. User edits show Manual, including
explicit edits to the same value. Filename imports show Filename; filename hints
used only for recognition do not claim to have supplied tags.

Confirmed recording suggestions show MusicBrainz. AcoustID is credited for
recognition in Matching Results and for an actual returned AcoustID identifier,
not as the supplier of Artist, Title or Genre. Release/track suggestions are
attributed only when their final values match the metadata captured before
plugins and scripts. Unknown transformations remain Unknown. Revert clears
session provenance. Rescanning preserves manual edits and their source records.

Matching Results lists the actual recognition route and contributing Discogs or
Last.fm fields associated with the current Recording MBID. Failed, cancelled,
filtered and unused provider responses do not count as contributors.

Find Tag displays the value, actual provider and evidence. Where available, the
source URL is shown in the candidate tooltip. Applying a choice updates only
that field and its source; closing or cancelling does neither. Stale responses
cannot overwrite the field or attribution. Links require explicit user action.

## Optional providers

Open Options → Metadata → Genres → Optional metadata providers. The controls
reuse `dlmtags_discogs_enabled`, `dlmtags_discogs_token`,
`smart_genre_lastfm_enabled` and `lastfm_api_key`. Both providers default to
disabled. An enabled provider without credentials is skipped; recognition and
MusicBrainz requests continue.

Inputs mask the credentials. Picard's configuration system in this checkout has
no supported OS credential-vault integration: credentials are stored in local
settings without encryption, excluded from shared settings/profiles. Provider
secrets are not added to logs; network display URLs redact API keys and tokens.

Automatic Genre uses MusicBrainz first, then the configured Discogs fallback and
configured Last.fm fallback. Existing or manually edited Genre is preserved.
Discogs requires a verified single-track release with matching artist/title.
Its `genres` supply Genre and its separate `styles` supply Style. Automatic Style
is filled only when empty and not manually edited/deleted. A Find Tag Genre
choice never changes Style; Style has its own field search. Picard's genre
filtering, minimum usage, maximum count and multivalue handling remain active.

Last.fm returns track tags for the recording MBID. No artist-wide genre is
presented as a verified song genre. No new credentials or dependencies are
provided, and no browser opens automatically. API coverage and strict Discogs
matching can legitimately leave fields empty.

## Verification boundary

Provider requests, settings, source labels, stale callbacks, manual overrides,
file switching and field-only application are covered by mocked regression
tests. No live authenticated Discogs or Last.fm request was made for this change.
No music files were written. Attribution is session-only and is not restored
from saved audio files or persisted sessions.

Changed implementation files: `picard/tagprovenance.py`, `file.py`, `track.py`,
`album.py`, `smartgenre.py`, `discogsgenre.py`, `tagsearch.py`,
`ui/dlmtags/tag_form.py`, `tag_editor.py`, `find_tag.py`, `workspace.py`,
`ui/metadatabox/__init__.py`, `ui/options/genres.py`,
`ui/tagsfromfilenames.py`, `webservice/__init__.py`.
Tests: `test/test_background_tag_search.py`, `test/test_smartscan.py`.
