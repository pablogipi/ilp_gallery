# ILP Gallery

A PySide6 gallery for images, GIFs and movies that runs on its own or inside Houdini, Maya and Nuke. It has hover-scrub thumbnails, a built-in player and fast search over JSON records.

```
┌──────────────── ilp_gallery.core (no Qt) ───────────────┐
│ GalleryItem   one JSON record                           │
│ GalleryLibrary   the middle layer: loads/saves JSON,    │
│               holds items, notifies listeners           │
│ SearchIndex   FTS5 or pure Python, derived from library │
└──────────────────────────────────────────────────────────┘
┌──────── ilp_gallery.media ────────┐ ┌──── ilp_gallery.ui ────┐
│ ffmpeg   probe / extract frames   │ │ GalleryWidget (embed)  │
│ ThumbnailCache  threads + disk +  │ │ GalleryView + Delegate │
│                 memory LRU        │ │ PlayerWidget, Search   │
└───────────────────────────────────┘ └────────────────────────┘
ilp_gallery.dcc   hosts: show(), show_maya(), Nuke panel, Houdini panel
```

## Quick start

```bash
pip install -e ".[standalone,test]"         # or, with rez: rez-env ilp_gallery (puts python/ on PYTHONPATH)
python examples/make_demo_library.py /tmp/gallery_demo
ilp_gallery /tmp/gallery_demo/library.json
```

Other commands:

```bash
ilp_gallery scan /jobs/lib/clips -o clips.json        # JSON from a folder tree (folders -> categories)
ilp_gallery search clips.json "tag:walk -loop"        # headless search, prints paths
ilp_gallery thumbs clips.json --threads 8             # pre-generate thumbnails (e.g. on the farm)
```

Requirements: Python 3.10+, PySide6 6.5+, and `ffmpeg`/`ffprobe` on `PATH` for movies (or set `ILP_GALLERY_FFMPEG` / `ILP_GALLERY_FFPROBE`).

## JSON records

Only `path` is required. A file holds either a list of records or `{"items": [...]}`. A folder source loads every `*.json` in it, so one JSON per published asset works too. Relative paths resolve against the JSON file's folder.

```json
{
  "id": "crowd_walk_001",
  "name": "Crowd walk cycle 01",
  "path": "/jobs/lib/clips/crowd_walk_001.mov",
  "type": "movie",
  "categories": ["crowd", "locomotion"],
  "tags": ["walk", "male", "loop"],
  "description": "Neutral walk, loopable",
  "thumbnail": null,
  "metadata": {"fps": 24, "frames": 48, "source": "mocap"}
}
```

Defaults when fields are missing:

- `id` defaults to the path.
- `name` defaults to the file name without its extension.
- `type` (`image` | `gif` | `movie`) is guessed from the extension.

Also handled:

- A `thumbnail` path, if given, overrides the poster frame.
- Keys the gallery doesn't know are kept when the JSON is saved again.
- If two records share an `id`, the one loaded last wins.

## Megascans library

```bash
ilp_gallery megascans -o /ice/resources/ASSET_LIB/megascans/gallery/megascans.json.gz   # .gz = compressed
ilp_gallery /ice/resources/ASSET_LIB/megascans/gallery/megascans.json.gz
```

The importer walks `Downloaded/{3d,3dplant,atlas,surface}/<asset>/<id>.json` (change the location with `--root` or `ILP_MEGASCANS_ROOT`) and builds one record per asset:

- **Categories:** the top folder first, then the JSON `categories`.
- **Tags:** the JSON `tags`, plus the useful words from `semanticTags` (`contains`, `descriptive`, `environment`, …) and `assetCategories`.
- **Tile image:** the smallest JPEG in `previews.images` that is actually on disk. If none is, it tries any local `*preview*`/`*thumb*` image, then the smallest albedo JPEG.
- **Player image:** the plain `Popup` preview JPEG, the largest one up to 1920 px wide.
- **`metadata`:** `asset_dir`, maps, resolutions, physical size, biome/region, and the `meta` values (tileable, scanArea, …). These can be searched with `meta:`, e.g. `meta:tileable`.
- **Square tiles:** set automatically.

Re-running only re-reads assets whose JSON changed. Pass `--full` to rescan everything. The scan runs on 16 threads by default (`--threads`) because the share is on the network.

## Search

Words match by prefix and the search runs as you type (FTS5 with bm25 ranking; the name counts most, then tags, then categories, then everything else). If the host's `sqlite3` has no FTS5, as in Nuke's bundled Python, a pure-Python index with the same query language and ranking is used instead. `ILP_GALLERY_SEARCH=fts5|python` forces one.

Themes: by default the gallery matches the host (Houdini, Nuke, Maya) or uses the ILP look standalone. `theme="sgi"` / `"sgi-dark"` / `"sgi-slate"` give a modern IRIX look, and `"fusion"` / `"fusion-dark"` / `"fusion-slate"` the same palettes in Qt's Fusion style; see `docs/API.md`, section 8. Accents are ignored, so `cafe` finds `Café`.

| Query | Meaning |
|---|---|
| `walk` | any field starting with "walk" |
| `walk male` | both (AND) |
| `walk OR run`, `walk \| run` | either |
| `"walk cycle"` | exact phrase |
| `-loop` | exclude |
| `tag:hero` `cat:crowd` `name:` `desc:` `path:` `meta:` | restrict to one field |
| `type:movie` / `is:gif` | media type |
| `tag:"hero shot"` | field + phrase |

