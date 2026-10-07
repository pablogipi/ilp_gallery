"""Data sources: where gallery items come from.

Modelled on Houdini's ``hou.AssetGalleryDataSource``: the gallery asks a
source for its items, polls it for changes, can ask it for thumbnails, and
can write back (tags, metadata) when the source isn't read-only.

Write a source by subclassing :class:`DataSource` and implementing
:meth:`load`. Everything else is optional::

    class PublishedAssets(DataSource):
        name = "shotgrid-assets"
        label = "Published assets"

        def load(self):
            for pub in sg.find("PublishedFile", filters, fields):
                yield {
                    "id": f"pub-{pub['id']}",
                    "name": pub["code"],
                    "path": pub["path"]["local_path"],
                    "categories": [pub["entity"]["name"]],
                    "tags": [t["name"] for t in pub["tags"]],
                    "metadata": {"type_label": pub["published_file_type"]["name"],
                                 "category_paths": [["assets", pub["entity"]["sg_asset_type"]]],
                                 "display": [["Version", pub["version_number"]]]},
                }

        def thumbnail(self, item):          # optional: bytes, a file path, or None
            return download(item.metadata["image_url"])

    gallery.add_source(PublishedAssets())

Records are the same dicts as the JSON library format (see ``GalleryItem``);
:func:`make_record` builds one with explicit arguments.
Reserved ``metadata`` keys the UI understands:

    type_label       subtitle under the tile ("Surface", "Model", ...)
    category_paths   hierarchy for the tree, e.g. [["surface", "rock", "cliff"]]
    display          ordered [label, value] rows for the details panel
    tile_aspect      width/height of tiles (1.0 = square)
"""

from __future__ import annotations

import gzip
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .item import GalleryItem

log = logging.getLogger(__name__)

Record = dict[str, Any]
Progress = Callable[[int, int], None]
LogFn = Callable[[str], None]


@dataclass
class Changes:
    """What :meth:`DataSource.poll` reports. ``reset=True`` means "reload everything"."""
    updated: list[Record | GalleryItem] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    reset: bool = False

    def __bool__(self) -> bool:
        return bool(self.updated or self.removed or self.reset)


def make_record(path: str, id: str | None = None, name: str | None = None, *,
                type: str | None = None, categories: Iterable[str] = (), tags: Iterable[str] = (),
                description: str = "", thumbnail: str | None = None,
                type_label: str | None = None, category_paths: Iterable[Iterable[str]] | None = None,
                display: Iterable[tuple[str, Any]] | dict | None = None, tile_aspect: float | None = None,
                **metadata: Any) -> Record:
    """Build a gallery record (the internal JSON representation) with named arguments.

    ``path``           the media shown/played (image, gif, movie) or any file
    ``thumbnail``      optional smaller image for the tile
    ``type_label``     subtitle under the tile ("Model", "Surface", ...)
    ``category_paths`` tree hierarchy: [["assets", "props", "chairs"]]
    ``display``        details-panel rows: [("Version", 12), ("Artist", "pablo")] or a dict
    ``**metadata``     anything else; searchable with ``meta:`` and available to
                       exporters via ``arg="<key>"``
    """
    meta = dict(metadata)
    if type_label:
        meta["type_label"] = type_label
    if category_paths:
        meta["category_paths"] = [[str(s) for s in p] for p in category_paths]
    if display:
        rows = display.items() if isinstance(display, dict) else display
        meta["display"] = [[str(k), v] for k, v in rows]
    if tile_aspect:
        meta["tile_aspect"] = float(tile_aspect)
    rec: Record = {"path": path, "categories": list(categories), "tags": list(tags),
                   "description": description, "metadata": meta}
    for k, v in (("id", id), ("name", name), ("type", type), ("thumbnail", thumbnail)):
        if v:
            rec[k] = v
    return rec


