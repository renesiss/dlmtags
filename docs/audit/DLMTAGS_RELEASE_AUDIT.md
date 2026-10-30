# DLMtags pre-release audit

**Date:** 2026-10-29  
**Scope:** `/home/sakis/Myapps/bingo/dlmtags`, current working tree; read-only audit of application code.  
**Decisions:** Public GitHub source **NOT READY** · Public Beta binaries **NOT READY** · Stable 1.0 **NOT READY**.  
**Severity tally:** 4 BLOCKER release gates, 3 HIGH risks. No unintended modification of an audio file was confirmed.

## 1. Executive summary

The checkout contains substantial DLMtags work, and most existing automated tests pass. It is not release-ready. A wheel and source archive built from a disposable copy still identify themselves as **Picard 3.0.2**; the wheel installs Picard's desktop launcher and icon, omits DLMtags' launcher and filesystem-loaded SVG icons, and therefore risks colliding with a separately installed Picard. The DLMtags work is not committed (26 modified paths and 17 untracked entries), so a release from the current Git commit would omit it. Rights and attribution for new DLMtags images and icons have not been documented. The corrected full test run has 10 failures.

An isolated FLAC test verified that staging a title change did not alter bytes, and a direct Picard format-handler save preserved a deliberately added unrelated tag and embedded image. This is narrower than an end-to-end DLMtags Save/Cancel/Exit guarantee. No real music files or real user configuration were touched.

## 2. Tests actually executed

| Check | Result | What it establishes |
|---|---:|---|
| Full `pytest -q test`, offscreen Qt, isolated `HOME` and XDG runtime, default XDG config/data/cache under that home | **6,734 passed, 10 failed, 92 skipped, 1 xfailed; 11,133 subtests passed** | Current suite is red. Failures are listed below. No live providers were contacted. |
| Initial full run with nondefault XDG config/data/cache directories | 6,730 passed, 14 failed | Four additional failures were environment-related: three path assertions and one pipe cleanup under a read-only runtime directory. Rerunning with conventional isolated XDG paths removed these four; the pipe test was skipped on that rerun. |
| Focused rerun of resize, Bulk Auto-Tag and setup wizard tests | 28 passed, 10 failed | Reproduced all 10 remaining failures. |
| Temporary disposable FLAC test, `test/data/test.flac` copied under pytest `tmp_path` | **1 passed** | SHA-256 unchanged after staging; hash changed after direct `File._save`; title updated; custom unrelated tag and one seeded embedded PNG remained byte-identical; default placeholder bytes absent. This used the format handler, not the full asynchronous GUI Save path. |
| `ruff check picard test` | **12 errors** | All 12 occur in files unchanged in this working tree; they predate the current uncommitted edits but still fail the repository gate. |
| `ruff format --check picard test` | **21 files would be reformatted** | All 21 are unchanged in this working tree. No formatting was applied. |
| `ty check picard` | **1,076 diagnostics** | Broad existing type-check debt. No reliable baseline was available to attribute each diagnostic to a change. |
| `desktop-file-validate dlmtags.desktop` | Passed | Syntax only; does not establish install portability. |
| Qt resource presence check | Passed | `:/images/dlmtags-icon.png`, `dlmtags-artwork-default.png` and `dlmtags-logo.svg` resolve in this checkout. |
| Offline wheel and sdist build from a temporary copy | Built `picard-3.0.2` wheel and sdist | Archive inspection found the packaging defects below. No system install was performed. |
| Narrow secret-pattern scan of current files and Git history | One historical AWS-key-shaped candidate; no current file hit for the narrow high-confidence patterns | Not a comprehensive credential audit or validity check. No values are reproduced here. |

The relevant automated tests cover mocked 0/1/16 sidebar selection and queue counts ([test/ui/test_bulk_autotag.py:468](../../test/ui/test_bulk_autotag.py)), 16 completed result cards ([test/ui/test_bulk_autotag.py:299](../../test/ui/test_bulk_autotag.py)), mocked Smart Scan cancellation/HTTP 503/AcoustID failure ([test/test_smartscan.py:808](../../test/test_smartscan.py)), manual artwork 404/timeout/import/cancel ([test/ui/test_artwork.py:53](../../test/ui/test_artwork.py)), and exit cancel/discard/success/failure/async cases ([test/ui/test_dlmtags_exit_dialog.py](../../test/ui/test_dlmtags_exit_dialog.py)). The 1,000-card test checks rendering, **not** a 100-track scan.

## 3. Verified findings and reproduction

### B1 — BLOCKER: distributable package omits DLMtags UI assets and launcher

