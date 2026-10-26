# SPDX-License-Identifier: GPL-2.0-or-later
"""Value-bound, session-only attribution. Never stored in audio Metadata."""


def set_source(file, tag, source, url=''):
    if source == 'Manual':
        file._manually_edited_tags.add(tag)
    file.dlmtags_tag_sources[tag] = source
    file._dlmtags_source_values[tag] = tuple(file.metadata.getall(tag))
    file._dlmtags_source_recordings[tag] = file.metadata['musicbrainz_recordingid']
    file.dlmtags_tag_urls.pop(tag, None)
    if url.startswith('https://'):
        file.dlmtags_tag_urls[tag] = url


def source_for(file, tag):
    values = tuple(file.metadata.getall(tag))
    current = file._dlmtags_source_values.get(tag) == values
    if values == tuple(file.orig_metadata.getall(tag)) and not (current and file.dlmtags_tag_sources.get(tag) == 'Manual'):
        return 'Existing file' if values else ''
    if tag in file.dlmtags_tag_sources and current:
        return file.dlmtags_tag_sources[tag]
    return 'Unknown' if values else ''


def provider_names(source):
    """Short provider names; Find Tag records agreeing providers as 'A + B'."""
    names = []
    for part in source.split(' + ') if source else ():
        for name in ('MusicBrainz', 'AcoustID', 'Discogs', 'Last.fm', 'Filename', 'Manual'):
            if part.startswith(name):
                part = name
                break
        names.append(part)
    return tuple(dict.fromkeys(names))


def provider_name(source):
    return ' + '.join(provider_names(source))
