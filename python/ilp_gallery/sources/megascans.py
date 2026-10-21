"""Build gallery records from a Quixel Megascans (Bridge) download folder.

Layout::

    <root>/                       default: $ILP_MEGASCANS_ROOT or /ice/resources/ASSET_LIB/megascans/Downloaded
        3d/ 3dplant/ atlas/ surface/        -> main category
            <asset folder>/
                <id>.json                   -> asset description (file name == id)
                *_thumb.jpg, *_preview.jpg, textures, meshes ...

Per asset:

* ``id``, ``name`` and ``tags`` come from the JSON.
* ``categories`` = top folder first, then the JSON ``categories``.
* ``tags`` also get the useful ``semanticTags`` and ``assetCategories`` words.
* ``thumbnail`` = the smallest JPEG preview **that exists on disk**. Falls back
  to any local preview/thumb image, then to the smallest albedo JPEG.
* ``path`` = a mid-size preview for the player pane (falls back to the thumbnail).
* ``metadata`` keeps asset_dir, megascans type, maps, physical size, ``meta``
  values (tileable, scanArea, ...), biome / region, average colour.

Scanning is threaded (network shares are latency bound) and incremental:
records whose JSON hasn't changed are reused from the previous output.
"""

from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable, Iterator

log = logging.getLogger(__name__)

DEFAULT_ROOT = os.environ.get("ILP_MEGASCANS_ROOT", "/ice/resources/ASSET_LIB/megascans/Downloaded")
MAIN_CATEGORIES = ("3d", "3dplant", "atlas", "surface")
JPEG_EXTS = (".jpg", ".jpeg")
IMAGE_EXTS = (".jpg", ".jpeg", ".png")
EMPTY_VALUES = {"", "undefined", "null", "none", "n/a"}
PLAYER_MAX_WIDTH = 1920  # biggest preview we'd load into the player pane
IMPORTER_VERSION = 6     # bump when records change shape -> incremental scans rebuild
                         # 6: "collections" (semanticTags.collection, the pack) and
                         #    "states" / "environments" / "colors" (Bridge's filters)
                         # 5: texture maps found with Bridge's newer JSON ("maps") and by file name
                         # 4: "images" (Clay and Tiled renders, texture maps) for the preview pane

# Display names: folder / assetCategories top level -> (tree label, tile subtitle)
TYPE_LABELS = {
    "3d": ("3D Assets", "3D Asset"), "3d asset": ("3D Assets", "3D Asset"),
    "3dplant": ("3D Plants", "3D Plant"), "3d plant": ("3D Plants", "3D Plant"),
    "plant": ("3D Plants", "3D Plant"),
    "surface": ("Surfaces", "Surface"), "decal": ("Decals", "Decal"),
    "atlas": ("Atlases", "Atlas"), "imperfection": ("Imperfections", "Imperfection"),
    "displacement": ("Displacements", "Displacement"), "brush": ("Brushes", "Brush"),
}

# semanticTags keys worth searching on (the rest is noise or duplicated)
_SEMANTIC_KEYS = ("contains", "descriptive", "environment", "state", "theme",
                  "subject_matter", "color", "interior_exterior", "orientation")


# -- helpers ---------------------------------------------------------------
def _area(resolution: str | None) -> int:
    try:
        w, h = str(resolution).lower().split("x")
        return int(float(w)) * int(float(h))
    except (ValueError, AttributeError):
        return 0


def _width(resolution: str | None) -> int:
    try:
        return int(float(str(resolution).lower().split("x")[0]))
    except (ValueError, IndexError):
        return 0


def _words(value: Any) -> list[str]:
    """Flatten str / list / nested dict into clean words."""
    out: list[str] = []
    if isinstance(value, str):
        out += [v.strip() for v in value.split(",")]
    elif isinstance(value, (list, tuple)):
        for v in value:
            out += _words(v)
    elif isinstance(value, dict):
        for k, v in value.items():
            out.append(str(k).strip())
            out += _words(v)
    return [w for w in out if w]


