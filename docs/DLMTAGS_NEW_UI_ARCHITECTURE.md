# DLMtags new UI architecture — Phase 1

## Scope and inspection baseline

This is an inspection and migration plan, not a UI implementation. Preserve the
Picard recognition, matching, metadata, artwork and saving engine, its settings,
plugin hooks and GPL notices. No application launch, builds, tests, installations,
commits, network lookups or media/database modifications were performed.

Read the repository `AGENTS.md` and `CONTRIBUTING.md`. No additional nested
`AGENTS.md` files were found. Inspected the original customized workspace and the
committed backend in the isolated worktree. Findings below are source inspection,
not runtime verification. No reference image is attached in this conversation;
the written layout requirements are the design baseline pending visual review.

## 1. Isolation and changes deliberately left behind

| Item | State |
| --- | --- |
| Original worktree | `dlmtags-picard` (local worktree) |
| Original branch | `dlmtags-development` |
| New worktree | `dlmtags-new-ui` (local worktree) |
| New branch | `dlmtags-new-ui` |
| Shared starting commit | `84862c534c7a801b726035ee445b0903f3d3fe4a` |
| Starting commit subject | Merge pull request #3521 from zas/PICARD-3511-multiselect-artwork-not-embedded |

The new branch was created from the original committed HEAD using `git worktree
add -b dlmtags-new-ui ... HEAD`. Neither local modifications nor untracked files
were copied, stashed, restored, cleaned or committed. Git worktrees share repository
objects and branch metadata, but have separate indexes and working files. Creating
the branch/worktree updates shared Git metadata only. This report is the only new
working file created for this phase.

These five tracked local modifications are **absent as modifications** in the new
worktree; their committed Picard versions are present:

| Original modified file | DLMtags changes absent from the isolated baseline |
| --- | --- |
| `picard/const/defaults.py` | Dark theme as the default, while respecting saved theme selection |
| `picard/ui/theme.py` | DLMtags dark semantic colors and their application to palette roles |
| `picard/ui/filebrowser.py` | Model ownership, real starting path, restoration and ancestor expansion fixes |
| `picard/ui/itemviews/__init__.py` | Custom split workspace, Search, direct filesystem browser, hidden stock views, Songs mirror, card/shadow and compact sidebar sizing |
| `picard/ui/mainwindow/__init__.py` | DLMtags branding/header, legacy chrome suppression, custom workspace assembly, action wiring and browser startup visibility |

At inspection these tracked changes totaled 406 insertions and 100 deletions.
The following untracked files are **entirely absent** from the new worktree:

```text
INSTALL-DLMtags-UI-Fix.txt
INSTALL_PATCH.txt
README-INSTALL.txt
README.txt
picard/ui/dlm_panels.py
picard/ui/itemviews/__init__.py.20261019-234305.bak
picard/ui/itemviews/__init__.py.bak-sidebar
picard/ui/itemviews/__init__.py.before-dmusic-sidebar
picard/ui/mainwindow/__init__.py.20261019-234305.bak
picard/ui/mainwindow/__init__.py.bak
picard/ui/mainwindow/__init__.py.bak-artwork
picard/ui/mainwindow/__init__.py.pre-ui-fix
picard/ui/mainwindow/workspace.py
picard/ui/mainwindow/workspace.py.before-dmusic-sidebar
```

In particular, `dlm_panels.py` contains the local Songs selection mirror, matching
summary and artwork wrapper; `workspace.py` contains the palette-based workspace
styles and latest thin scrollbar/spacing polish. These are reference material,
not imported dependencies of the new architecture. Do not copy the patch wholesale:
review each reusable behavior, preserve its licensing, and port it explicitly in
a later phase. Backup/install files need no migration.

## 2. Existing components to reuse

Paths below are relative to the new worktree and refer to actual inspected code.

