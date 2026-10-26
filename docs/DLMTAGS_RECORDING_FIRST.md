# Smart Scan Phase 4: recording first

Smart Scan uses Picard's existing AcoustID and MusicBrainz clients. The first
decision scores recording identity with Picard's artist, title, length and ISRC
weights, without album, track-number or release-preference weights. The actual
AcoustID score is still checked independently. Artist/title/version conflicts,
duration safeguards, weak-fingerprint fallback and ambiguity thresholds remain.
A confirmed recording identifier can distinguish otherwise identical candidates.

Source descriptions such as Official Music Video, Official Video, Lyric Video,
HD and 4K are removed from matching hints. Musical qualifiers such as live,
acoustic, remix and instrumental remain. A source label contributes no proof of
an identical recording: fingerprint, identity and duration checks still apply.

Filename normalization removes trailing bracketed YouTube IDs before replacing
underscores. A video-description suffix followed by an uploader credit is
separated from song identity; a repeated artist uploader with a video ID is also
recognized. Source descriptions appear separately in Matching Results. They
never become Title, musical-version evidence or metadata-provider attribution.

Separator-free captions are resolved per candidate using only its returned
artist credits, canonical names, sort names and known aliases. Exact compact
spelling handles lost punctuation (Rag_n_Bone_Man / Rag’n’Bone Man and P_nk /
P!nk) without a global artist-substitution table. The remaining caption is still
checked against recording title, version and duration. A meaningful unresolved
caption remains conflicting evidence. Existing explicit Artist/Title tags retain
priority. Captions without a trustworthy artist/title boundary use Picard's
plain text background Lookup, then undergo the same candidate checks. Alias
spellings absent from the returned data are not invented; ambiguous captions
or unknown uploader formats can still require review.

HTTP 503 is a temporary network error, including when Qt reports NoError. The
existing bounded retry/backoff policy is retained. After exhausted retries,
Smart Scan reports Error with HTTP status/temporary diagnostics, without
evaluating the failed payload as artist/title evidence. Cancellation takes
precedence. A failed Lookup retains weak fingerprint candidates for diagnostics
but does not turn a network failure into a recording conflict.

Release selection happens only within the selected Recording MBID. Duplicate
provider entries contribute their releases to that recording, not competing
recording votes. Existing album identifiers, barcode or credible album title take
priority. Configured release preferences remain effective. Chronology precedes
ordinary release similarity, with an official single preferred on the same date.

Without existing album evidence, automatic release verification requires an
unambiguous original official single supported by the returned data. Missing or
overlapping partial dates, equivalent editions, arbitrary compilations and
unsupported album assumptions leave the release unverified. API results are
not guaranteed to contain every release; this policy uses only returned data.

A release conflict no longer rejects an independently confirmed recording.
Unverified releases remain suggestions; Picard loads recording-only metadata.
Album, release dates, track/disc numbers and release identifiers stay unchanged,
including empty values and subsequent manual edits. Existing artwork and
protected tags survive the recording-only move. Reverting the file or assigning
another recording removes this per-recording protection.

Matching Results now shows a compact status, recognized Artist/Title and short
explanation. Alternative recordings appear only for recording ambiguity;
release candidates remain available internally. Scan and field searches do not
open browser pages. See [Background Tag Search](DLMTAGS_BACKGROUND_TAG_SEARCH.md)
for the simplified workflow and supported field sources.

Confirmed recordings still receive Phase 3 genre enrichment even when their
release is unverified. Genre source diagnostics, Scan -> Lookup fallback,
progress handling, Original/New Tags, Clusters and the theme remain in place.
Only in-memory New Tags change. There is no automatic Save.