def _dedupe(words: Iterable[str]) -> list[str]:
    seen, out = set(), []
    for w in words:
        k = w.lower()
        if k not in seen:
            seen.add(k)
            out.append(w)
    return out


def pick_previews(data: dict, files: set[str]) -> tuple[str | None, str | None]:
    """Return (thumbnail, player_preview) file names that exist in ``files``."""
    images = [i for i in (data.get("previews") or {}).get("images") or []
              if isinstance(i, dict) and isinstance(i.get("uri"), str) and len(i["uri"]) < 260]
    local = [i for i in images if os.path.basename(i["uri"]) in files]

    def key(i):  # smallest first: pixels, then bytes
        return (_area(i.get("resolution")), i.get("contentLength") or 0)

    jpgs = sorted((i for i in local if i["uri"].lower().endswith(JPEG_EXTS)), key=key)
    thumb = os.path.basename(jpgs[0]["uri"]) if jpgs else None
    if thumb is None and local:  # no JPEG on disk, take the smallest listed image
        thumb = os.path.basename(sorted(local, key=key)[0]["uri"])

    # Player: the plain "preview" (not clay/tiled), largest up to PLAYER_MAX_WIDTH.
    def plain_preview(i):
        tags = set(i.get("tags") or [])
        return "preview" in tags and not tags & {"clay", "tiled"}
    cands = [i for i in jpgs if plain_preview(i) and _width(i.get("resolution")) <= PLAYER_MAX_WIDTH] \
        or [i for i in jpgs if _width(i.get("resolution")) <= PLAYER_MAX_WIDTH]
    player = os.path.basename(max(cands, key=key)["uri"]) if cands else None
    return thumb, player


RENDERS = (("clay", "Clay"), ("tiled", "Tiled"))      # Bridge's other renders, after the plain one


def pick_renders(data: dict, files: set[str]) -> list[tuple[str, str]]:
    """[(label, file name), ...] of the extra renders on disk (Clay, Tiled): the
    largest JPEG of each up to PLAYER_MAX_WIDTH, PNG if there's no JPEG."""
    images = [i for i in (data.get("previews") or {}).get("images") or []
              if isinstance(i, dict) and isinstance(i.get("uri"), str) and len(i["uri"]) < 260
              and os.path.basename(i["uri"]) in files]
    out = []
    for tag, label in RENDERS:
        mine = [i for i in images if tag in (i.get("tags") or [])
                and _width(i.get("resolution")) <= PLAYER_MAX_WIDTH]
        if not mine:
            continue
        jpg = [i for i in mine if i["uri"].lower().endswith(JPEG_EXTS)]
        best = max(jpg or mine, key=lambda i: (_area(i.get("resolution")), i.get("contentLength") or 0))
        out.append((label, os.path.basename(best["uri"])))
    return out


MAP_LABELS = {"ao": "AO", "normal": "Normal", "normalbump": "Normal Bump", "albedo": "Albedo"}
MAP_ORDER = ("albedo", "normal", "roughness", "displacement", "ao", "cavity", "bump", "gloss", "specular",
             "metalness", "opacity", "translucency")
MAP_PREVIEW_MAX = 2048     # map images for the preview: the largest JPEG up to this width


# Bridge names texture files <id>_<res>_<Map>[_<extra>].<ext>: slhnqsg_8K_Albedo.jpg
_MAP_FILE = re.compile(r"^[A-Za-z0-9]+_(\d+)K_([A-Za-z][A-Za-z0-9]*)(?:_[A-Za-z0-9]+)*\.([A-Za-z]+)$")
MAP_EXTS = IMAGE_EXTS + (".exr", ".tif", ".tiff")