| Capability | Existing implementation and integration boundary |
| --- | --- |
| Application/services | `picard/tagger.py`: `Tagger` owns files, albums, clusters, format registry, web services and AcoustID; retain the existing application/event-loop lifecycle |
| File/folder loading | `Tagger.add_paths()` → `_scan_paths_recursive()` → `add_files()` → batched `File.load()`; retain recursive/hidden/ignore settings, format recognition, duplicate-path checks and load callbacks |
| Filesystem navigation | `picard/ui/filebrowser.py`: `FileBrowser`, lazily created `QtGui.QFileSystemModel`, supported-format filters, hidden-files setting, starting-directory state, drag and context actions; loading delegates to `Tagger.add_paths()` |
| Acoustic recognition | `Tagger.analyze()` → `picard/acoustid/__init__.py` task queue, fpcalc process and AcoustID API helper → recording resolver → `File._lookup_finished()`; preserve configuration checks, limits and callbacks |
| Metadata matching | `Tagger.autotag()` delegates to object `lookup_metadata()`; `File`, `Cluster`, lookup helpers and MusicBrainz API services own search/matching decisions |
| Release/album grouping | `picard/album.py`, `picard/track.py`, `picard/cluster.py`: Album/Track/Cluster identities, unmatched files, file movement, album assignment and metadata comparison; `Tagger.cluster()` retains backend clustering |
| Metadata read/edit | `picard/file.py`, `picard/metadata.py`, `picard/formats/`; `File.orig_metadata` versus `File.metadata`; reuse `picard/ui/metadatabox/` editing, multivalue handling, deleted/ignored fields and update suppression initially |
| Artwork | `picard/coverart/`, its provider infrastructure, metadata image collections and `picard/ui/coverartbox/`; preserve provider configuration, retrieval, processing, image selection and removal semantics |
| Saving | `Tagger.save(objects)` and `File.save()` with existing format writers, scripts, filename/move rules and artwork persistence; only explicit Save invokes this path |
| Progress/errors | `Tagger.tagger_stats_changed`, object state/pending/errors, webservice pending requests, status messages/logging and MainWindow processing/loading counters |
| Cancellation | Existing network abort APIs (`abort_task`, request shutdown), `Album.stop_loading()` and AcoustID `stop_analyze(file)`; cancellation has different scopes and is not one universal operation |
| Theme | `picard/ui/theme.py`: theme singleton, palette roles, accent/system-theme handling, `theme_changed` and `colors_changed`; retain platform theme support |

The service algorithms and domain behavior can be reused unchanged. Their current
window dependencies still require a compatibility contract; they are not headless
services that can simply be instantiated behind arbitrary panels.

AcoustID cancellation removes eligible queued tasks; it does not establish a
guarantee that every already-running fpcalc/network operation is killed. Preserve
the existing distinction between stopping queued work and aborting network work.
Never report a successful cancellation solely because a button was pressed.

## 3. UI/backend coupling to separate

1. **Application construction:** `Tagger` imports and constructs `MainWindow`
   directly and stores it as `window`. Later startup, session, dialog and shutdown
   paths depend on that object. A later opt-in window factory must preserve the
   original default and construct only one operational window.
2. **Window as service locator:** `Tagger` uses `window.ignore_selection_changes`,
   loading suspension contexts/enter/exit methods, status reporting and
   `window.panel.remove()`. `Cluster` calls processing/action enablement methods.
   These calls must remain functional during migration, with balanced counters.
3. **Album/editor coupling:** Album completion refreshes `window.metadata_box` and
   `window.cover_art_box`; album operations use `metadata_box.ignore_updates`.
   Preserve actual compatible editor/artwork instances until these call sites can
   be moved behind explicit adapter methods without altering behavior.
4. **File/player coupling:** file save logic accesses `window.player`. Replacing
   the shell must retain its expected lifecycle even though player redesign is
   outside this project.
5. **Selection ownership:** `MainWindow.update_selection()` and `selected_objects`
   coordinate actions, metadata and artwork. `MetadataBox` reads that selection
   and invokes window contexts/refreshes. Use one canonical domain-object
   selection and guarded synchronization, not parallel competing selections.
6. **Tree ownership:** `Item.ui_item` and `TreeItem.window` tie domain updates to
   stock tree items; tree updates use `window.panel`. Retain the stock view/model
   bridge internally until every relevant update/remove/selection path is
   accounted for. Flattening presentation must not flatten the domain graph.
7. **Actions/plugins/dialogs:** existing QAction enablement, shortcuts, plugin
   actions, authentication, options/setup dialogs, sessions, close confirmation
   and browser integration expect the window contract. A new toolbar cannot
   replace these with disconnected buttons.

Recommended transition: an independent `QMainWindow` shell with a narrowly named
compatibility bridge and existing widgets composed inside its panels. Do not
instantiate an invisible legacy MainWindow alongside it: that would introduce
two action/selection owners and competing lifecycles. Before switching startup,
inventory all `tagger.window`/`TreeItem.window` call sites, including plugins and
support dialogs. Retain supported compatibility attributes on the actual window
until callers can be adapted. Do not solve missing methods using silent no-ops.

