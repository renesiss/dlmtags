# DLMtags Phase 2 — theme and reusable components

## Scope

Implemented in the `dlmtags-new-ui` worktree, branch `dlmtags-new-ui`.
The old worktree was not modified. Startup, MainWindow, recognition, metadata,
artwork, file loading and saving remain unchanged. No Phase 3 panels or controller
were implemented, and no previous UI patch was copied.

## Files

Modified:

- `picard/ui/theme.py`: added `DLMTAGS_COLOR_ROLES`, semantic aliases to existing
  QPalette roles. No RGB values, application palette changes or default changes.

Created:

- `picard/ui/dlmtags/__init__.py` and `components/__init__.py`: inert packages.
- `picard/ui/dlmtags/theme.py`: widget-owned `DLMtagsTheme` facade.
- `picard/ui/dlmtags/components/styles.py`: opt-in scoped QSS and idempotent
  `style_component()` installation.
- `picard/ui/dlmtags/components/card.py`: `Card` with 14 px radius, 8 px margins,
  6 px spacing and optional shadow (22 px blur, horizontal offset 2 px).
- `picard/ui/dlmtags/components/section_header.py`: `SectionHeader` with plain-text
  bold title and optional real QAction buttons.
- `picard/ui/dlmtags/components/action_button.py`: `ActionButton`, requiring a
  QAction and using native `setDefaultAction()` binding.
- `picard/ui/dlmtags/components/table.py`: `CompactTable`, a read-only QTableView
  with no internal data store, compact headers, row selection and font-aware rows.
- `picard/ui/dlmtags/components/splitter.py`: `Splitter` with 6 px handles,
  noncollapsible panes and validated, proportional size restoration.
- `docs/DLMTAGS_PHASE2_REPORT.md`: this report.

The Phase 1 architecture report remains present and unchanged.

## Theme ownership and runtime updates

`picard/ui/theme.py` remains the single owner of actual palette colors, dark/light
selection and accent handling. Semantic aliases are sidebar → AlternateBase,
panel → Base, text → Text, secondary text → PlaceholderText, border → Mid,
hover → AlternateBase, selection → Highlight, selection text → HighlightedText,
shadow → Shadow. Components use equivalent `palette(...)` QSS expressions;
the facade resolves cached effect colors from the widget's current color group.
There is no DLMtags RGB palette, theme detector or second application singleton.

Each styled widget owns a `DLMtagsTheme` QObject. Both existing Picard
`theme_changed` and `colors_changed` signals invoke its bound refresh slot.
Refresh reapplies only the widget's existing stylesheet to reevaluate palette
expressions, schedules repaint and emits `changed`. The Card updates its cached
shadow color on that signal and on PaletteChange. Shadow opacity is an effect
parameter, not a separate color definition. QObject parenting provides automatic
signal disconnection on destruction; repeated `style_component()` calls reuse the
direct-child facade rather than accumulating theme subscriptions.

Styling is set on individual opted-in widgets, never QApplication. Selectors
require a `dlmtagsComponent` property, so unrelated tables/dialogs are not globally
restyled. Dark/light and user-selected accents follow Picard's existing palette.
No calls to `theme.setup()`, `apply_theme()`, `setPalette()` or configuration writes
are added. Existing application startup does not import the new package.

## Phase 3 usage

Import components directly from their modules. Create them on the GUI thread,
after Picard's existing theme/application setup, with normal QWidget parents.

```python
from picard.ui.dlmtags.components.card import Card
from picard.ui.dlmtags.components.section_header import SectionHeader
from picard.ui.dlmtags.components.table import CompactTable

card = Card(parent, sidebar=True, shadow=True)
card.content_layout.addWidget(SectionHeader(translated_title, card, actions=(existing_action,)))
table = CompactTable(card)
table.setModel(existing_projection_model)
card.content_layout.addWidget(table)
```

These names represent caller-supplied real objects, not included demo data.
The caller translates visible titles, retains QAction ownership and supplies the
real model/selection controller. QAction enabled, checked, text, icon, tooltip and
trigger behavior are managed by QToolButton's native default-action binding; no
parallel clicked handler should invoke the same operation again.

Use `style_component(search_edit, 'search')` for an existing QLineEdit and
`style_component(file_browser, 'scrollArea')` for the real FileBrowser. The latter
adds only presentation and AsNeeded scrolling; it does not replace the filesystem
model, double-click handler or navigation state. Both axes remain accessible when
needed; vertical/horizontal bars are 6 px with rounded handles and zero-sized
arrow-button subcontrols. Folder rows are approximately 30 px. Table rows are at
least 30 px and grow with font metrics; tables have extended row selection and
editing disabled by default. The TagEditor must retain its actual edit semantics,
rather than using this read-only view indiscriminately.

`style_component()` owns the target widget's stylesheet. Do not overwrite an
existing custom stylesheet without reviewing it first. Configure any additional
component properties before installation, or explicitly reinstall styling after
changing them. A Card does not impose styling on arbitrary nested widgets; opt
them in deliberately. No component connects to MusicBrainz/AcoustID or stores
metadata. No disconnected feature buttons or placeholder operations are included.

Add panes to `Splitter`, assign their meaningful minimum sizes/stretch factors,
then call `restore_sizes(positive_integer_sizes)` after establishing geometry.
The helper scales proportions to its current extent, subtracts handle space and
lets Qt enforce pane minima. Invalid/mismatched/zero sizes return False for the
caller to supply defaults. Save sizes under future DLMtags-specific settings;
this phase creates no settings, screens, startup hook or persistence mechanism.

## Review and remaining integration risks

- Python syntax was parsed using `ast.parse` without importing application code,
  creating bytecode or instantiating Qt objects. Whitespace diff review passed.
- Constructor/method/enum usage was checked against the installed PyQt6 `.pyi`
  files, including QObject child lookup, native QAction binding, shadow offsets,
  header sizing, splitter sizes and font/palette events.
- Ruff and ty are unavailable on PATH and in the checked local tool locations;
  no dependencies were installed and those checks were not run.
- No application launch, builds, tests, commits, pushes or media/database writes.
  Actual QSS rendering, native-platform differences, runtime theme transitions,
  signal cleanup, action destruction, splitter restoration and font/DPI scaling
  still need runtime verification when authorized.
- Card shadows may be clipped if future parents omit room for effects. Corner
  radius paints the card surface; it does not mask arbitrary child contents.
  Keep the supplied inset layout and avoid nested opaque panel borders.
- Splitter restoration must follow pane construction and meaningful geometry;
  screen changes/minimum sizes can constrain requested proportions. No promise
  of pixel-identical restored sizes is made.
- Existing Picard window/tree/editor coupling documented in Phase 1 is untouched.
  Components are ready for composition, not a replacement functioning MainWindow.

Stopped after Phase 2. Phase 3 has not begun.
