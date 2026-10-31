# DLMtags P0 validation — 2026-10-31

**Verdict: PARTIAL.** Runtime configuration and Save file placement are isolated, and the DLMtags wheel installs and launches in a clean target. Same Python environment installation alongside Picard remains unsafe because both distributions install the `picard/` package.

## Root causes and changes

- The fork retained Picard's Qt organization, application and DBus IDs, so QSettings could read and write `Picard.ini`. `picard/__init__.py` now defines DLMtags identities; `picard/const/appdirs.py` uses DLMtags-specific path overrides. Crash logs use a DLMtags prefix. No automatic Picard configuration import occurs.
- The inherited Save path obeyed hidden rename, move, extra-file move, empty-directory deletion and sidecar-image settings. `picard/file.py` now saves tags at the original path and does not call those file organization routines. `picard/options.py` defaults empty-directory deletion off. The Files page in `picard/ui/options/dlmtags.py` explains in-place saving.
- `pyproject.toml` and `setup.py` still identified the distribution, launchers and desktop assets as Picard. They now identify DLMtags. `dlmtags.desktop` is portable; `MANIFEST.in` and package data include DLMtags SVG/PNG assets. Picard copyright and GPL attribution remain in place.
- Regression tests were added or adapted in `test/test_dlmtags_identity.py`, `test/test_file.py`, `test/test_const_appdirs.py`, `test/formats/test_apev2.py` and `test/webservice/test_api_helpers_listenbrainz.py`.

## Validation

- Isolated HOME/XDG full suite: **6,773 passed, 93 skipped, 1 xfailed, 11,133 subtests passed** (34.61 s). This includes disposable format-file tests and existing Save, Save & Exit, Exit Without Saving, and artwork tests. No real collection was used.
- Focused hidden-setting Save regression: **2 passed, 88 deselected**. A separate focused identity/format/IPC run had one failure caused by a stale expectation of `FileNotFoundError`; after adapting it to Mutagen's actual `MutagenError`, the full suite passed.
- `ruff format` on modified Python files: **11 unchanged**. `ruff check`: **All checks passed**. `git diff --check`: **passed**. `ty` was unavailable in the environment.
- Fresh wheel and sdist built successfully. Both contain `dlmtags.desktop`, `dlmtags-icon.png` and DLMtags SVG icons. The fresh wheel contains only `dlmtags` in `.data/scripts/` and no `org.musicbrainz.Picard` installed assets. An earlier build reused a stale build directory and contained a `picard` script; rebuilding in `/tmp/dlmtags-p0-fresh-build` removed it.
- `pip install --no-deps --target /tmp/dlmtags-p0-install` succeeded for the fresh wheel. Its installed `dlmtags --version` exited 0 and reported `DLMtags DLMtags 3.0.2` under isolated HOME/XDG directories. The settings regression writes a simulated Picard QSettings store and verifies that DLMtags reads a different file and leaves Picard's bytes unchanged. The API-key regression verifies URL log redaction.

## Remaining risks

- **Installation collision:** the fresh wheel contains 679 files under `picard/`; installing it into a Python environment containing upstream Picard can replace upstream modules. No upstream Picard installation was present here, so simultaneous launch and same-environment coexistence were not tested or claimed. The smallest safe beta distribution path is a dedicated virtual environment or application bundle with its own prefix; same-environment coexistence needs a package namespace migration or equivalent isolation.
- Existing settings in a previously shared `Picard.ini` are deliberately not imported. DLMtags settings and API keys remain supported in the new store, but affected users must re-enter them until an explicit, reversible migration is implemented. Do not copy the entire Picard configuration: hidden settings may have unwanted effects.
- Hidden Picard metadata options such as `clear_existing_tags` and `remove_images_from_tags` remain in the inherited config model. The in-place Save guard prevents path and directory changes, but those options need a separate policy before claiming all hidden settings are harmless. The suite validates normal metadata/artwork behavior and does not prove every manually edited hidden setting combination.