**Evidence.** SVG toolbar and exit icons are loaded by filesystem path in [workspace.py:355](../../picard/ui/dlmtags/workspace.py) and [exit_dialog.py:13](../../picard/ui/dlmtags/exit_dialog.py); Artwork also loads its search SVG by path in [artwork.py:574](../../picard/ui/dlmtags/artwork.py). [MANIFEST.in:1](../../MANIFEST.in) includes `resources/images` but does not include `picard/ui/dlmtags/icons`. The built wheel and sdist contain no `picard/ui/dlmtags/icons/*.svg` or `dlmtags.desktop`. The wheel contains `org.musicbrainz.Picard.desktop` instead. The compiled Qt resources do include the three image/logo assets, so this finding is specific to filesystem SVGs and desktop integration.

**Reproduce safely.** Copy the source to a temporary directory, run `pip wheel --no-deps --no-build-isolation .` and `python setup.py sdist`, then list archive entries for `picard/ui/dlmtags/icons/` and `dlmtags.desktop`; neither appears. Launching an installed wheel was not tested; missing icons at runtime are inferred from the path-loading code.

### B2 — BLOCKER: install identity collides with upstream Picard

**Evidence.** [pyproject.toml:5](../../pyproject.toml) names the distribution `picard`, [picard/__init__.py:46](../../picard/__init__.py) retains Picard's application name/display name/ID, and [setup.py:752](../../setup.py) installs `org.musicbrainz.Picard.desktop` and matching icons. The wheel is named `picard-3.0.2` and contains the Picard desktop path. This is a concrete collision risk if packaged beside the original Picard; the repository-local DLMtags launcher does not correct the wheel.

**Reproduce safely.** Inspect wheel metadata, `entry_points.txt`, and `.data/share/applications/` in the temporary wheel. No installation over a real Picard copy was attempted.

### B3 — BLOCKER release gate: DLMtags changes are absent from the current Git commit

**Evidence.** `git status --porcelain` reports **26 modified paths and 17 untracked entries**, including the DLMtags launcher, Bulk Auto-Tag modules, exit dialog, icons, images and tests. HEAD is `ef8eaae83`. A GitHub source release from HEAD or `git archive HEAD` would omit this work. This is a release-process finding, not a request to commit it during this audit.

**Reproduce safely.** Compare `git status --short` with `git ls-files` for, for example, [picard/ui/dlmtags/exit_dialog.py](../../picard/ui/dlmtags/exit_dialog.py) and [dlmtags.desktop](../../dlmtags.desktop). Preserve the working tree while resolving this gate.

### B4 — BLOCKER legal gate: provenance of new distributable art is undocumented

**Evidence.** The new [application icon](../../resources/images/dlmtags-icon.png), [default artwork](../../resources/images/dlmtags-artwork-default.png), [DLMtags logo](../../resources/images/dlmtags-logo.svg) and [SVG icon directory](../../picard/ui/dlmtags/icons) have no identified author, source or redistribution license in [AUTHORS.txt](../../AUTHORS.txt), [README.md](../../README.md) or a DLMtags asset notice. The PNGs had no PNG text/EXIF chunks in a narrow metadata check; that does not establish ownership. Unknown provenance is **not** proof of infringement. It is an unresolved redistribution permission gate.

**Reproduce safely.** Inventory those assets and look for a corresponding author/license record. Obtain the rights holder's confirmation or replace with demonstrably licensed work before publishing them. Professional legal review may be needed.

### H1 — HIGH: the automated release gate is red

**Evidence.** Seven resize tests in [test/test_dlmtags_resize.py:36](../../test/test_dlmtags_resize.py) fail because the test `Window` lacks `file_sidebar`, now required by [workspace.py:387](../../picard/ui/dlmtags/workspace.py). The real main window creates that sidebar before the workspace at [mainwindow/__init__.py:345](../../picard/ui/mainwindow/__init__.py), so these seven failures prove stale test integration, **not** a demonstrated application crash. Two Bulk Auto-Tag tests expect a two-argument `ConfirmResultsDialog` call ([test/ui/test_bulk_autotag.py:175](../../test/ui/test_bulk_autotag.py)), while the implementation supplies `allowed_tags` at [bulk_autotag.py:683](../../picard/ui/dlmtags/bulk_autotag.py). One setup wizard test expects `MetadataPage` ([test/ui/test_setupwizard.py:113](../../test/ui/test_setupwizard.py)), absent from the current `PAGES` at [setupwizard.py:690](../../picard/ui/setupwizard.py). These are current-tree failures; their user-visible impact is not established.

**Reproduce safely.** Run the three focused test files under `QT_QPA_PLATFORM=offscreen` with an isolated home. They produced 10 failures and 28 passes. Determine whether tests or product behavior should change; do not waive the failures without review.