def _uri_dicts(node):
    """Every dict with a "uri" under ``node``, however the JSON nests them."""
    if isinstance(node, dict):
        if isinstance(node.get("uri"), str):
            yield node
        for v in node.values():
            yield from _uri_dicts(v)
    elif isinstance(node, list):
        for v in node:
            yield from _uri_dicts(v)


def _res_from_k(k: str) -> str:
    n = int(k) * 1024
    return f"{n}x{n}"


def local_maps(data: dict, files: set[str]) -> list[tuple[str, str, str]]:
    """[(map type, file name, resolution), ...] for the texture files on disk. Works
    with both JSON layouts Bridge writes (old "components", newer "maps") and falls
    back on the file names themselves, so a JSON that lists nothing still works."""
    found: dict[str, tuple[str, str, str]] = {}

    def add(kind, uri, res):
        name = os.path.basename(str(uri or ""))
        if name in files and name.lower().endswith(MAP_EXTS) and kind:
            found.setdefault(name, (str(kind), name, str(res or "")))

    for comp in data.get("components") or []:                  # old layout
        for u in comp.get("uris") or []:
            for r in u.get("resolutions") or []:
                for fmt in r.get("formats") or []:
                    add(comp.get("type"), fmt.get("uri"), r.get("resolution"))
    for d in _uri_dicts(data.get("maps")):                      # newer layout
        name = os.path.basename(d["uri"])
        m = _MAP_FILE.match(name)
        add(d.get("type") or (m.group(2) if m else ""), name,
            d.get("resolution") or (_res_from_k(m.group(1)) if m else ""))
    for name in files:                                          # whatever the JSON says
        m = _MAP_FILE.match(name)
        if m and name not in found and not name.lower().startswith(("preview", "thumb")):
            add(m.group(2), name, _res_from_k(m.group(1)))
    return list(found.values())


def pick_maps(data: dict, files: set[str]) -> list[tuple[str, str]]:
    """[(label, file name), ...] of the texture maps the preview can show: one
    JPEG / PNG per map type (EXR and TIFF are skipped: Qt can't show them), the
    largest up to MAP_PREVIEW_MAX wide, else the smallest there is (the player
    decodes big JPEGs at reduced size)."""
    by_kind: dict[str, list] = {}
    for kind, name, res in local_maps(data, files):
        if name.lower().endswith(IMAGE_EXTS):
            by_kind.setdefault(kind.lower(), []).append(
                (_area(res), _width(res), not name.lower().endswith(JPEG_EXTS), name, kind))
    out = []
    for key, cands in by_kind.items():
        fit = [c for c in cands if c[1] <= MAP_PREVIEW_MAX]
        best = max(fit, key=lambda c: (c[0], not c[2])) if fit else min(cands)
        out.append((MAP_LABELS.get(key, best[4][:1].upper() + best[4][1:]), best[3]))
    order = {k: i for i, k in enumerate(MAP_ORDER)}
    return sorted(out, key=lambda lf: order.get(lf[0].lower(), len(order)))


PACKS = "Packs"      # the collections branch's level for Quixel's packs


def asset_collections(data: dict) -> list[list[str]]:
    """The asset's collections for the gallery's "Collections" branch: Quixel's named
    collections (``semanticTags.collection``: "Iceland", "Wasteland"...) at the top,
    and its pack (``pack.name``: "Neoclassical Modular Building Vol. 4") under
    "Packs", unless a collection already has the pack's name."""
    sem = data.get("semanticTags") or {}
    names = _dedupe(_words(sem.get("collection")))
    out = [[n] for n in names]
    pack = data.get("pack")
    pack_name = (pack.get("name") or pack.get("_id")) if isinstance(pack, dict) else pack
    pack_name = str(pack_name or "").strip()
    if pack_name and pack_name.lower() not in {n.lower() for n in names}:
        out.append([PACKS, pack_name])
    return out


def _size_label(value) -> str:
    text = str(value or "").strip().replace(" m", "")
    return f"{text.replace('x', ' × ')} m" if text else ""


