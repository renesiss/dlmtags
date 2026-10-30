# DLMtags migration manifest

This manifest was prepared before the destination copy. It records the exact inclusion rule and overlay paths.

## Included baseline

- All 1715 Git-tracked files at `ef8eaae8342e8f4ad40ec78b7e7c92aab81c81d3` (the DLMtags commit), including the complete current Picard engine, UI, resources, translations, tests, build scripts, docs, COPYING.txt, AUTHORS.txt, and copyright headers.
- The independent repository retains that DLMtags commit and its upstream base `84862c534`; it is shallow at the base to avoid importing unrelated old history and a historical credential-shaped value. The original upstream authors remain credited in source headers, AUTHORS.txt and .mailmap.
- Eight tracked `test/data` audio fixtures are included only as test data; no user music was selected.
- Existing platform installers, plugin code, generated Qt resource source `picard/resources.py`, and translations are retained because their future build/runtime role is uncertain. The Qt resources are rebuilt in the new checkout.

## Included working-tree overlay

- 26 modified tracked files copied byte-for-byte initially:

  - `picard/file.py`
  - `picard/options.py`
  - `picard/resources.py`
  - `picard/tagger.py`
  - `picard/ui/__init__.py`
  - `picard/ui/aboutdialog.py`
  - `picard/ui/coverartbox/coverartthumbnail.py`
  - `picard/ui/dlmtags/artwork.py`
  - `picard/ui/dlmtags/components/styles.py`
  - `picard/ui/dlmtags/confirm_results.py`
  - `picard/ui/dlmtags/sidebar.py`
  - `picard/ui/dlmtags/sidebar_style.py`
  - `picard/ui/dlmtags/tag_editor.py`
  - `picard/ui/dlmtags/workspace.py`
  - `picard/ui/itemviews/__init__.py`
  - `picard/ui/mainwindow/__init__.py`
  - `picard/ui/options/dialog.py`
  - `picard/ui/options/genres.py`
  - `picard/ui/setupwizard.py`
  - `po/el.po`
  - `po/picard.pot`
  - `resources/makeqrc.py`
  - `resources/picard.qrc`
  - `test/test_dlmtags_inline_suggestions.py`
  - `test/test_dlmtags_resize.py`
  - `test/ui/test_coverart_removal_display.py`

- 31 untracked source/resource/test/document files copied byte-for-byte initially:

  - `dlmtags.desktop`
  - `docs/audit/DLMTAGS_RELEASE_AUDIT.md`
  - `picard/bulk_autotag.py`
  - `picard/ui/dlmtags/api_credentials.py`
  - `picard/ui/dlmtags/bulk_autotag.py`
  - `picard/ui/dlmtags/exit_dialog.py`
  - `picard/ui/dlmtags/icons/api-connect.svg`
  - `picard/ui/dlmtags/icons/api-edit.svg`
  - `picard/ui/dlmtags/icons/api-keys.svg`
  - `picard/ui/dlmtags/icons/api-test.svg`
  - `picard/ui/dlmtags/icons/bulk-auto-tag.svg`
  - `picard/ui/dlmtags/icons/choose_folder.svg`
  - `picard/ui/dlmtags/icons/exit-cancel.svg`
  - `picard/ui/dlmtags/icons/exit-discard.svg`
  - `picard/ui/dlmtags/icons/file-settings.svg`
  - `picard/ui/dlmtags/icons/save.svg`
  - `picard/ui/dlmtags/icons/scan.svg`
  - `picard/ui/dlmtags/icons/search-artwork.svg`
  - `picard/ui/dlmtags/icons/settings.svg`
  - `picard/ui/dlmtags/icons/visible-tags.svg`
  - `picard/ui/dlmtags/welcome.py`
  - `picard/ui/options/dlmtags.py`
  - `resources/images/dlmtags-artwork-default.png`
  - `resources/images/dlmtags-icon.png`
  - `resources/images/dlmtags-logo.svg`
  - `test/test_bulk_autotag.py`
  - `test/ui/test_artwork.py`
  - `test/ui/test_bulk_autotag.py`
  - `test/ui/test_dlmtags_exit_dialog.py`
  - `test/ui/test_dlmtags_titles.py`
  - `test/ui/test_dlmtags_welcome.py`

The destination `dlmtags.desktop` is then updated to use destination paths. The destination README is updated to describe the shallow history accurately. The original source versions are unchanged.

## Excluded

- All 8183 ignored entries: `.venv/`, `build/`, `.pytest_cache/`, `.ruff_cache/`, `picard.egg-info/`, `__pycache__/`, compiled `.so`, generated `tagger.py`, generated `scripts/picard`, generated desktop/AppStream files and compiled locales. These are recreated from source when needed.
- `.aws/`, `.codex/`, `.agents/`, local credentials, personal configuration and any real music files outside the tracked test fixtures: excluded regardless of ignore status.
- Full old upstream Git history: not imported; only the DLMtags commit and upstream base are retained. This preserves relevant DLMtags authorship while excluding an unrelated historical credential-shaped value. The public upstream history is available from the upstream project.
- No symlinks: source selected files and stage checkout were checked and contained none.

## Verification rule

Compare SHA-256 and POSIX mode for every included overlay file against the source before the two documented destination-only edits. Compare the staged and destination trees, excluding `.git`, `.venv` and regenerated build products. Do not run cleanup or reset in the source.
