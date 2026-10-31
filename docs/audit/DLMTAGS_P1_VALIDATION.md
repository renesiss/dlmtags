# DLMtags P1 validation — 2026-10-31

**Verdict: PARTIAL. Accuracy: NOT VERIFIED.** The targeted Save and Confirm Results safety concerns were closed on 2026-10-31; the wider P1 verdict remains partial because no representative accuracy comparison exists. No build, package, Qt-resource rebuild, install, commit or push was performed.

## Confirmed defects and fixes

1. Bulk Year came from `decision.release['date']` when that release was verified. A reissue or compilation could therefore replace an older recording Year. Single Smart Scan used `first_release_date(track)` and preserved an existing Year. `picard/smartscan.py` now owns `decide_year()`, used by `picard/file.py` and `picard/bulk_autotag.py`. An empty Year takes known recording first-release evidence; a differing valid existing Year stays put in single Scan and appears only as a reviewed Bulk proposal. Invalid existing values, deletions and missing original-date evidence are preserved. The selected album date is never the source of an original Year.
2. Bulk constructed Genre proposals synchronously from raw recording/release metadata and completed before Smart Genre's asynchronous MusicBrainz → Discogs → Last.fm chain. It could miss useful results and disagree with single Scan. Bulk now waits asynchronously for `SmartGenreRequest` only when Genre is empty and genre lookup is enabled. Its existing 30-second deadline, provider settings, API keys, limits and fallback handling are reused. Empty, failed or timed-out results leave Genre unchanged. Existing Genre is preserved; only Title, Artist, Year and Genre enter Bulk proposals. No duplicate provider chain is started for a Bulk file.
3. `scripts/eval_matching/benchmark_accuracy.py` adds an offline labeled-export scorer. `scripts/eval_matching/README.md` documents collection and fair comparison. The existing corpus is synthetic matching data, not representative labeled audio; no accuracy comparison was measured.

## Benchmark method

Use identical immutable audio files and independently reviewed ground truth for Picard Scan, DLMtags Smart Scan and DLMtags Bulk. Run each on disposable copies under equivalent settings, cache/network conditions and provider availability. Export exact versions, configuration, decisions, proposed four tags, elapsed milliseconds and per-provider request counts. The scorer requires identical case IDs and SHA-256 hashes across runs and cases covering live recordings, remixes, covers, compilations, missing/incorrect tags, malformed filenames and non-Latin names. It computes exact-field precision, recall, coverage, false-positive rate, Needs Review rate and incorrect Artist/Title/Year counts. **No representative labeled dataset or three application exports were available; all comparative accuracy metrics are NOT VERIFIED.** Synthetic scorer tests are infrastructure checks only.

## Validation and remaining limits

- Focused Smart Scan, Bulk, Year, Genre and benchmark tests: **251 passed, 1 xfailed, 131 subtests passed** (3.47 s). An earlier focused run of Bulk/Year/Genre/UI tests passed **83 tests** after updating UI mocks for asynchronous lookup.
- Full isolated HOME/XDG pytest: **6,781 passed, 93 skipped, 1 xfailed, 11,133 subtests passed** (56.08 s). No real music collection was used.
- `ruff format` ran on modified Python files; `ruff check` passed. `git diff --check` passed. `ty` is unavailable in the existing environment and was not installed.
- P0's shared `picard/` package installation collision remains unresolved. Do not install DLMtags and upstream Picard into the same Python environment.

Files changed for P1: `picard/smartscan.py`, `picard/bulk_autotag.py`, `picard/file.py`, `picard/ui/dlmtags/bulk_autotag.py`, `scripts/eval_matching/benchmark_accuracy.py`, `scripts/eval_matching/README.md`, and related tests (`test/test_bulk_autotag.py`, `test/test_dlmtags_year.py`, `test/ui/test_bulk_autotag.py`, `test/test_accuracy_benchmark.py`).

## P0/P1 safety closure — 2026-10-31

**Confirmed Save effects.** Hidden `clear_existing_tags` can clear unrelated tag frames; `remove_images_from_tags` can remove embedded art. Hidden `remove_ape_from_aac`, `remove_ape_from_ac3`, and `remove_wave_riff_info` can delete format-specific tags. `preserve_images` affects the clearing branch but is not destructive by itself. Hidden rename, move, additional-file move, empty-directory deletion, collision overwrite and sidecar-artwork settings no longer reach operations in DLMtags' `_save_and_rename()` path. The visible Files-page choices `remove_ape_from_mp3` and `remove_id3_from_flac` remain explicit user actions.

**Save fix.** `picard/file.py` now rejects a Save before the format writer if any of the five hidden destructive metadata settings above is active, including profile overrides. The asynchronous Save callback reports failure and leaves the file staged for correction. The settings store is not mutated. Disposable FLAC tests verified byte-for-byte unchanged audio, unrelated tags, embedded artwork and parent directory after rejected saves. A disposable MP3 test verified that hidden rename/move/delete settings still allow an in-place tag Save without creating files in the configured destination.

