"""Four reviewed tag proposals from Smart Scan's recording decisions."""

from dataclasses import dataclass

from picard.mbjson import (
    recording_to_metadata,
    release_to_metadata,
)
from picard.metadata import Metadata
from picard.smartscan import (
    Outcome,
    normalize_release_date,
)


@dataclass(frozen=True)
class Proposal:
    changes: dict[str, tuple[list[str], list[str]]]
    score: float


AUTO_TAGS = ('title', 'artist', 'date', 'genre')


def classify(decision):
    """Use Smart Scan's own verification and ambiguity outcomes."""
    if decision.outcome == Outcome.ERROR:
        return 'failed', decision.describe()
    if decision.outcome == Outcome.NEEDS_REVIEW:
        return 'ambiguous', decision.describe()
    if decision.outcome != Outcome.MATCHED or decision.user_confirmed:
        return 'skipped', decision.describe()
    return 'matched', decision.describe()


def propose(metadata, decision):
    """Offer only supplied recording values and verified release details."""
    suggested = Metadata()
    recording_to_metadata(decision.match.track, suggested)
    release = decision.release if decision.release_verified else None
    year = None
    if release:
        release_metadata = Metadata()
        release_to_metadata(release, release_metadata)
        if not suggested.getall('genre'):
            suggested['genre'] = release_metadata.getall('genre')
        release_date = normalize_release_date(release.get('date'))
        if len(release_date) >= 4 and release_date[:4].isdigit():
            year = release_date[:4]
    changes = {}
    for tag in AUTO_TAGS:
        if tag == 'date':
            values = [year] if year else []
        else:
            values = suggested.getall(tag)
        if not values or tag in metadata.deleted_tags:
            continue
        old = metadata.getall(tag)
        new = list(values)
        if old != new:
            changes[tag] = (old, new)
    return Proposal(changes, decision.match.similarity)


def apply(file, proposal, original):
    """Stage exactly the reviewed values through File's normal metadata path.

    Reject stale previews so edits made while scanning cannot be overwritten.
    Saving to audio remains the user's separate Picard Save action.
    """
    if set(proposal.changes) - set(AUTO_TAGS):
        return False
    for tag, (before, _) in proposal.changes.items():
        if file.metadata.getall(tag) != before or original.getall(tag) != before:
            return False
    # Stage only reviewed fields. File.update() refreshes Picard's changed
    # state and UI; its normal Save/Revert actions remain responsible for disk.
    for tag, (_, after) in proposal.changes.items():
        file.metadata[tag] = after
    file.update()
    return True
