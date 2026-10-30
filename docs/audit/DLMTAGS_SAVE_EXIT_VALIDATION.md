# DLMtags Save & Exit validation

## Scope and decision

This validation used only `/home/sakis/Myapps/DLMtags` and disposable copies of its bundled `test/data/test.mp3` and `test/data/test.flac`. No personal music files, old repository files, or release artifacts were changed. The normal DLMtags **Save & Exit** path now waits for all queued file-save callbacks and refuses to close if metadata or staged artwork remains unsaved. This is a UI lifecycle guarantee, not a guarantee of atomic disk writes or survival of power loss.

## Save lifecycle traced

1. The main-window Save action resolves selected files and calls `Tagger.save` after any configured warning (`picard/ui/mainwindow/__init__.py:1444`). The notification manager subscribes before dispatch and deduplicates files already in the active batch (`picard/ui/dlmtags/save_toast.py:109`).
2. `File.save` marks a file pending, copies its metadata, increments `_saving_files_count`, and queues `_save_and_rename` on Picard's single-thread save pool (`picard/file.py:677`, `picard/tagger.py:336`). The worker writes with the existing format handler; its result is posted to the Qt main thread (`picard/util/thread.py:107`).
3. `_saving_finished` decrements the count, applies success/error state, and emits `save_finished` only after that callback (`picard/file.py:823`). The toast reports a consolidated success, partial failure, or error only after all tracked callbacks finish (`picard/ui/dlmtags/save_toast.py:128`). An empty batch shows nothing.
4. Main-window close gathers pending metadata and artwork state (`picard/ui/mainwindow/__init__.py:513`, `:539`). Save & Exit uses the same `Tagger.save` path, skips files already pending, and waits for the global save count and file states in a Qt timer (`picard/ui/dlmtags/exit_dialog.py:97`, `:114`). Exit Without Saving does not start a write.
5. On application shutdown, Picard waits for the save thread pool (`picard/tagger.py:337`, `:738`). That wait alone is insufficient for Save & Exit because completion callbacks are delivered through the Qt event loop; the dialog waits for those callbacks before accepting.

## Confirmed defects and fixes

| Severity | Defect and root cause | Fix and regression evidence |
| --- | --- | --- |
| HIGH | Exit Without Saving remained available during an earlier active save. The dialog's discard button accepted immediately, which could imply a write had been discarded while it continued. | `exit_dialog.py`: disable discard while `_saving_files_count > 0`, explain the in-progress write, keep Cancel available, and guard queued/programmatic discard clicks. Restore the choice only after completion. Tests cover active, completed, and failed prior saves, plus a gated real MP3 write. |
| HIGH | With quit confirmation disabled, `MainWindow.closeEvent` bypassed the save guard during an active write. | `mainwindow/__init__.py`: invoke the exit guard whenever `_saving_files_count > 0`, regardless of the preference. A regression test verifies that a rejected dialog keeps the window open. |
| HIGH | Save & Exit checked `is_saved()` and expected embedded images but could accept when `metadata.images` still differed from `orig_metadata.images` (staged external artwork). | `exit_dialog.py`: also require the staged artwork list to match the original before accepting. The pre-fix regression accepted; it now keeps the dialog open and preserves the staged artwork. |

The fix does not cancel, terminate, or interrupt an active write. If an existing write finishes with no remaining changes, the waiting exit request completes. If changes remain or a save fails, the dialog stays open and allows Cancel; discard becomes available only after writes finish.

## Actual audio-file coverage

The tests in `test/test_dlmtags_save_exit_data.py` copy bundled MP3/FLAC fixtures to temporary directories. They compare SHA-256 hashes and inspect tags with Mutagen.

| Scenario | Observed result |
| --- | --- |
| Stage title without Save | Hash and original metadata unchanged. |
| Save MP3 | New title written; unrelated `TXXX` tag retained; hash changed only after Save. No `APIC` artwork frame added. |
| Save FLAC with existing embedded PNG | New title written; unrelated tag and original picture bytes retained. No placeholder image added. |
| Exit Without Saving | Accepted discard leaves the temporary MP3 hash unchanged and staged metadata still different in memory. |
| Simulated save failure | Temporary MP3 hash unchanged, file enters ERROR, toast reports failure. |
| Read-only MP3 (`0444`) | Save callback reports failure; hash unchanged. |
| Invalid MP3 and unsupported text file | Load/format recognition fails; hashes unchanged. |
| Save & Exit during a gated real asynchronous MP3 write | Existing write and second queued write both finish before dialog accepts; both intended titles persist. |
| Exit requested during a gated write | Discard disabled; Cancel returns to the app; the already-started write completes afterward. |
| Two-file partial failure | One file changes, one retains its hash; toast reports `1 file saved; 1 failed`. |
| Repeated Save request while pending | No second write is queued for the same file and one success toast appears. |

Qt tests in `test/ui/test_dlmtags_exit_dialog.py` and `test/ui/test_dlmtags_save_toast.py` additionally cover cancellation, successful/failed asynchronous completion, zero saves, 16-file consolidation, and no duplicate notification.

## Test results

- Focused Save/Exit, notification, and disposable-audio tests: **33 passed** before final format and suite run.
- File-format tests: pending final run.
- Full pytest suite: pending final run.
- Ruff and `git diff --check`: pending final run.

## Remaining data-integrity risks

- Picard's format handlers write through Mutagen directly. A process crash, power loss, disk-full condition, or I/O error after writing begins may leave a partially updated file. No transaction, rollback, or fsync guarantee was demonstrated. The read-only and injected pre-write failures above do **not** prove safety against interrupted writes.
- Direct application quit paths outside `MainWindow.closeEvent` (for example a signal-triggered `Tagger.quit`) wait for worker threads but set `stopping` before all queued jobs necessarily start. They do not offer the same interactive Save & Exit guarantee. This was not changed in this focused UI audit.
- Requiring staged external artwork to clear can keep Save & Exit open even if an external image was written successfully but Picard still reports a difference in memory. This favors preventing silent loss; that edge needs a separate end-to-end artwork-export test before claiming it is resolved.
