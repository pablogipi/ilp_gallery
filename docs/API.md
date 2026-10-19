# ILP Gallery API

The gallery is a Qt component that shows **records**: plain dicts describing one image, GIF, movie or asset each. Everything in this API exists to produce records, react to them, or move them into a host application.

```
DataSource ──records──▶ GalleryLibrary ──▶ GalleryWidget / GalleryBrowser
 (JSON, Megascans,       (items + search      │  register_exporter()     → your ingest / publish code
  folder, ShotGrid, …)    index, Qt-free)     │  register_drag_handler() → what a drag carries into Nuke/Houdini/Maya
                                              │  add_action()            → extra context-menu entries
```

## 1. Records: the internal format

`make_record()` builds one with named arguments. It returns the same dict the JSON library files store.

```python
from ilp_gallery.core import make_record

make_record(
    "/show/pub/chairA_model_v012.usd",     # path: what is shown/played, or the asset file
    id="sg-pub-4411",                      # unique across sources (default: the path)
    name="chairA model",
    thumbnail="/show/pub/chairA_v012.jpg", # optional tile image
    category="asset",                      # main category: item.category
    categories=["Model", "chairA"],        # flat, searchable with cat: (default: category + tree levels)
    tags=["hero", "approved"],             # searchable with tag:
    description="Hero chair, lookdev ready",
    type_label="Model",                    # subtitle under the tile
    category_paths=[["Asset", "chairA", "Model"]],  # hierarchy in the category tree
    display={"Version": 12, "Artist": "pablo"},     # details-panel rows, in order
    tile_aspect=1.0,                       # square tiles (default 16:9)
    preview="/show/pub/chairA_v012.mp4",   # optional: a movie that scrubs on hover and plays
    version=12, sg_id=4411,                # any extra keys → metadata (meta: search, exporters)
)
```

Reserved `metadata` keys the UI reads: `type_label`, `category`, `category_paths`, `display`, `tile_aspect`, `preview`. On a `GalleryItem` they are also exposed as `item.type_label`, `item.category`, `item.category_paths`, `item.display` and `item.preview`.

`category` is the item's main category. When it's given, `category_paths` (the tree) defaults to `[[category]]`, and `categories` (searched by `cat:`) defaults to the category plus every level of the tree paths. Without it, `item.category` is the first tree level, or the first of `categories`.