**Confirmation fix.** Single Smart Scan's Confirm Results dialog, its direct one-candidate action, Bulk's manual review dialog and direct `File.confirm_recording()` all converge on the `decision.user_confirmed` branch of `File._apply_smart_scan_match()`. That branch now applies only Artist and Title to New Tags; Year and Genre remain separately reviewed suggestions. It does not enter Picard's album movement/metadata pipeline, add a MusicBrainz recording ID tag, or stage Style from Discogs. Bulk's `apply()` independently rejects a proposal containing a fifth tag before applying any field. Existing manual Artist/Title edits require the existing explicit replacement choice. Stale decisions and late Genre replies after cancellation remain rejected.

**Closure validation.** Focused Save/Smart Scan/Bulk/confirmation tests: **322 passed, 1 xfailed, 114 subtests passed** (3.71 s). Targeted new regressions: **7 passed, 7 subtests passed** (0.26 s). Full isolated HOME/XDG pytest: **6,787 passed, 93 skipped, 1 xfailed, 11,140 subtests passed** (46.14 s). `ruff format` ran on modified Python files; `ruff check` and `git diff --check` passed. No real collection was used.

**Remaining limits.** This guard covers DLMtags' normal `File.save()` path; direct calls to format-specific `_save()` and arbitrary third-party plugin pre/post-save hooks are outside this guarantee. Automatic matched Smart Scan still uses its existing Picard album metadata pipeline; the four-tag boundary described above applies to user-confirmed results and Bulk proposals. The installation collision and unmeasured comparative accuracy remain open, so the overall P1 verdict stays **PARTIAL**.

## Final P1 closure audit — 2026-10-31

**Normal UI safety verdict: PASS. Overall P1 verdict: PARTIAL.** No confirmed bypass of the Save guard or Confirm Results four-tag boundary was found in normal DLMtags UI paths. Comparative accuracy remains NOT VERIFIED, and same-environment installation remains unsafe as documented above.

| User action | Traced path | Result |
|---|---|---|
| Main Save | `MainWindow.save()` → `Tagger.save()` → `File.save()` → `_save_and_rename()` → format writer | Hidden destructive settings are checked before the writer; file remains in place. |
| Save & Exit | `ExitConfirmationDialog._save_and_exit()` → `Tagger.save()` → same `File.save()` path | A blocked Save keeps the dialog open and audio unchanged. |
| Exit Without Saving | `ExitConfirmationDialog._discard_and_exit()` | No audio Save call. |
| Single Confirm Results or one-candidate Confirm | `TagForm._confirm_results()` → `File.confirm_recording()` → user-confirmed `_apply_smart_scan_match()` | Artist/Title staged; Year/Genre offered as reviewed suggestions. Unrelated tags and art are preserved. |
| Bulk Confirm / Confirm All | `BulkAutoTagDialog.confirm()` → `bulk_autotag.apply()` | Only Title, Artist, Year and Genre can be staged; stale or fifth-tag proposals are rejected. |
| Bulk Needs Review | `BulkAutoTagDialog.review()` → `ConfirmResultsDialog.accept()` → `File.confirm_recording()` | Same narrow user-confirmed boundary as single review. |
| Find Tag approval | `FindTagDialog._apply()` → `TagSearchRequest.apply()` | Explicitly selected Title, Artist, Year or Genre is staged in that field only; a later Save is required. |

**GUI-to-audio evidence.** A new offscreen Qt test selected all four fields in Find Tag, clicked Apply for each, then clicked Save & Exit on a disposable FLAC copy. All four values were written; its unrelated tag, embedded PNG and file path remained intact. A second GUI test enabled hidden `clear_existing_tags`, clicked Save & Exit, and verified the dialog stayed open and the audio hash and path stayed unchanged. Provider candidates were injected into Find Tag for this lifecycle test; the tag selection, Qt actions, asynchronous Save and real Mutagen audio write were exercised. Existing provider tests cover network result handling separately.

**Confirmed user-accessible defects:** none remain in the traced normal Save or Confirm Results paths. Hidden destructive flags can still cause a visible Save failure; the curated Files UI does not expose a way to clear them, so recovery requires resetting the DLMtags configuration or profile outside that page. Cached Year/Genre previews have no one-click acceptance; users can use Find Tag or manually edit the fields, then Save. These are usability limits, not silent data modification.

**Internal or extension limits:** format-specific `_save()` methods are callable by code and tests but have no normal DLMtags UI entry point outside `File.save()`. Third-party plugin save hooks can run arbitrary code if deliberately installed or enabled; they are outside the format-writer guard and were not treated as normal built-in UI actions. Automatic matched Smart Scan remains a separate Picard metadata workflow, not a Confirm Results entry point. Neither direct writer calls nor plugin behavior was used to claim an additional user-facing vulnerability without a reachable UI path.

**Final audit validation:** focused Smart Scan, Find Tag, Bulk, Genre, Save and GUI tests: **305 passed, 1 xfailed, 145 subtests passed** (3.38 s). Targeted new GUI lifecycle tests: **2 passed** (0.19 s). Full isolated HOME/XDG pytest: **6,789 passed, 93 skipped, 1 xfailed, 11,140 subtests passed** (44.87 s). `ruff format` left the new test file unchanged; `ruff check` passed on all modified Python files. Repository-wide `ruff check .` reported **9 pre-existing I001 import-order errors** in untouched files; they were not suppressed or changed for this audit. `git diff --check` passed. No build, package, Qt-resource compilation, dependency installation, commit or push was performed.