def list_extras(item, shared: bool) -> list[str]:
    """For ``GalleryWidget(list_extras=...)``: a Megascans asset's physical size,
    and its ID when other assets have the same name (Quixel names scans by
    material: dozens of "Road", "Old Road"...)."""
    m = item.metadata
    if m.get("source") != "megascans":
        return []
    out = []
    size = _size_label(m.get("physical_size") or m.get("scanArea"))
    if size:
        out.append(size)
    if shared:
        out.append(item.id)
    return out


def _fallback_preview(files: set[str], asset_dir: str) -> str | None:
    """No listed preview on disk: any local *preview* / *thumb* image, smallest file."""
    cands = [f for f in files if f.lower().endswith(IMAGE_EXTS)
             and any(k in f.lower() for k in ("preview", "thumb", "grid", "popup"))]
    if not cands:
        return None
    def size(f):
        try:
            return os.path.getsize(os.path.join(asset_dir, f))
        except OSError:
            return 1 << 62
    jpg = [f for f in cands if f.lower().endswith(JPEG_EXTS)]
    return min(jpg or cands, key=size)


def _albedo_fallback(data: dict, files: set[str]) -> str | None:
    best = None
    for comp in data.get("components") or []:
        if comp.get("type") not in ("albedo", "diffuse", "opacity"):
            continue
        for u in comp.get("uris") or []:
            for res in u.get("resolutions") or []:
                for fmt in res.get("formats") or []:
                    uri = os.path.basename(fmt.get("uri") or "")
                    if uri in files and uri.lower().endswith(JPEG_EXTS):
                        cand = (_area(res.get("resolution")), uri)
                        best = min(best, cand) if best else cand
        if best:
            break
    return best[1] if best else None


def _find_json(asset_dir: str, files: set[str]) -> str | None:
    jsons = [f for f in files if f.lower().endswith(".json")]
    if not jsons:
        return None
    if len(jsons) == 1:
        return jsons[0]
    folder = os.path.basename(asset_dir).lower()
    named = [j for j in jsons if os.path.splitext(j)[0].lower() in folder]
    if named:
        return named[0]
    return max(jsons, key=lambda j: os.path.getsize(os.path.join(asset_dir, j)))


