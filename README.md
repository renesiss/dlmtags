# DLMtags

DLMtags is a free, open-source audio tagger for Linux, forked from
[MusicBrainz Picard](https://picard.musicbrainz.org). It keeps Picard's tagging
engine (MusicBrainz lookups, AcoustID fingerprinting, file formats, scripting,
plugins) and adds a new single-window UI and a simplified scan-and-tag workflow.

> **Status: early development.** DLMtags is a work in progress and has no
> releases. Expect rough edges, and back up your music library before saving
> tags with it.

## Relationship to Picard

DLMtags is based on MusicBrainz Picard, © the Picard developers and
contributors (see [AUTHORS.txt](AUTHORS.txt) and the Git history). This
migration preserves the DLMtags commit and its upstream base commit, while the
older upstream Git history remains available from the original Picard project.
DLMtags changes start after upstream commit `84862c534` (Picard `master`,
October 2026).

DLMtags is **not** affiliated with or endorsed by MetaBrainz or the Picard
project. Please report DLMtags problems in this repository, not to the Picard
issue tracker.

## What DLMtags changes

These changes are in the code, with tests in `test/`. Design notes are in
`docs/DLMTAGS_*.md`.

- **New main window layout** (`picard/ui/dlmtags/`): file-browser sidebar,
  structured tag editor, artwork and song-info panels, and reusable themed
  components that follow Picard's light/dark palette.
- **Smart Scan** (`picard/smartscan.py`): uses Picard's existing
  AcoustID/MusicBrainz recognition but looks up the recording first. A
  confirmed recording fills in Artist/Title even when the release is
  uncertain. Nothing is saved automatically.
- **Find Tag / background field search** (`picard/tagsearch.py`): searches
  in the background for values of a single field, with inline suggestions.
- **Smart Genre** (`picard/smartgenre.py`, `picard/discogsgenre.py`): suggests
  genres from MusicBrainz, plus Last.fm and Discogs if you configure them.
- **Tag sources** (`picard/tagprovenance.py`): each field shows where its
  value came from (existing file, manual, filename, MusicBrainz, …).
- **Optional API settings**: you can enter your own Last.fm API key and Discogs
  token. They are empty by default, marked non-shareable, and redacted from
  debug logs. No keys come with the code.

Not done yet: visual polish, packaging, and Windows/macOS testing. DLMtags is
developed and tested only on Linux.

## Linux setup (from source)

Requirements: Python 3.10+, Git, gettext, a C compiler, and Qt 6 runtime
libraries. For AcoustID fingerprinting, also install `fpcalc` (chromaprint).

Debian/Ubuntu system packages:

```bash
sudo apt install git python3 python3-venv python3-dev build-essential gettext libchromaprint-tools
```

Clone and run:

```bash
git clone https://github.com/renesiss/dlmtags.git
cd dlmtags

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python setup.py build
python setup.py build_ext -i
python setup.py build_locales   # translations
python tagger.py
```

If you use [uv](https://docs.astral.sh/uv/), replace the venv and pip steps
with `uv sync`. [INSTALL.md](INSTALL.md) (inherited from Picard) has more
detail on dependencies and build options.

Run the tests:

```bash
pip install -r requirements-dev.txt
pytest test/
```

## License

DLMtags, like Picard, is licensed under the
[GNU General Public License, version 2 or later](COPYING.txt)
(GPL-2.0-or-later). Upstream copyright notices are kept in the source files.
Modified files are recorded in the Git history.
