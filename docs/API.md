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
    categories=["Model", "chairA"],        # flat, searchable with cat:
    tags=["hero", "approved"],             # searchable with tag:
    description="Hero chair, lookdev ready",
    type_label="Model",                    # subtitle under the tile
    category_paths=[["Asset", "chairA", "Model"]],  # hierarchy in the category tree
    display={"Version": 12, "Artist": "pablo"},     # details-panel rows, in order
    tile_aspect=1.0,                       # square tiles (default 16:9)
    version=12, sg_id=4411,                # any extra keys → metadata (meta: search, exporters)
)
```

Reserved `metadata` keys the UI reads: `type_label`, `category_paths`, `display`, `tile_aspect`. On a `GalleryItem` they are also exposed as `item.type_label`, `item.category_paths` and `item.display`. `item.value("json")` reads any field or metadata key.

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

Results are merged on top of the default drag data, which is the file URL of `item.path` plus the JSON records under `application/x-ilp-gallery-items`. Pass `replace_default=True` to drop the default. A handler that raises is logged, and the drag still happens.

`ilp_gallery.dcc.install_host_defaults(gallery)` registers examples per host:

| Host | Registered |
|---|---|
| All hosts | a drag with the best file per item (the albedo texture for Megascans) |
| Nuke | "Create Read nodes" |
| Houdini | "Create File COPs" |
| Maya | "Image plane on camera" |

The host exporters run with `threaded=False`, since they call the host's scene API. Pass `host_defaults=False` to `dcc.show()` / `make_browser()` to set up your own instead.

> These host examples haven't been run inside Houdini, Nuke or Maya yet: treat them as starting points.

## 5. Library files and performance

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

## 6. Threading rules

- These run on worker threads: `load()`, `poll()`, `thumbnail()`, `rebuild()` and threaded exporters. **Don't touch Qt widgets or host scene APIs in them.**
- Anything that edits a Houdini, Nuke or Maya scene: register it with `threaded=False`.
- The library is thread-safe for the gallery's own use: workers prepare items, and the GUI thread applies them.

## 7. Look

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