# -- record building -------------------------------------------------------
def asset_record(asset_dir: str, main_category: str) -> dict | None:
    """Gallery record for one asset folder, or None if it isn't one."""
    try:
        files = {e.name for e in os.scandir(asset_dir) if e.is_file()}
    except OSError as exc:
        log.warning("Can't list %s: %s", asset_dir, exc)
        return None
    jname = _find_json(asset_dir, files)
    if jname is None:
        return None
    jpath = os.path.join(asset_dir, jname)
    try:
        with open(jpath, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Skipping %s: %s", jpath, exc)
        return None
    if not isinstance(data, dict):
        return None

    thumb, player = pick_previews(data, files)
    thumb = thumb or _fallback_preview(files, asset_dir) or _albedo_fallback(data, files)
    if thumb is None:
        log.info("No preview image for %s", asset_dir)
    player = player or thumb

    asset_id = str(data.get("id") or os.path.splitext(jname)[0])
    sem = data.get("semanticTags") or {}
    tags = _dedupe(
        _words(data.get("tags"))
        + [w for k in _SEMANTIC_KEYS for w in _words(sem.get(k))]
        + _words(data.get("assetCategories"))
    )
    paths = category_paths(data.get("assetCategories"), main_category)
    top = paths[0][0].lower() if paths else main_category
    type_label = TYPE_LABELS.get(top, TYPE_LABELS.get(main_category, (main_category, main_category.title())))[1]
    categories = _dedupe([main_category] + _words(data.get("categories")) + [p for path in paths for p in path])
    # Tags repeating the top-level categories are noise; category-path words stay searchable as tags.
    drop = {c.lower() for c in [main_category] + _words(data.get("categories"))} | {"other"}
    tags = [t for t in tags if t.lower() not in drop]

    meta_list = [m for m in data.get("meta") or [] if isinstance(m, dict) and m.get("key")]
    meta_values = {m["key"]: m.get("value") for m in meta_list}
    def has_value(v):
        return v is not None and v != [] and str(v).strip().lower() not in EMPTY_VALUES

    display = clean_display([[m.get("name") or m["key"], m.get("value")] for m in meta_list
                             if has_value(m.get("value"))])
    env = {k: v for k, v in (data.get("environment") or {}).items() if has_value(v)}
    on_disk = local_maps(data, files)
    order = {k: i for i, k in enumerate(MAP_ORDER)}
    maps = sorted(_dedupe([c.get("type") for c in data.get("components") or [] if c.get("type")]
                          + [kind.lower() for kind, _n, _r in on_disk]),
                  key=lambda k: order.get(str(k).lower(), len(order)))
    resolutions = sorted({r.get("resolution") for c in data.get("components") or []
                          for u in c.get("uris") or [] for r in u.get("resolutions") or []
                          if r.get("resolution")} | {res for _k, _n, res in on_disk if res},
                         key=_area, reverse=True)
    # Biome and region: searchable tags, not rows of their own in the details pane
    tags = _dedupe(tags + [str(v) for v in (env.get("biome"), env.get("region")) if v])
    description = ""

    return {
        "id": asset_id,
        "name": data.get("name") or asset_id,
        "path": os.path.join(asset_dir, player) if player else jpath,
        "type": "image",
        "thumbnail": os.path.join(asset_dir, thumb) if thumb else None,
        "categories": categories,
        "tags": tags,
        "description": description,
        "metadata": {
            "source": "megascans",
            "importer_version": IMPORTER_VERSION,
            "tile_aspect": 1.0,  # Megascans grid thumbs are square
            "type_label": type_label,
            "category_paths": paths,
            "display": display,
            "megascans_type": main_category,
            "asset_dir": asset_dir,
            "json": jpath,
            "json_mtime": os.path.getmtime(jpath),
            "maps": maps,
            "resolutions": resolutions,
            "physical_size": data.get("physicalSize"),
            "collections": asset_collections(data),
            # Bridge's filters (States, Environments, Colors), kept apart for filter menus
            **{key: _dedupe([w.title() if w.islower() else w for w in _words(sem.get(src))])
               for key, src in (("states", "state"), ("environments", "environment"), ("colors", "color"))},
            # the preview pane: the plain render (the item's path), its Clay and Tiled
            # renders, and the texture maps, as Bridge's Renders / Maps buttons
            "images": ([{"path": os.path.join(asset_dir, f), "label": label, "group": "Renders"}
                        for label, f in pick_renders(data, files)] if player else [])
            + [{"path": os.path.join(asset_dir, f), "label": label, "group": "Maps"}
               for label, f in pick_maps(data, files)],
            "image_label": "Render" if player else None,
            "image_group": "Renders",
            "average_color": data.get("averageColor") if has_value(data.get("averageColor")) else None,
            "biome": env.get("biome"),
            "region": env.get("region"),
            **{k: v for k, v in meta_values.items() if has_value(v)},
        },
    }


def category_paths(asset_categories: Any, main_category: str) -> list[list[str]]:
    """{"surface": {"rock": {"cliff": {}, "rough": {}}}} -> [[surface, rock, cliff], [surface, rock, rough]]"""
    paths: list[list[str]] = []

    def walk(node, prefix):
        if isinstance(node, dict) and node:
            for k, v in node.items():
                k = str(k).strip()
                if k:
                    walk(v, prefix + [k])
        elif prefix:
            paths.append(prefix)

    walk(asset_categories, [])
    # drop the uninformative "other" level Megascans uses a lot
    paths = [[p for i, p in enumerate(path) if not (i and p.lower() == "other")] for path in paths]
    return paths or [[main_category]]


# meta entries that mean nothing to an artist picking an asset: store points, engine
# flags, per-LOD and per-variation tables
HIDDEN_DISPLAY = {"points", "usebillboardmaterial", "loddistance", "loddistances", "dimensions",
                  "biome", "region"}


def _structured(value: Any) -> bool:
    """A dict, or a list holding dicts / lists: raw data, not something to read."""
    if isinstance(value, dict):
        return True
    if isinstance(value, (list, tuple)):
        return any(isinstance(v, (dict, list, tuple)) for v in value)
    text = str(value).strip()
    return text.startswith(("{", "[{", "[[")) or "{'" in text


def clean_display(rows) -> list[list[str]]:
    """The details pane's rows for an asset: hidden keys and raw structured values
    dropped, the rest pretty-printed. Also run on libraries built by older imports."""
    out = []
    for row in rows or []:
        try:
            name, value = row
        except (TypeError, ValueError):
            continue
        key = str(name).replace(" ", "").replace("_", "").lower()
        if key in HIDDEN_DISPLAY or _structured(value):
            continue
        out.append([str(name), _pretty(value)])
    return out


def _pretty(value: Any) -> str:
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    if isinstance(value, (list, tuple)):
        return ", ".join(map(str, value))
    return str(value)


def iter_asset_dirs(root: str, categories: Iterable[str] = MAIN_CATEGORIES) -> Iterator[tuple[str, str]]:
    for cat in categories:
        cdir = os.path.join(root, cat)
        try:
            entries = sorted((e for e in os.scandir(cdir) if e.is_dir()), key=lambda e: e.name)
        except OSError:
            log.warning("Missing category folder %s", cdir)
            continue
        for e in entries:
            yield e.path, cat


def scan(root: str = DEFAULT_ROOT, previous: list[dict] | None = None, threads: int = 16,
         categories: Iterable[str] = MAIN_CATEGORIES, progress=None) -> list[dict]:
    """Scan ``root``; reuse records from ``previous`` whose JSON mtime is unchanged."""
    reuse: dict[str, dict] = {}
    for rec in previous or []:
        m = rec.get("metadata") or {}
        if m.get("source") == "megascans" and m.get("asset_dir"):
            reuse[m["asset_dir"]] = rec

    dirs = list(iter_asset_dirs(root, categories))

    def work(args):
        asset_dir, cat = args
        old = reuse.get(asset_dir)
        if old:
            m = old["metadata"]
            try:
                if m.get("importer_version") == IMPORTER_VERSION and \
                        os.path.getmtime(m["json"]) == m.get("json_mtime"):
                    return old
            except OSError:
                pass
        return asset_record(asset_dir, cat)

    records: list[dict] = []
    with ThreadPoolExecutor(max(1, threads)) as pool:
        for n, rec in enumerate(pool.map(work, dirs), 1):
            if rec:
                records.append(rec)
            if progress and (n % 200 == 0 or n == len(dirs)):
                progress(n, len(dirs))
    return records


def build_library_json(output: str, root: str = DEFAULT_ROOT, full: bool = False,
                       threads: int = 16, progress=None) -> tuple[int, int]:
    """Scan and write ``output`` atomically (gzip if it ends in .gz). Returns (items, reused)."""
    from ..core.source import read_json, write_json

    previous: list[dict] = []
    if not full and os.path.exists(output):
        try:
            previous = read_json(output).get("items", [])
        except (OSError, ValueError, AttributeError):
            previous = []
    before = {id(r) for r in previous}
    records = scan(root, previous, threads=threads, progress=progress)
    reused = sum(1 for r in records if id(r) in before)
    write_json(output, {"source": "megascans", "root": root, "importer_version": IMPORTER_VERSION,
                        "items": records})
    return len(records), reused


# -- data source -------------------------------------------------------------
from ..core.source import JsonFileSource  # noqa: E402  (core never imports this module)


class MegascansSource(JsonFileSource):
    """The Megascans download library as a gallery source.

    Reads the pre-built cache (``megascans.json`` / ``.json.gz``) that everybody
    shares; :meth:`rebuild` rescans ``root`` into it (incrementally)::

        src = MegascansSource(cache="/ice/.../megascans.json.gz")
        gallery.add_source(src)
    """

    def __init__(self, cache: str, root: str | None = None, name: str = "megascans",
                 label: str = "Megascans") -> None:
        super().__init__(cache, name=name, label=label)
        self.root = root or DEFAULT_ROOT

    def load(self):
        if not os.path.exists(self.path):
            log.info("No Megascans cache at %s yet: building it from %s", self.path, self.root)
            self.rebuild()
        records = super().load()
        self.root = self.header.get("root") or self.root
        for rec in records:                          # caches built before clean_display existed
            m = rec.get("metadata") or {}
            if "display" in m:
                m["display"] = clean_display(m["display"])
            env = [str(m[k]) for k in ("biome", "region") if m.get(k)]
            if env and rec.get("description") == ", ".join(env):
                rec["description"] = ""
                rec["tags"] = _dedupe(list(rec.get("tags") or []) + env)
        return records

    def rebuild(self, progress=None, log=None) -> None:
        import time
        t = time.perf_counter()
        n, reused = build_library_json(self.path, root=self.root, progress=progress)
        if log:
            log(f"Megascans: {n:,} assets ({n - reused:,} new or changed) from {self.root} "
                f"in {time.perf_counter() - t:.1f} s")

    @property
    def rebuild_hint(self) -> str:
        """How to bring an outdated cache up to date (shown in the gallery's log)."""
        return f"ilp_gallery megascans -o {self.path}"

    def outdated(self, items) -> int:
        """How many items were written by an older importer version."""
        return sum(1 for it in items if it.metadata.get("importer_version") != IMPORTER_VERSION)


# -- helpers for export / drag callbacks -------------------------------------
def json_path(item) -> str:
    """The asset's original Megascans JSON (what an ingest tool usually wants)."""
    return item.metadata["json"]


def asset_data(item) -> dict:
    """The original Megascans JSON, parsed."""
    with open(json_path(item), "r", encoding="utf-8") as fh:
        return json.load(fh)


def texture_files(item, maps: Iterable[str] | None = None, resolution: str | None = None,
                  prefer: Iterable[str] = ("exr", "jpg", "jpeg", "png", "tif")) -> dict[str, str]:
    """Texture files on disk per map type: ``{"albedo": "/.../x_4K_Albedo.exr", ...}``.

    ``resolution`` like "4K"/"4096x4096" (default: the highest available on disk),
    ``prefer`` orders file formats. Only files that exist are returned.
    """
    data = asset_data(item)
    asset_dir = item.metadata["asset_dir"]
    try:
        files = set(os.listdir(asset_dir))
    except OSError:
        return {}
    want = {m.lower() for m in maps} if maps else None
    res_px = None
    if resolution:
        r = str(resolution).upper().rstrip("K")
        res_px = int(r) * 1024 if str(resolution).upper().endswith("K") and r.isdigit() else _width(resolution)
    rank = {ext: i for i, ext in enumerate(prefer)}
    out: dict[str, str] = {}
    for comp in data.get("components") or []:
        mtype = (comp.get("type") or "").lower()
        if want and mtype not in want:
            continue
        best = None
        for u in comp.get("uris") or []:
            for res in u.get("resolutions") or []:
                w = _width(res.get("resolution"))
                if res_px and w != res_px:
                    continue
                for fmt in res.get("formats") or []:
                    f = os.path.basename(fmt.get("uri") or "")
                    ext = f.rsplit(".", 1)[-1].lower()
                    if f in files and ext in rank:
                        key = (-w, rank[ext])
                        if best is None or key < best[0]:
                            best = (key, f)
        if best:
            out[mtype] = os.path.join(asset_dir, best[1])
    return out
