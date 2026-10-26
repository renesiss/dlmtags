# Picard, the next-generation MusicBrainz tagger
# SPDX-License-Identifier: GPL-2.0-or-later

"""Album suggestions of a Smart Scan and the shared status vocabulary.

The suggestions are real Find Tag candidates. They are only previewed in New
Tags; the album is chosen through the Find Tag dialog.
"""

from picard.i18n import (
    N_,
    gettext as _,
    ngettext,
)
from picard.tagprovenance import provider_name


# Unconfirmed values (album or artwork) that still need the user's choice.
SUGGESTED_COLOR = '#F0AD4E'


class Status:
    VERIFIED = 'verified'
    SUGGESTED = 'suggested'
    NOT_FOUND = 'not_found'
    ERROR = 'error'
    SEARCHING = 'searching'
    # Chosen by the user: now part of New Tags, written only by Save.
    CHOSEN = 'chosen'


# Text (and a symbol) for every state, so colour is never the only cue.
STATUS_TEXT = {
    Status.VERIFIED: N_('✓ Verified'),
    Status.SUGGESTED: N_('★ Suggested'),
    Status.NOT_FOUND: N_('Not Found'),
    Status.ERROR: N_('⚠ Error'),
    Status.SEARCHING: N_('Searching…'),
    Status.CHOSEN: N_('✎ In New Tags'),
}


def status_text(status):
    return _(STATUS_TEXT[status])


def album_suggestion(file):
    """The top unconfirmed album candidate of the file's last Smart Scan, if any."""
    decision = getattr(file, 'smart_scan_decision', None)
    suggestions = getattr(file, 'dlmtags_album_suggestions', None)
    if decision is None or decision.release_verified or not suggestions:
        return None
    return suggestions[0]


# Provenance of an unverified Artist/Title suggestion (picard.smartscan.TagSuggestion).
PROVENANCE_TEXT = {
    'MusicBrainz': N_('MusicBrainz — Unverified'),
    'Filename': N_('Filename — Unverified'),
}


def tag_suggestion(file, tag):
    """The unverified Artist/Title suggestion of the last Smart Scan, while the
    field still holds the values it had then."""
    suggestion = getattr(file, 'dlmtags_tag_suggestions', {}).get(tag)
    if suggestion is None or tuple(file.metadata.getall(tag)) != suggestion.values_then:
        return None
    return suggestion


def provenance_text(suggestion):
    text = PROVENANCE_TEXT.get(suggestion.provenance)
    return _(text) if text else _('%s — Unverified') % provider_name(suggestion.provenance)


def album_search_status(file):
    """(Status, message) of the file's album suggestion search, or None if none ran."""
    request = getattr(file, 'dlmtags_album_search', None)
    if request is None:
        return None
    if request.active:
        return Status.SEARCHING, _('Searching MusicBrainz, Discogs and Last.fm…')
    if request.state == 'applied' and request.current():
        return Status.CHOSEN, _('The chosen album is in New Tags. Save writes it to the file.')
    if file.dlmtags_album_suggestions:
        count = len(file.dlmtags_album_suggestions)
        return Status.SUGGESTED, ngettext(
            '%d suggestion, not applied', '%d suggestions, not applied', count,
        ) % count
    if request.state == 'error':
        return Status.ERROR, request.message or _('A provider request failed.')
    if request.state in {'cancelled', 'applied'} and not request.current():
        return Status.NOT_FOUND, _('The album or recording changed. Scan again for suggestions.')
    return Status.NOT_FOUND, request.message or _('No album suggestions.')