### H2 — HIGH security triage: historical credential-shaped value

**Evidence.** A narrow `git log -G` scan of the full history found an AWS access-key-shaped assignment in historical upstream commit `5d44f0306`, `contrib/plugins/coverartplus/__init__.py:73`. That file is absent from the current tree. The key's ownership, validity and revocation status are unknown; its value is deliberately omitted. The current working tree had no hit for the narrow AWS/GitHub/private-key patterns. The inherited history contains roughly 16,994 commits, so this was not a comprehensive history scan.

**Reproduce safely.** Use a history-aware secret scanner that redacts values, inspect the cited commit and ask the credential owner to verify revocation. Do not paste the value into issues or logs. This inherited candidate does not, by itself, prove a live DLMtags credential leak.

### H3 — HIGH data-integrity risk: “Exit Without Saving” does not stop a save already in flight

**Evidence.** [exit_dialog.py:69](../../picard/ui/dlmtags/exit_dialog.py) connects the discard button directly to `accept()`. Pending files are included in the exit prompt by [mainwindow/__init__.py:542](../../picard/ui/mainwindow/__init__.py); Picard saves run in its save thread pool and cleanup waits for that pool at [tagger.py:334](../../picard/tagger.py). The dialog never cancels an already started save. Therefore “Exit Without Saving” means “start no new save,” not “prevent an existing pending write.” This control flow is verified statically. Actual disk modification in this race was **not** demonstrated and is not counted as a confirmed unintended audio-file modification.

**Reproduce safely.** On a disposable audio copy, make a change, start Save, delay the save worker, close the window while the file is pending, and choose Exit Without Saving; compare hashes and tags afterward. Define and test the expected behavior before beta.

### M1 — MEDIUM: the development launcher is not portable

**Evidence.** [dlmtags.desktop:5](../../dlmtags.desktop) hard-codes this machine's checkout, `.venv` Python, working directory and icon path. It passes `desktop-file-validate`, but moving the checkout or installing elsewhere invalidates those paths. **Reproduce:** copy the launcher to another account or prefix and inspect/execute its `Exec` and `Icon` paths. This also exposes a user-specific filesystem path if committed unchanged.

### M2 — MEDIUM: quality and distribution documentation remain Picard-oriented

**Evidence.** Ruff reports 12 import/style errors; `ruff format --check` names 21 unformatted files. All are unchanged in this working tree, but the release gate is not green. [INSTALL.md](../../INSTALL.md) still describes installing/running Picard, [pyproject.toml:6](../../pyproject.toml) describes Picard, and no DLMtags screenshot, binary recipe or dedicated third-party asset notice was found. [bulk_autotag.py:679](../../picard/ui/dlmtags/bulk_autotag.py) still directs a user to “Lookup” although that toolbar action was removed. **Reproduce:** run the two Ruff commands; inspect the cited files and missing release artifacts.

### M3 — MEDIUM privacy disclosure gap: provider keys persist in user settings

**Evidence.** [options.py:437](../../picard/options.py) and [options.py:620](../../picard/options.py) mark Last.fm and Discogs keys non-shareable, and [config.py:753](../../picard/config.py) uses user-scoped INI `QSettings`. Existing tests verify edit/cancel synchronization and URL-log redaction ([test/test_dlmtags_inline_suggestions.py:483](../../test/test_dlmtags_inline_suggestions.py), [test/test_background_tag_search.py:208](../../test/test_background_tag_search.py)). `shareable=False` does not mean encrypted at rest. File permissions, backup exposure and all log paths were not independently validated. **Reproduce safely:** enter a dummy key in an isolated home, save settings, and inspect only that isolated config file without printing its value.

## 4. Data integrity and functional assessment

The disposable FLAC experiment supports one narrow path: staging did not write; direct format-handler Save updated the intended title and preserved a custom unrelated tag and existing embedded image. The full format suite exercised its own temporary copies. The exact packaged placeholder was rejected by the manual import test ([test/ui/test_artwork.py:176](../../test/ui/test_artwork.py)); the widget uses the Qt resource only when no art is shown ([coverartthumbnail.py:100](../../picard/ui/coverartbox/coverartthumbnail.py)). The manual import guard compares exact file bytes at [artwork.py:68](../../picard/ui/dlmtags/artwork.py), so it does not establish that re-encoded copies of the same picture are rejected. No placeholder embedding was observed in the disposable FLAC test.

