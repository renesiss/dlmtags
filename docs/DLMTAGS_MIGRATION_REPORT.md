# DLMtags clean project migration

**Source:** `/home/sakis/Myapps/bingo/dlmtags` (read only for this migration)  
**Destination:** `/home/sakis/Myapps/DLMtags`  
**Date:** 2026-10-29

## Result

The migration uses a reviewed file manifest at [DLMTAGS_MIGRATION_MANIFEST.md](DLMTAGS_MIGRATION_MANIFEST.md). It preserves the committed DLMtags source and all 57 modified/untracked working-tree files, including upstream Picard code, translations, tests, build scripts, licensing and attribution. The destination has its own shallow Git repository with the DLMtags commit and the upstream base commit, and no remote pointing to the source. The source was not reset, cleaned or edited.

## What was copied and excluded

- **Included:** 1,715 files tracked by source commit `ef8eaae83`; 26 modified tracked files overlaid from the source working tree; 31 untracked application, resource, test, launcher and audit files; this manifest and report. Eight tracked audio files under `test/data` are test fixtures, not user music.
- **Retained for safety:** all current Picard engine and UI modules, plugins, format handlers, platform installers, `.ui` files, compiled Qt resource source, `po/` translations, test infrastructure, `COPYING.txt`, `AUTHORS.txt`, `.mailmap`, copyright headers and build/dependency definitions. Their apparent lack of use in the new UI was not a reason to remove them.
- **Excluded:** all 8,183 ignored entries, including the old `.venv`, caches, generated `build/`, egg-info, compiled extensions, generated locales, generated source launcher and desktop/AppStream output. No personal `.aws`, `.codex`, `.agents` state or real music files were selected. No symlinks were copied.
- **History choice:** only commit `ef8eaae83` and its upstream base `84862c534` were cloned. The much older upstream history was not imported because it is not required for the current build and contains a historical credential-shaped value. Upstream authorship remains in AUTHORS, `.mailmap`, source headers and the base commit. The original Picard repository remains the source for its full history.
- **Destination-only changes:** README now describes the shallow history accurately. `dlmtags.desktop` points to `/home/sakis/Myapps/DLMtags`; it no longer points to the old checkout. Application source code was otherwise copied unchanged.

The full included file rule and every working-tree overlay path are in the manifest. SHA-256 and POSIX modes matched the source for all 57 overlay files before the two destination-only edits. A second check over all 1,746 selected source files found no byte mismatch except those two edits.

## Build and verification

- A separate temporary Python 3.13 virtual environment installed the pinned `requirements-dev.txt` successfully; `pip check` reported no broken requirements. No packages or virtual environment were copied from the source checkout.
- `setup.py build_ui` successfully regenerated Qt forms and `picard/resources.py` in staging. All three DLMtags Qt image/logo resource paths resolved. Generated changes caused solely by the installed Qt tool version were restored to the exact source versions after this verification, preserving the source working-tree content.
- `setup.py build` succeeded in staging and generated the source launcher `tagger.py`. The destination performs its own build and has its own virtual environment.
- Staging import/resource smoke checks passed for `picard.tagger`, DLMtags workspace, artwork, Bulk Auto-Tag and bundled Qt images.
- The staging offscreen GUI launch started and remained active without a Python traceback until interrupted after the smoke interval. Audio-service warnings occurred in the headless sandbox; this is not a visual desktop test.
- Staging focused tests: **92 passed, 2 failed**. The failures are the existing `test_ambiguous_cannot_be_confirmed_and_uses_manual_review` and `test_needs_review_card_has_visible_review_action` assertions in `test/ui/test_bulk_autotag.py`; both expect a two-argument `ConfirmResultsDialog` call, while the implementation passes `allowed_tags`. No application bug was fixed during migration.

**Destination checks:**

- Independent venv and pinned dependency install: **created in destination / passed**.
- `pip check`: **passed**; `setup.py build`: **passed**.
- Copy verification before build: **passed** (1,748 selected regular files, SHA-256 and modes).
- Import/Qt resource check: **passed**; desktop validation: **passed**.
- Focused tests: **2 failed, 92 passed in 2.62s** (exit code 1).
- Headless GUI smoke: application alive after 4 seconds: **True**; browser integration initialized: **True**; Python traceback: **False**. The audit process then terminated it (exit 0). The earlier `timeout` wrapper returned 125 despite the application reaching its event loop; the controlled run is the stronger evidence. This is not a visual desktop test.
- Old source path in runtime/build/launcher files: **none**; symlink to source: **none**; Git history/remotes: **two commits; no remotes**.

## Reproducibility and limitations

The destination is independent of the old checkout at runtime: the development launcher is rewritten, the selected application/build files have no old-checkout absolute path, and no symlink points back. The historical audit document still records the original path as evidence; it is documentation, not a runtime dependency.

This migration does not solve the pre-release audit findings. In particular, the package metadata and installed desktop identity still identify Picard, DLMtags SVGs are not yet bundled in wheel/sdist builds, new asset provenance remains undocumented, and the focused test failures remain. The development launcher is tied to the new local path and is not a portable release launcher. The required upstream source contains a public distributed OAuth client credential; no personal credential/configuration was copied. A full secret scan, rights review and installed binary test remain release work.

No push, release, tag or application-source fix was performed.