## 4. Proposed modules and responsibilities

All paths in this section are proposed; none have been implemented.

```text
picard/ui/dlmtags/
    __init__.py
    mainwindow.py              # DLMtagsMainWindow: shell, ownership, splitters
    theme.py                   # DLMtagsTheme: facade over Picard's theme
    controller.py              # PicardUiController: intent → existing services
    compatibility.py           # explicit legacy window/panel contract
    selection.py               # canonical object selection + guarded projection
    models/
        songs.py               # read-only projection of actual File objects
        matches.py             # projection of actual backend match data
    components/
        card.py                # rounded surface + optional subtle shadow
        section_header.py      # title + real QAction controls
        action_button.py       # presentation of existing QAction
        table.py               # compact view defaults, focus and headers
        splitter.py            # responsive defaults + geometry validation
        styles.py              # scoped palette-based QSS, thin scrollbars
    panels/
        file_browser.py        # FileBrowserPanel: stock FileBrowser wrapper
        songs.py               # SongsPanel: flat File projection
        tag_editor.py          # TagEditorPanel: stock MetadataBox initially
        artwork.py             # ArtworkPanel: exactly one CoverArtBox
        matching_results.py    # MatchingResultsPanel: real data only
```

`DLMtagsMainWindow` owns panels/controller; the controller references the existing
Tagger, binds existing signals once, and disconnects when disposed. Panel signals
express user intent; widgets never start their own recognition clients, duplicate
rate-control queues or write media. Model rows retain File identity and, where
available, recording MBID, release MBID, release-track identity and source data.
Do not reconstruct domain identity from displayed artist/title strings.

The controller exposes load paths, select objects, analyze, metadata lookup,
cluster, preview/edit metadata, artwork actions, explicit save and scoped cancel.
It preserves the existing recognition configuration/consent dialogs and action
preconditions. Selecting a song or result must not save it. Existing matching may
update proposed in-memory metadata; preserve that distinction from disk writes.

Current Picard matching is not a general retained ranked-candidate feed. The local
`CandidateTable` is a summary of current selected-file metadata/similarity, not a
provider candidate store. Initially show only genuine backend state; leave an
honest empty state when no results are available. A richer candidate browser needs
a separately reviewed data boundary, not invented rows or confidence values.

### Layout contract

- One header above the workspace, with one single-line DLMtags title outside the
  sidebar card and real actions only.
- Outer horizontal splitter: sidebar; main content; artwork. Main content is a
  vertical splitter containing Tag Editor above Matching Results. Exactly one
  artwork instance; no accidental splitter children from temporary widgets.
- Sidebar card: Search, Computer Folders, then Songs in a resizable vertical
  splitter. No visible Files/Tags/Clusters tabs. Internal grouping remains active.
- Tag Editor displays original and proposed values using existing multivalue and
  edit semantics. Artwork keeps aspect ratio, existing navigation and manual
  editing actions.
- Cards use about 14 px corners, subtle shadows and 6–8 px spacing. Views have
  compact readable rows/headers; scrollbars are 6 px, rounded, without arrows.
  Horizontal bars use AsNeeded, never AlwaysOff for clipped navigable content.
- Stretch central content first; keep panes resizable with sensible minima.
  Validate restored splitter sizes against the current screen/DPI; use distinct
  DLMtags layout settings so old Picard geometry does not collapse navigation.
- FileBrowser starts/restores a configured directory or home, expands ancestors
  and retains directory expansion, supported-file filtering and load behavior.
  Explicitly parent its asynchronous filesystem model; preserve platform roots.

## 5. Migration strategy

1. **This phase:** establish isolation and this source-backed plan only. Keep all
   original local changes untouched; do not imply their fixes exist on this branch.
2. **Phase 2:** implement only the theme facade and reusable presentation
   components described below. Do not switch application startup or services yet.
3. **Compatibility contract:** enumerate and preserve startup/shutdown, selection,
   processing, action, editor, artwork, tree and player expectations. Add an
   explicit opt-in startup selection in a later authorized change; retain legacy
   UI as the default while integration is incomplete.
4. **Compose panels incrementally:** browser/Songs, editor, artwork, then matching
   results. Reuse stock widgets and service calls first. Port the prior browser
   fixes and selection synchronization deliberately, with provenance in review.
5. **Replace projections gradually:** replace tree-dependent presentation only
   after event/removal/grouping parity is demonstrated. Keep Album/Track/Cluster
   objects and ranking untouched. Never use display row numbers as identity.