The 0/1/16 sidebar-selection mock test passed, and the 16-track result-accounting mock test passed. This is evidence for those simulated states only. Manual Confirm Results, Confirm/Cancel/Confirm All/Cancel All, settings, visible tags and API-key synchronization have passing focused tests, except the two outdated Bulk Auto-Tag mock assertions noted above. No live MusicBrainz/AcoustID/CAA lookup, 100-track batch, folder-browsing session, or GUI Save & Exit on a real file was executed. Every selected file being accounted for in production therefore remains unverified.

## 5. Stability, networking, security and remaining limits

- Simulated Smart Scan HTTP 503, AcoustID failure, cancellation and stale callbacks have passing tests. Manual Artwork tests cover empty results, 404-like missing artwork and timeout. Dedicated DLMtags end-to-end HTTP 429/500, network disconnect, repeated-scanning and recovery runs were not performed. UI responsiveness under 100 real tracks or a large library was not measured.
- Malformed, read-only and unsupported files were not exercised through the new DLMtags GUI in this audit. The inherited format suite passed, but that does not prove the complete DLMtags save/exit flow for these cases.
- The isolated FLAC test did not inject save failure or process interruption. Existing exit tests use mocks for failed and asynchronous saves. Partial-save behavior across multiple real files remains unverified.
- The full history scan used narrow patterns; no comprehensive secret scanner was installed. No dependency vulnerability advisory scan was completed because `pip-audit`/equivalent reliable tooling was unavailable locally. The hash-pinned [requirements.txt](../../requirements.txt) supports reproducible dependency selection but is not a vulnerability assessment.
- External web links found in DLMtags generally use HTTPS or provider-specific URL checks; no exploit was demonstrated. Full input fuzzing, image decompression limits and third-party response validation were outside this audit.
- A clean Linux install outside the development virtual environment, Dock/Alt+Tab integration, and a live GUI session with actual services were not tested. The temporary package build and archive inspection are the evidence for distribution defects.

## 6. Licensing and distribution assessment

The local [COPYING.txt](../../COPYING.txt) is GPL version 2, and [README.md](../../README.md) says **GPL-2.0-or-later**. Upstream [MusicBrainz Picard development documentation](https://picard.musicbrainz.org/docs/development/) confirms GPL 2.0 or later. Existing Picard copyright headers and [AUTHORS.txt](../../AUTHORS.txt) remain; the latter documents some upstream art credits. [Riverbank's PyQt page](https://en.riverbankcomputing.com/software/pyqt) says PyQt is GPLv3/commercial, and the [GNU GPL FAQ](https://www.gnu.org/licenses/gpl-faq.en.html#v2v3Compatibility) says GPL-2.0-or-later code can be combined under GPLv3 terms. **Inference:** a GPL route is possible, but the binary's complete license bundle, Qt/PyQt obligations, all third-party dependency licenses, and the provenance of DLMtags assets require a distribution-specific review. This report is not legal advice. Do not assume that retaining Picard's `COPYING.txt` alone clears newly added artwork and binaries.

## 7. Required fixes, in priority order

1. Give DLMtags a distinct distribution/application/desktop identity and verify installation alongside upstream Picard without replacing its launcher, icon, settings or IPC identity. Build and inspect an installed artifact outside the checkout.
2. Include every filesystem-loaded DLMtags SVG and the proper desktop launcher in source/binary packages; verify icons and entry points from the installed artifact.
3. Record creator, source, permitted license and attribution for each new PNG/SVG; obtain professional review where ownership or GPL compatibility is unclear.
4. Review and commit only intended source/assets/tests after a secret and path audit. Do not publish a HEAD-only archive of the current uncommitted state.
5. Resolve the 10 current full-suite failures, then clear Ruff/style gates and establish a type-check baseline for changed modules.
6. Test the in-flight save/discard race on disposable files and define safe behavior. Extend hash/metadata checks to GUI Save, failed/interrupted saves, read-only/malformed inputs, real embedded-art replacement and exit cancellation.
7. Execute 100-track and large-library tests, provider error matrix (429/500/503, timeout/disconnect/404), and an installed-package GUI smoke test. Complete a dependency advisory scan and a comprehensive, redacted history secret scan.
8. Replace checkout-specific install instructions and launcher paths with reproducible Linux build/install steps, screenshots, versioning and third-party notices.

## 8. Final release decision

| Target | Decision | Reason |
|---|---|---|
| Public GitHub source | **NOT READY** | Uncommitted implementation and undocumented asset rights; source metadata still identifies upstream Picard. |
| Public Beta binary | **NOT READY** | Incomplete DLMtags package and upstream Picard installation collision; required data-integrity and installed-GUI checks remain. |
| Stable 1.0 | **NOT READY** | All blockers, red tests and major functional/security coverage gaps remain. |

Passing most tests does not change these decisions. No audio-file corruption was confirmed, but the untested paths are too significant for a release claim.