class DataSource:
    """Base class. Only :meth:`load` is required."""

    #: unique key for this source inside a library (items remember it)
    name: str = ""
    #: human readable name (log, menus)
    label: str = ""
    #: seconds between :meth:`poll` calls from the UI; 0 = never poll
    poll_interval: float = 0.0

    def __init__(self, name: str | None = None, label: str | None = None) -> None:
        if name:
            self.name = name
        if label:
            self.label = label
        if not self.name:
            self.name = type(self).__name__
        if not self.label:
            self.label = self.name

    # -- reading (required) ---------------------------------------------
    def load(self) -> Iterable[Record | GalleryItem]:
        """Return every item. Called on add, on reload and when poll() reports ``reset``.

        May be slow (network, database): the UI calls it on a worker thread,
        so don't touch Qt widgets here.
        """
        raise NotImplementedError

    # -- change notification (optional) ---------------------------------
    def poll(self) -> Changes | None:
        """Report changes since the last call (Houdini's ``updatedItemIds``).

        Called on a worker thread every ``poll_interval`` seconds.
        """
        return None

    # -- thumbnails (optional) ------------------------------------------
    def thumbnail(self, item: GalleryItem) -> bytes | str | None:
        """Image bytes or a file path for items without a local ``thumbnail``.

        Called on a thumbnail worker thread; the result is cached on disk.
        """
        return None

    # -- rebuilding (optional) ------------------------------------------
    def can_rebuild(self) -> bool:
        """True if :meth:`rebuild` does something (the "Rescan library" button)."""
        return type(self).rebuild is not DataSource.rebuild

    def rebuild(self, progress: Progress | None = None, log: LogFn | None = None) -> None:
        """Regenerate whatever the source reads from (e.g. rescan disk into a cache)."""

    # -- writing (optional) ---------------------------------------------
    def is_read_only(self) -> bool:
        return type(self).set_tags is DataSource.set_tags and \
            type(self).set_metadata is DataSource.set_metadata

    def set_tags(self, item: GalleryItem, tags: list[str]) -> None:
        raise NotImplementedError(f"{self.label} is read-only")

    def set_metadata(self, item: GalleryItem, metadata: dict) -> None:
        raise NotImplementedError(f"{self.label} is read-only")

    # -- helpers -----------------------------------------------------------
    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name!r}>"


class RecordsSource(DataSource):
    """Wrap a list, or a function returning records. Handy for quick tools and tests::

        gallery.add_source(RecordsSource(lambda: query_publishes(project), name="publishes"))
    """

    def __init__(self, records: Iterable[Record] | Callable[[], Iterable[Record]],
                 name: str = "records", label: str | None = None) -> None:
        super().__init__(name, label)
        self._records = records

    def load(self):
        return self._records() if callable(self._records) else list(self._records)


# -- JSON files ------------------------------------------------------------
def read_json(path: str) -> Any:
    """Read ``.json`` or gzip-compressed JSON (by magic bytes, any extension)."""
    with open(path, "rb") as fh:
        head = fh.read(2)
        fh.seek(0)
        if head == b"\x1f\x8b":
            with gzip.open(fh, "rt", encoding="utf-8") as gz:
                return json.load(gz)
        return json.loads(fh.read().decode("utf-8"))


def write_json(path: str, payload: Any, compress: bool | None = None) -> None:
    """Atomic write; gzip when ``compress`` or the path ends in ``.gz``."""
    compress = path.endswith(".gz") if compress is None else compress
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    if compress:
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as fh:
            json.dump(payload, fh, separators=(",", ":"), ensure_ascii=False)
    else:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def records_from_json(path: str) -> tuple[list[Record], dict]:
    """(records, header) from one library file. Header = the top-level keys except items."""
    try:
        data = read_json(path)
    except (OSError, ValueError) as exc:
        log.warning("Skipping %s: %s", path, exc)
        return [], {}
    header: dict = {}
    if isinstance(data, dict):
        header = {k: v for k, v in data.items() if k != "items"}
        data = data.get("items", [data] if "path" in data else [])
    if not isinstance(data, list):
        log.warning("Skipping %s: expected a list of records", path)
        return [], header
    base = os.path.dirname(os.path.abspath(path))
    out = []
    for rec in data:
        if not isinstance(rec, dict) or "path" not in rec:
            log.warning("Skipping bad record in %s: %r", path, rec)
            continue
        p = os.path.expandvars(os.path.expanduser(str(rec["path"])))
        if not os.path.isabs(p):  # relative media paths resolve against the JSON's folder
            rec = dict(rec, path=os.path.normpath(os.path.join(base, p)))
        out.append(rec)
    return out, header