6. **Later verification and cutover:** when authorized, verify loading, duplicate
   paths, async completion, edits, artwork, matching, explicit save, cancellation,
   shutdown, plugins and Linux/Windows behavior before changing the default UI.
   Keep rollback possible through the window factory, not destructive config edits.

## 6. Risks and compatibility

- **Isolation gap:** this worktree has upstream UI/theme/browser behavior, not the
  original DLMtags fixes. Do not mistake a clean checkout for completed migration.
- **Legacy contract completeness:** direct widget references and class-level
  TreeItem state make an immediate standalone shell unsafe without a call-site
  inventory. Document unsupported integrations before cutover rather than hiding
  failures behind placeholder objects.
- **Thread affinity/lifetime:** QObject ownership, PyQt6 `QtGui.QFileSystemModel`,
  queued callbacks, QProcess completion and view deletion need deliberate handling.
  Route worker results to the GUI thread using existing mechanisms; late results
  must not select removed files or access deleted panels.
- **Theme changes:** reuse active/inactive/disabled QPalette roles and both theme
  notifications. Avoid snapshots of colors that remain stale after theme changes.
  Scope QSS to the DLMtags root; do not restyle unrelated dialogs globally.
- **Metadata fidelity:** preserve list-valued tags, deletion state, original versus
  proposed values, release/recording distinction, images and capability checks.
- **Progress/cancel:** existing jobs differ in interruptibility. Do not advertise
  universal immediate cancellation or invent progress percentages.
- **Platform/accessibility:** home paths, Windows drives/UNC paths, macOS volumes,
  non-ASCII filenames, DPI, keyboard focus, screen-reader labels and contrast need
  subsequent verification. Thin scrollbars retain wheel/keyboard operation.
- **Licensing/resources:** retain all GPL notices when reusing code; use existing
  icons/translations and dependency set. Edit source `.ui` rather than generated
  `ui_*.py` where relevant. No assets or external providers are needed for Phase 2.

## 7. Exact Phase 2 plan — theme and reusable components

Phase 2 is future work requiring a separate implementation request.

1. Add `picard/ui/dlmtags/theme.py` with `DLMtagsTheme`, a presentation facade over
   the existing singleton. It must not construct another application theme,
   redefine UiTheme, infer system theme independently or override user settings.
2. If DLMtags palette customization is needed, review the original uncommitted
   semantic tokens explicitly and put accepted color definitions in existing
   `picard/ui/theme.py`. Map surface/border/text/accent to QPalette roles there;
   keep component code free of duplicate hardcoded RGB values. Preserve light,
   system and user accent behavior. Do not silently migrate the dark-default edit.
3. Add scoped `components/styles.py`: use a unique DLMtags root selector for card,
   table/header, search, focus, selection and scrollbar styling. Set vertical
   width/horizontal height to 6 px, round handles to 3 px, remove arrow subcontrols,
   use muted palette roles and restrained hover. Apply AsNeeded policies in view
   components. All colors resolve from the current theme palette.
4. Add `Card`, `SectionHeader` and `ActionButton`. Card owns a margin-8 layout and
   optional shadow whose tint is derived from theme colors. Buttons bind real
   QAction enabled/checked/text/icon/tooltip state. Components define no fake
   actions and make no Tagger/network/media calls.
5. Add compact table defaults (roughly 28 px rows, compact headers), keyboard/focus
   styling and a splitter helper (6–8 px handles, noncollapsed defaults, clamped
   restore sizes). Use font metrics/minimum sizes so larger system fonts remain
   readable. No Songs/match models or feature panels in this phase.
6. Connect theme/color notifications once, update any cached brushes and shadow
   colors, and disconnect appropriately. Reapply scoped presentation without
   recreating models, losing selection or changing filesystem paths.
7. Keep production startup unchanged. Deliver reusable components without an
   application screenshot/demo populated with fabricated backend data. Inventory
   changed files and document integration points for the following phase.
8. When execution is authorized later, add focused component verification for
   theme switching, style isolation, QAction synchronization, scrollbar policies,
   font scaling and resizing; follow `test/README.md` and its shared Qt fixture.
   Check keyboard navigation and Linux/Windows display behavior before rollout.

Phase 2 acceptance: a single source of color truth, reusable scoped components,
runtime theme responsiveness, no new dependencies, no production UI cutover and
no modifications to recognition, loading, saving or media. No Phase 2 code is
included in this report.