**Previews.** Items that don't move themselves (a published clip, a model, a rig) can carry a `preview`. A movie or GIF scrubs on hover, plays in the details panel and gives the tile its poster (first frame) unless a `thumbnail` is set; a still image is used as the thumbnail. `item.motion` is the `(path, type)` that plays: the item itself if it's a movie, else its preview. Items with nothing to show get a warning icon (Houdini's own inside Houdini); `gallery.set_placeholder("icon.svg")` changes it. `item.value("json")` reads any field or metadata key.

## 2. Data sources

This is modelled on Houdini's `hou.AssetGalleryDataSource`. Subclass `DataSource` and implement `load()`; everything else is optional.

| Method | Called | Purpose |
|---|---|---|
| `load()` **required** | on add / reload, **worker thread** | return every record (or `GalleryItem`) |
| `poll()` | every `poll_interval` s, worker thread | return `Changes(updated=[…], removed=[ids], reset=False)` or `None`; Houdini's `updatedItemIds` |
| `thumbnail(item)` | thumbnail worker thread | image **bytes** or a file path for items without a local thumbnail; cached on disk |
| `rebuild(progress, log)` | "Rescan library" button | regenerate what `load()` reads (e.g. rescan disk into a cache) |
| `set_tags(item, tags)`, `set_metadata(item, md)` | `library.set_tags()` | write back; implementing either makes `is_read_only()` return False |

```python
from ilp_gallery.core import DataSource, Changes, make_record

class Publishes(DataSource):
    name = "publishes"            # key inside the library; items remember it (item.source)
    label = "Published assets"    # shown in the log
    poll_interval = 60            # seconds; 0 = never poll

    def load(self):
        return [make_record(p.path, id=p.uid, name=p.name, type_label=p.kind) for p in query()]

    def poll(self):
        changed = query(updated_since=self.last)
        return Changes(updated=[...]) if changed else None
```

Built-in sources:

- `JsonFileSource(path)`: a `.json` / `.json.gz` library or a folder of them. It polls the file's modification time and reloads when the file is rebuilt.
- `MegascansSource(cache, root)`: the shared Megascans cache. `rebuild()` rescans the download folder incrementally.
- `FolderSource(folder)`: every image or movie in a folder tree; sub-folder names become categories.
- `RecordsSource(list_or_callable)`: quick tools and tests.

`examples/shotgrid_source.py` is a complete ShotGrid published-files source:

- `load` runs a `sg.find` over the project's publishes.
- `poll` queries for publishes with a newer `updated_at`.
- `thumbnail` downloads the pre-signed image URL.
- `set_tags` writes tags back to ShotGrid.

Adding sources:

```python
browser.add_source(Publishes())            # background load; sourceLoaded / sourceFailed signals
library.add_source(Publishes())            # Qt-free, blocking (farm, tests)
browser.load("/ice/.../megascans.json.gz") # path → the right source automatically
```

Several sources can live in one gallery. Reloading or polling one only touches its own items.

## 3. Export callbacks

```python
def ingest(json_file, log):                       # `log` optional: writes to the Export log
    log(f"ingesting {json_file}")
    ilp_ingest.megascans(json_file)

browser.register_exporter(ingest, label="Ingest into project", arg="json",
                          default=True, sources=["megascans"])
```

| Option | Meaning |
|---|---|
| `arg` | what the callback receives: `"item"` (GalleryItem), a field (`"path"`, `"id"`, `"name"`, `"thumbnail"`) or a metadata key (`"json"`, `"asset_dir"`, `"sg_id"`, …) |
| `batch=True` | one call with the list of values instead of one call per item |
| `threaded=True` (default) | worker thread: the UI stays responsive and **Stop** works between items |
| `threaded=False` | GUI thread: **required for host scene APIs** (`hou`, `maya.cmds`, `nuke`) |
| `sources=[...]` | only offered for items from these sources |
| `default=True` | runs from the Export button; the others go in its ▾ menu and in the context menu |

The callback returns `False`, or raises, to mark an item as failed. The error is logged and the export carries on with the next item. The Settings "Export command" still works too, as a built-in exporter that runs a command template per item.

**Naming the main action.** The Export button shows the default exporter's `label`, so `register_exporter(ingest, label="Ingest", default=True)` gives an "Ingest" button and "Ingest selected" in the toolbar. `gallery.set_export_label("Assign")` renames it regardless of the exporter (`None` restores it); `exportLabelChanged` reports changes.

**Preview command.** Opens an item's preview movie in a player, from the Preview button (shown for items that have something to play), the context menu, or `gallery.preview(items)`:

```python
gallery.set_preview_command("rv {preview}")      # one player per item
gallery.set_preview_command("rv {previews}")     # the whole selection in one player
```

It runs through the shell and detached, so the player doesn't block the gallery. In the browser it's also in Settings ("Preview command", default `rv {preview}`, or `ILP_GALLERY_PREVIEW_CMD`).

Command templates (export and preview) also accept **any metadata key** with a plain value, so a record can keep a light preview for the gallery and point the player at the full movie:

```python
make_record(clip, preview=".../clip.gif", movie=".../clip.mp4")   # the GIF scrubs, RV gets the mp4
gallery.set_preview_command("rv {movie}")
```

The Preview button and menu entry appear for items that have what the template needs: something to play for `{preview}` / `{previews}`, and the metadata keys it uses (`{movie}`). Built-in placeholders take priority over metadata keys of the same name; unknown placeholders are left as they are.

## 4. Drag handlers

```python
browser.register_drag_handler(lambda items: [texture_files(i, ["albedo"])["albedo"] for i in items],
                              sources=["megascans"])
```

A handler receives the dragged items and returns one of:

- a list of paths, sent as file URLs plus text
- a `str`, sent as text
- a `{mime: str | bytes}` dict
- a `QMimeData`
- `None`, to add nothing

Results are merged on top of the default drag data, which is the file URL of `item.path` plus the JSON records under `application/x-ilp-gallery-items`. Pass `replace_default=True` to drop the default file URL and text; the JSON records are always kept, because the drawer reads them. A handler that raises is logged, and the drag still happens.

**A different drop per DCC.** `hosts=` limits a handler to some applications (`"houdini"`, `"nuke"`, `"maya"`, `"standalone"`), so one setup can register them all and the right one is used:

```python
g.register_drag_handler(clip_to_houdini, hosts=["houdini"])
g.register_drag_handler(clip_to_maya, hosts=["maya"], replace_default=True)
g.register_drag_handler(lambda items: [i.preview for i in items], hosts=["nuke"])   # Nuke gets the movie
```

`ilp_gallery.dcc.install_host_defaults(gallery)` registers examples per host:

| Host | Registered |
|---|---|
| All hosts | a drag with the best file per item (the albedo texture for Megascans) |
| Nuke | "Create Read nodes" |
| Houdini | "Create File COPs" |
| Maya | "Image plane on camera" |

The host exporters run with `threaded=False`, since they call the host's scene API. Pass `host_defaults=False` to `dcc.show()` / `make_browser()` to set up your own instead.

> These host examples haven't been run inside Houdini, Nuke or Maya yet: treat them as starting points.

## 5. Drawer

A pane to the right of the tiles that collects chosen items: a batch to process, or the clip list of a host node.

```python
g = GalleryWidget(drawer=True, drawer_key="name", drawer_title="Clips")
g.drawerChanged.connect(lambda entries: save(entries))     # edits made in the drawer UI
g.set_drawer(load())                                         # silent: no drawerChanged
```

- **Entries** are `DrawerEntry(key, path, enabled)`. `key` is the value of the `drawer_key` field (default `"name"`), so an entry survives a new version with a new path. `set_drawer()` also takes `(key, path, enabled)` tuples, `{"key", "path", "enabled"}` dicts or bare keys.
- **States** against the library: `ok` (the stored path matches), `outdated` (the library has another path: shown in orange, with "Update to latest" per entry and "Update all") and `missing` (the key isn't in the library: kept, shown greyed out). Duplicate key values in the library are logged; the last loaded wins.
- **Editing:** "Add to drawer" heads the tile context menu; tiles can be dropped on the drawer; rows reorder by drag, and have an enable checkbox, Remove, Clear and a context menu (Update to latest, Enable, Disable, Show in gallery).
- **Assigned category:** with a drawer, the categories tree gets a pinned row under "All assets", in italics, listing the items in the drawer with their count. It follows the drawer as entries are added or removed, and survives reloads and the UI state. `drawer_category="In batch"` renames it; `None` leaves it out.
- **Tile badges:** a filled disc with a tick for enabled entries and a ring for disabled ones; green when the path is current, orange when it's outdated (`MARK_OK` / `MARK_OUTDATED` in `ui/delegate.py`).
- **What an entry stores:** `drawer_key` is the identity field and `drawer_path` the field kept as its path, so a Megascans batch can use `GalleryBrowser(drawer=True, drawer_key="id", drawer_path="json")`. For full control set `gallery.drawer.entry_factory` to a function `GalleryItem -> DrawerEntry | (key, path, enabled) | None` (None: can't be added).
- **Running on the drawer:** once an exporter is registered, the drawer shows a run button with the main action's label ("Ingest (12)"). It calls `export_drawer()`, which runs the default exporter, or the one passed in, on the enabled entries in drawer order and skips missing ones.
- **Thumbnails:** rows use the item's thumbnail; an item with no usable media, or an entry that isn't in the library, gets the same "no preview" icon as the tiles.
- **Preview:** clicking a row shows its library item in the details panel when the panel is open. An outdated entry previews the library's current version.
- **Removing by drag:** rows dragged out of the drawer onto the gallery are removed.
- **Context menu actions:** `add_drawer_action(label, callback, single=False)` adds an entry at the top of the rows' context menu; `callback` receives the selected `DrawerEntry` objects in drawer order, and `single=True` offers it only when one row is selected. The built-in entries follow: Update to latest, Enable, Disable, Show in gallery, Remove Item. A callback that raises is logged.
- `add_to_drawer(items)`, `drawer_entries()` and `drawer_items(enabled_only=False)` (the library items, in drawer order) round out the API.

Tiles dropped anywhere else on the gallery are swallowed, so a host never receives a drop the user made on the gallery itself.

## 6. Library files and performance

At 18,886 assets:

| Step | Time |
|---|---|
| JSON parse | ~0.55 s (binary formats such as pickle measured the same) |
| Building the search index | ~0.55 s |
| Window appearing (loading in the background) | ~50 ms |
| All assets in (background) | ~1.4 s |
| Time the UI thread is blocked | ~8 ms |

- **Compression** is what helps over the network. Name the file `.json.gz` and it's written and read gzip-compressed (`ilp-gallery megascans -o megascans.json.gz`).
- **Detection:** any extension works, because files are recognised by their content.
- **Search backend:** SQLite FTS5 when the host's `sqlite3` supports it. Otherwise (Nuke's bundled Python has no FTS5) a pure-Python index with the same queries, results and ranking takes over automatically. At 20k items it takes ~90 ms per query instead of ~10 ms. `ILP_GALLERY_SEARCH=fts5|python` forces one; `library.index.backend` says which is in use.
- **Live rebuilds:** clients poll the file and reload when it's rebuilt, so rebuilding the cache on a schedule updates open galleries.

## 7. Threading rules

- These run on worker threads: `load()`, `poll()`, `thumbnail()`, `rebuild()` and threaded exporters. **Don't touch Qt widgets or host scene APIs in them.**
- Anything that edits a Houdini, Nuke or Maya scene: register it with `threaded=False`.
- The library is thread-safe for the gallery's own use: workers prepare items, and the GUI thread applies them.

## 8. Look

By default the gallery matches the application it runs in. Inside Houdini it reads Houdini's colour scheme (`hou.qt.getColor`: background, text, buttons), since Houdini's Qt palette doesn't follow schemes like Houdini Light. In Nuke and Maya it derives the colours from the host's Qt palette. Either way it keeps the host's font size, and it follows the host when its colours change: on a palette change, and whenever the panel is shown. Standalone, it uses the built-in ILP look.

The derived colours are sanity-checked: panels stay on the same side of light/dark as the window, and text, selected rows and links keep readable contrast, whatever the host reports.

```python
from ilp_gallery.ui import ILP, Theme

make_browser(theme="auto")                          # default: host look in a DCC, ILP standalone
make_browser(theme="host")                          # always derive from the app's palette
make_browser(theme="ilp")                           # always the ILP look
make_browser(theme=ILP.replace(accent="#e08a3d"))   # any Theme; .replace() changes a few colours
browser.set_theme("ilp")                            # switch live
```

`ILP_GALLERY_THEME=auto|host|ilp` overrides what `"auto"` picks; an explicit `theme=` argument still wins. `browser.theme` is the `Theme` in use. `gallery.refresh_theme()` re-reads the host's colours by hand, and `gallery.themeChanged` fires with the new `Theme`.

> Houdini Light was checked against values reported from a real session; the Nuke and Maya looks with palettes like theirs.

### SGI themes

A modern take on IRIX's Indigo Magic look, in `ilp_gallery.ui.sgi`:

```python
make_browser(theme="sgi")         # light IRIX look: warm greys, slate-blue desktop, salmon fields
make_browser(theme="sgi-dark")    # the same widgets in Autodesk Flame's palette
make_browser(theme="sgi-slate")   # blue-slate dark variant

from ilp_gallery.ui.sgi import SGI
make_browser(theme=SGI.replace(accent="#2a7f62"))
```

What they add beyond colours:

- Motif bevels with mitered corners, the embossed IRIX check mark, diamond radio buttons with the blue arrow, flush sliders, slanted italic tabs, bevelled tiles.
- Scrollbars outside their list's frame, with square three-step buttons and a ridged thumb.
- IRIX shortcut marks: a small staircase under the mnemonic letter (`"&File"`) instead of an underline, in menus, labels, check boxes and buttons. Submenus are marked with the Toolchest cabinet.
- IBM Plex Sans and Plex Mono at 9.5pt, bundled (SIL Open Font License, `ui/sgi/fonts/OFL.txt`) and registered for the application only.

The pieces are generated as SVGs from the theme's colours into `~/.cache/ilp_gallery/sgi` (set `ILP_GALLERY_SGI_CACHE` to move it). Details a stylesheet can't express (detached scrollbars, shortcut marks) come from a small proxy style that the theme installs on the gallery's own widgets only; switching to another theme removes it again. The host application's widgets are never touched.

Menus you create yourself inside the gallery get the shortcut marks with `gallery.theme.polish_menu(menu)` (a no-op for other themes).

`ThumbWheel` is the IRIX thumbwheel as a `QAbstractSlider`, for your own tools: `ThumbWheel(Qt.Horizontal, theme)`. It works with any theme.

### Fusion style

The three SGI palettes also come in Qt's built-in Fusion style: same colours, modern widgets. Six looks in all:

| | light | dark (Flame) | slate |
|---|---|---|---|
| SGI style | `"sgi"` | `"sgi-dark"` | `"sgi-slate"` |
| Fusion style | `"fusion"` | `"fusion-dark"` | `"fusion-slate"` |

```python
make_browser(theme="fusion-dark")                  # by name
make_browser(theme="sgi-dark", style="fusion")     # or palette + style
browser.set_theme("sgi-slate", style="fusion")     # live
```

`style=` accepts `"sgi"` or `"fusion"` with any of the SGI palettes, including tweaked ones (`SGI.replace(...)`). Fusion is applied to the gallery's own widgets only, like the SGI style, and switching looks live removes whatever the previous one installed.

## 9. Panes and top bar

The layout is: categories | tiles | a right column where the drawer (top) and the details panel (bottom) share the height, so the tiles keep the middle.

Every pane collapses. Its title sits in a tab with a small arrow: the arrow (or a double-click on the tab) collapses the pane, and its tab moves to a narrow strip on that side of the gallery. Collapsed panes on the same side stack their tabs in one strip, top to bottom, like Maya's side panels; the categories and tiles fold to the left, the drawer and details to the right. Clicking a tab expands the pane at its previous size, and holding a drag over a tab opens it, so tiles can still be dropped on a folded drawer.

```python
g = GalleryWidget(drawer=True, collapsed=("details",), tile_size=90, size_control="wheel")
g.set_collapsed("drawer", True)
g.is_collapsed("drawer")            # True; the drawer still works (has_drawer() stays True)
g.add_toolbar_widget(QLabel(node.path()))   # beside the size control
```

- **Pane names:** `"tree"`, `"grid"`, `"details"`, `"drawer"`; the panes are in `g.panes`, the layout in `g.area`. When one pane of the right column collapses the other takes its height; when both do, the column's width goes to the tiles. A collapsed details panel stops the player and isn't updated until it opens again. The drawer's tab shows its count ("Clips: 4"). `show_sidebar` / `show_details` / `drawer` remove a pane completely (no tab).
- **Tile size:** `tile_size` sets the starting width (90 to 420; 90 is the most compact). `size_control="wheel"` swaps the slider for the SGI thumbwheel (drag it like ivview's Dolly wheel); it takes its colours from any theme. `size_label=False` drops the "Size" label in front of the control, and `media_badges=False` hides the MOV / GIF labels on the tiles.
- **Second row:** `add_toolbar_widget(widget, row=1)` puts widgets in a row under the search bar, left to right in the order added (`side="right"` right-aligns them). The row stays hidden until something is added; it suits a host node's own controls.
- **Tiles pane title:** the tab shows the data source's `label` when the gallery has one source ("Motion Clips"), else "Assets"; `grid_title=` or `set_grid_title()` sets it.
- **Toolbar icons:** `add_toolbar_button(text, callback, tooltip, icon=...)` takes a `QIcon` or `"reload"` (the platform's refresh icon); with an icon, `text` can be `""`.
- **Top bar:** `add_toolbar_widget(widget, side="left")` places a widget beside the size control, `side="right"` right-aligns it (before any buttons). `add_toolbar_label(text, side="right", framed=True)` adds a selectable label with a border in the theme's colours, e.g. a host node's path. `add_toolbar_button()` still adds buttons on the right.

## 10. UI state

`ui_state()` returns what the user set up, as plain JSON-able data: the search text, the selected category, the collapsed panes, the tile size and the pane sizes. `set_ui_state(state)` puts it back (a dict or its JSON); `ui_state=` does the same at construction. A category that isn't loaded yet is selected once its items arrive.

```python
g = GalleryWidget(drawer=True, ui_state=node.parm("gallery_ui_state").eval())
g.uiStateChanged.connect(lambda state: store(json.dumps(state)))
g.save_ui_state("/tmp/gallery.json"); g.load_ui_state("/tmp/gallery.json")
```

`uiStateChanged` is debounced (0.4 s) and only fires for real changes made by the user: restoring a state doesn't fire it, and pane sizes count only when a splitter is dragged (not when the window or layout resizes them). Keys missing from a state are left as they are, so a host can store only part of it.

`set_drawer_current(key)` marks one drawer entry as current (bold, on a tint of the theme's accent), e.g. a host node's current clip; `None` clears it.

## 11. Display names

`name_format` (constructor or `set_name_format()`) changes how items are *shown*, not what they are: search and drawer keys keep the full name, and every tooltip starts with it. It's a function `GalleryItem -> NameParts | (title, subtitle, chip) | str | None` that can read anything the record carries; `None` (or an error) shows the item as usual. Drawer entries that aren't in the library are passed as a stand-in item with only the name (`stub_item()`).

```python
from ilp_gallery.ui.names import NameParts, acronym

def clip(item):
    md = item.metadata
    action = md.get("type_label")
    if not action:
        return None
    category = item.category
    moves, variations = md.get("movement_var") or [], md.get("var") or []
    return NameParts(" · ".join([action, *moves, *variations]), md.get("actor") or "",
                     chip=acronym(category), wrap=True,
                     details=[("Category", category), ("Action", action),
                              ("Movement Variations", ", ".join(moves)), ("Variations", ", ".join(variations))])

GalleryWidget(drawer=True, name_format=clip, subtitles="when_needed", show_paths=False)
```

- **Tiles:** `title` on the first line; with `wrap=True` what doesn't fit carries on in the second line (split at " · ", then at spaces). Otherwise the second line is `subtitle` (instead of the type label). `chip` is a small coloured tag in the image's top-left corner.
- **Second line only when needed:** `subtitles="when_needed"` (or `set_subtitles()`) shows the subtitle only when another item in the library has the same title, e.g. two actors' takes of "run · fwd · 001"; on a wrapped title it follows the overflow. `"never"` hides it, `"always"` (default) shows it. The drawer always shows it.
- **Drawer rows:** the title, then the subtitle in the muted colour on the same line; the chip's colour as a border around the row's thumbnail.
- **Tooltips and the details panel:** the full name, then the `details` rows (label, value). With details, the panel shows them (followed by the record's own `display` rows) instead of the category breadcrumbs and tags.
- **Preview title:** when the format returns `details`, the details panel shows the short `title` as its heading; the full name is its tooltip.
- **Paths:** `show_paths=False` keeps file paths out of the UI: tooltips, the drawer's tooltips ("A newer version is in the library" instead of the new path), the details panel's Location and its "Open folder" button.
- **Colours:** each chip text always gets the same muted colour; `NameParts(chip_color="#rrggbb")` sets one explicitly. `acronym(word)` makes 3-letter tags: the first letter and the next consonants ("locomotion" → LCM, "combat" → CMB).

## 12. Shared libraries

Every gallery normally has its own library, data sources and thumbnail cache. In a host where several panels show the same items (a Parameters pane per node editor, floating parameter windows), pass the same `shared` key to all of them:

```python
gallery = GalleryWidget(shared="motion_clips", drawer=True)
source = gallery.add_source(MotionClipsSource())    # returns the source actually in use
```

- **One library, loaded once.** Galleries with the same key use one `GalleryLibrary` (items and search index) and one `ThumbnailCache`, kept for the life of the process. The first gallery loads a source; later ones get the already loaded items at once: `add_source()` reuses the registered source with that name and returns it, so keep the return value (e.g. for a Refresh button).
- **Changes reach every gallery.** A reload or `poll()` applied by any of them updates all of them: tiles, drawer states, the Assigned category, an open details panel.
- **One poller.** Only one of the galleries polls the sources; when it closes or its panel is deleted, another takes over.
- **Per-gallery state stays separate:** search, category, collapsed panes, drawer contents, selection.
- Listeners are held weakly, so a host deleting a panel without closing it doesn't leave a dead listener behind. `ilp_gallery.ui.shared.drop_shared(key)` forgets a shared library.

## 13. State saved in the scene

`ilp_gallery.ui.persist.bind_state(gallery, key)` keeps a gallery's UI state in the open scene: it's restored when the gallery is built and whenever a scene is opened, saved on every change, and reset to the gallery's defaults on File > New.

```python
from ilp_gallery.ui.persist import bind_state
bind_state(gallery, "motion_clips_panel")          # after sources and options are set up
```

- **What:** `ui_state(include_drawer=True)`: search, category, collapsed panes, tile size, pane sizes and the drawer's entries (`include_drawer=False` leaves the drawer out, e.g. when a node already stores it).
- **Where:** Houdini, user data on `/obj` (saved in the .hip); Maya, `fileInfo`; Nuke, a hidden knob on the Root node; standalone, QSettings (per user). Values are base64 JSON in Maya and Nuke, which escape text their own way.
- **A node's parameter:** `HoudiniParmStore(node_or_callable, "gallery_ui_state")`, e.g. for a Parameters-pane panel: pass a callable returning the node it shows, and call `binding.restore()` when that node changes. Writes aren't undo steps.
- **Other stores:** pass `store=` any object with `read(key)`, `write(key, text)` and optionally `watch(callback) -> unwatch` (`callback("load" | "clear")`). `MemoryStore` is one for tests.
- Galleries bound to the same key share one entry: the last change wins.

`GalleryBrowser` passes anything it doesn't define itself through to its `GalleryWidget` (`reload_source`, `add_toolbar_button`, the drawer and state API), and its `add_source()` returns the source in use, like the widget's.

## 14. Log pane

`GalleryWidget(log=True)` adds a **Log** pane along the bottom, full width: every `logMessage` (loads, polls, exporter output, errors with their traceback indented underneath) with a time stamp, a **Clear** button, and **Stop** while a stoppable task (a threaded export) runs. It collapses downward to a tab in a strip along the bottom (`collapsed=("log",)` builds it folded; the state is saved like the other panes); while folded, its tab counts new messages ("Log (3)"). `logMessage` still fires as before, so a host can send the same messages elsewhere too, e.g. Houdini's status bar.

`PaneArea.add_pane(..., where="bottom")` is the generic slot it uses: full-width panes under the rest, folding to tabs in the bottom strip.

**Copy path / Copy command.** The details panel's copy button, next to the main action, copies the item's path ("Copy path"), or its export command when a command template is set in Settings ("Copy command"); the label follows `command_template`.

## 15. View (RV)

"View" opens items in an external viewer, RV by default: double-click a tile or a drawer row, **View** in the tiles' and the drawer's context menus, or the **View** button next to Copy path in the details panel.

- **The command** is a template, run through the shell (`/bin/sh -c`, as one line) and detached: `"rv {previews}"` by default (every chosen item in one RV). `{preview}` runs one command per item. Placeholders are those of the export command; values are shell-quoted, so paths with spaces stay one argument. `command_shell=False` runs the program directly instead (no PATH tricks, wrappers or `&&`).
- **What it opens:** the item's movie or GIF, or its `preview`. `view_field="movie"` opens that record field instead, e.g. a full quality QuickTime next to a light preview GIF; items without it can't be viewed.
- **Errors:** a viewer that exits with an error within ~2 s is reported in the log with its last stderr lines (a missing program shows as exit code 127, "not found").
- **Override it** per gallery with `view_command=` or `set_view_command()`, or for everyone at once with the environment variable `ILP_GALLERY_VIEW_COMMAND`, e.g. `rez-env rv -- rv {previews}` where RV comes from rez. `view_command=""` turns viewing off (no menu entries, no button, double-click plays inline). In the standalone browser, Settings > Preview command does the same.
- **Double-click** views when the command's program is on PATH; otherwise it plays the item in the details panel as before. The menu entries and the button always try, and a missing program is reported in the log with a hint.
- Items qualify when they have something to play (a movie or GIF, or a `preview`).