class JsonFileSource(DataSource):
    """A library JSON file (``.json`` or ``.json.gz``), or a folder of them.

    Polls the file's modification time, so a rebuilt library shows up
    without restarting the tool.
    """

    poll_interval = 10.0

    def __init__(self, path: str, recursive: bool = False, name: str | None = None,
                 label: str | None = None) -> None:
        self.path = os.path.abspath(os.path.expanduser(path))
        self.recursive = recursive
        self.header: dict = {}
        super().__init__(name or self.path, label or os.path.basename(self.path.rstrip("/")))
        self._stamp = None

    def files(self) -> list[str]:
        if not os.path.isdir(self.path):
            return [self.path]
        out = []
        for root, dirs, files in os.walk(self.path):
            dirs.sort()
            out += [os.path.join(root, f) for f in sorted(files) if f.endswith((".json", ".json.gz"))]
            if not self.recursive:
                break
        return out

    def _stamp_now(self):
        stamps = []
        for f in self.files():
            try:
                st = os.stat(f)
                stamps.append((f, st.st_mtime_ns, st.st_size))
            except OSError:
                pass
        return tuple(stamps)

    def load(self):
        self._stamp = self._stamp_now()
        records: list[Record] = []
        for f in self.files():
            recs, header = records_from_json(f)
            if header and not self.header:
                self.header = header
            records += recs
        return records

    def poll(self) -> Changes | None:
        now = self._stamp_now()
        if self._stamp is not None and now != self._stamp:
            return Changes(reset=True)
        return None


class FolderSource(DataSource):
    """Every image / movie under a folder; sub-folder names become categories."""

    def __init__(self, folder: str, recursive: bool = True, name: str | None = None) -> None:
        self.folder = os.path.abspath(os.path.expanduser(folder))
        self.recursive = recursive
        super().__init__(name or self.folder, os.path.basename(self.folder.rstrip("/")))

    def load(self):
        from .library import scan_folder
        return scan_folder(self.folder, recursive=self.recursive)


def source_for_path(path: str, recursive: bool = False) -> DataSource:
    """Pick the right source for a path: Megascans library, other JSON, or a media folder."""
    p = os.path.abspath(os.path.expanduser(path))
    if os.path.isfile(p):
        try:
            head = _peek_header(p)
        except OSError:
            head = {}
        if head.get("source") == "megascans":
            from ..sources.megascans import MegascansSource
            return MegascansSource(cache=p, root=head.get("root"))
        return JsonFileSource(p, recursive=recursive)
    if os.path.isdir(p) and not any(f.endswith((".json", ".json.gz")) for f in os.listdir(p)):
        return FolderSource(p, recursive=True)
    return JsonFileSource(p, recursive=recursive)


def _peek_header(path: str) -> dict:
    """Top-level keys before "items" without parsing the whole file."""
    with open(path, "rb") as fh:
        raw = fh.read(4096)
    if raw[:2] == b"\x1f\x8b":
        with gzip.open(path, "rb") as fh:
            raw = fh.read(4096)
    text = raw.decode("utf-8", "replace")
    cut = text.find('"items"')
    if cut < 0:
        return {}
    try:
        return json.loads(text[:cut].rstrip().rstrip(",") + "}")
    except ValueError:
        return {}


def to_items(records: Iterable[Record | GalleryItem], source: str) -> list[GalleryItem]:
    """Convert records, stamping each with its source name. Bad records are skipped."""
    items = []
    for rec in records:
        try:
            it = rec if isinstance(rec, GalleryItem) else GalleryItem.from_dict(rec)
        except (ValueError, TypeError) as exc:
            log.warning("Skipping record from %s: %s", source, exc)
            continue
        it.source = source
        items.append(it)
    return items