The search field suggests completions for the word under the cursor (`her` offers `hero`, `tag:hero` and so on). Clicking a category or tag in the sidebar adds it to the query, or removes it if it's already there. On a 20k-item test library, indexing took about 0.3 s and each query about 8 ms.

## The browser window

`ilp_gallery megascans.json` opens the full tool (`GalleryBrowser`):

- **Category tree:** built from `assetCategories` (Surfaces › Rock › Cliff), with counts that follow the search. Search and tree combine: pick *Surfaces › Rock*, then type `moss`.
- **Grid:** name and type under each tile.
- **Details panel:** preview, **Export**, **Copy command**, **Open folder**, clickable category breadcrumbs and tags, the Megascans meta table (Scan Area, Tileable, Texel Density…), available maps and resolutions, and the asset location.
- **Toolbar:** Export selected, Rescan library (rebuilds `megascans.json` incrementally, then reloads) and Settings.
- **Export log dock:** status, Stop export, Clear log.
- **View menu:** toggle Categories / Details / Export log (Ctrl+1/2/3), tile sizes, Reload (F5).

**Export** runs a command template once per asset, through the shell, streaming its output into the log. Set it in Settings or with `ILP_GALLERY_EXPORT_CMD`:

```
stockpile export --asset {asset_dir}
```

Placeholders: `{id} {name} {asset_dir} {json} {path} {type_label} {thumbnail}` (values are shell-quoted). **Copy command** puts the same command(s) on the clipboard.

Settings are stored per user with QSettings (`~/.config/ILPvfx/ILPGallery.conf`): export command, default library (loaded when no file is given on the command line) and Megascans root.

## API (data sources, export callbacks, drag handlers)

See **[docs/API.md](docs/API.md)**. In short:

```python
from ilp_gallery import dcc
from ilp_gallery.sources.megascans import MegascansSource

b = dcc.show([])                                            # browser window (or dcc.make_browser)
b.add_source(MegascansSource(cache="/ice/.../megascans.json.gz"))
b.add_source(MyShotGridPublishes(sg, project_id=123))       # any DataSource subclass
b.register_exporter(ingest, label="Ingest", arg="json", default=True, sources=["megascans"])
b.register_drag_handler(lambda items: [i.path for i in items])
```

Examples: `examples/shotgrid_source.py`, `examples/megascans_ingest.py`.

## Embedding

```python
from ilp_gallery import GalleryWidget

w = GalleryWidget(show_sidebar=True, show_preview=False)
w.load("/jobs/lib/clips")                          # file or folder, call again to add more
w.set_items(records_from_shotgrid)                  # or feed records directly
w.add_action("Import clip", my_import_fn)           # context menu, receives list[GalleryItem]
w.add_action("Apply", apply_fn, default=True)       # default=True: also runs on double-click
w.itemActivated.connect(lambda item: ...)
w.selectionChanged.connect(lambda items: ...)
w.set_query("cat:crowd -tag:loop")
```

- The library (`w.library`) is plain Python. Changing it updates the view on its own: `library.update(item)`, `library.remove(ids)`, `library.save(path)`.
- Tiles can be dragged out: other apps receive file URLs (so they drop into Nuke, Houdini or a file browser) and the full JSON under `application/x-ilp-gallery-items`.
- Keys: `Ctrl+F` search, `Ctrl+C` copy paths, `Ctrl+wheel` tile size. In the player: `Space` play/pause, `←`/`→` step a frame, `Home` jump to the start.

## DCC hosts

Set `ILP_GALLERY_LIBRARY` (one or more sources, `:`-separated) so panels open with content.

```python
# Houdini: shelf tool / Python shell (full browser window)
from ilp_gallery import dcc; dcc.show("/path/to/megascans.json")
#   pane tabs: build them in your tool's own .pypanel with GalleryWidget (see docs/API.md)

# Maya: dockable workspace control
from ilp_gallery import dcc; dcc.show_maya("/jobs/lib/clips")

# Nuke: menu.py
from ilp_gallery import dcc; dcc.register_nuke_panel()
```

Each host gets one example context action (Nuke: Read node, Maya: image plane on the selected camera, Houdini: File node in a COP network). Swap them for your own through `actions=[...]` or `add_action`. For Maya 2024, Nuke 15 and other hosts that still ship PySide2, a small import shim is needed. Everything else is plain Qt.

## Why no QtMultimedia

QtMultimedia backends are missing or unreliable inside some DCCs. Movie frames are therefore piped out of an `ffmpeg` subprocess (raw RGB, decoded on a background thread into a bounded buffer), so playback behaves the same in every host. Thumbnails are cached on disk under `~/.cache/ilp_gallery/thumbs`, keyed by path + mtime + size. Point `ILP_GALLERY_CACHE` at a shared location to share them between artists.

## Tests

```bash
pytest                       # core (no Qt) + offscreen UI tests (need ffmpeg)
```

## Known limits / next steps

- Sidebar counts cover the whole library, not the current results.
- EXR/DPX are decoded by ffmpeg with no colour management. OCIO display transforms would be the next step for plates.
- Image sequences (`%04d`) aren't an item type yet.
- Movies play without audio, on purpose, since these are reference clips.
- The search index is rebuilt in memory on each load. For very large libraries, pass `GalleryLibrary(index_path=...)` and add incremental sync.
