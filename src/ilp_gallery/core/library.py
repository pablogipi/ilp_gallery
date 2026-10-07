"""GalleryLibrary: the middle layer holding every item of a gallery.

Items come from :class:`~ilp_gallery.core.source.DataSource` objects
(JSON files, Megascans, a folder, ShotGrid, ... see ``core.source``).
Each item remembers its source, so reloading or polling one source only
touches its own items.

The library is Qt-free. The UI subscribes with :meth:`add_listener`.
Mutations are guarded by a lock, so a worker thread can prepare items
(:meth:`prepare`) while the GUI thread keeps searching.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Callable, Iterable, Iterator

from .index import SearchIndex
from .item import MEDIA_TYPES, GalleryItem, guess_media_type
from .source import Changes, DataSource, RecordsSource, source_for_path, to_items, write_json

log = logging.getLogger(__name__)

Listener = Callable[[], None]


class GalleryLibrary:
    def __init__(self, index_path: str = ":memory:") -> None:
        self._items: dict[str, GalleryItem] = {}
        self._order: list[str] = []
        self._sources: dict[str, DataSource] = {}
        self._listeners: list[Listener] = []
        self._lock = threading.RLock()
        self.index = SearchIndex(index_path)

    # -- container protocol --------------------------------------------
    def __len__(self) -> int:
        return len(self._order)

    def __iter__(self) -> Iterator[GalleryItem]:
        with self._lock:
            order = list(self._order)
        return (self._items[i] for i in order if i in self._items)

    def __contains__(self, item_id: str) -> bool:
        return item_id in self._items

    def get(self, item_id: str) -> GalleryItem | None:
        return self._items.get(item_id)

    def ids(self) -> list[str]:
        with self._lock:
            return list(self._order)

    # -- sources -------------------------------------------------------
    @property
    def sources(self) -> list[DataSource]:
        return list(self._sources.values())

    def source(self, name: str) -> DataSource | None:
        return self._sources.get(name)

    def source_of(self, item: GalleryItem | str) -> DataSource | None:
        it = self.get(item) if isinstance(item, str) else item
        return self._sources.get(it.source) if it is not None else None

    def add_source(self, source: DataSource) -> int:
        """Register ``source`` and load it now (blocking). Returns item count.

        The UI's ``GalleryWidget.add_source`` does the same off the GUI thread.
        """
        self.register_source(source)
        return self.apply(source, self.prepare(source, source.load()), reset=True)

    def register_source(self, source: DataSource) -> None:
        with self._lock:
            if source.name in self._sources and self._sources[source.name] is not source:
                raise ValueError(f"A different source is already registered as {source.name!r}")
            self._sources[source.name] = source

    def remove_source(self, name: str) -> int:
        with self._lock:
            src = self._sources.pop(name, None)
            ids = [i for i in self._order if self._items[i].source == name]
        return self.remove(ids) if src else 0

    def prepare(self, source: DataSource, records: Iterable) -> list[GalleryItem]:
        """Convert records and index them. Thread-safe; heavy part of loading."""
        items = to_items(records, source.name)
        self.index.upsert(items)
        return items

    def apply(self, source: DataSource, items: list[GalleryItem], reset: bool = False,
              removed: Iterable[str] = ()) -> int:
        """Make prepared items visible. ``reset`` drops the source's items not in ``items``."""
        with self._lock:
            gone = set(removed)
            if reset:
                keep = {it.id for it in items}
                gone |= {i for i in self._order if self._items[i].source == source.name and i not in keep}
            for it in items:
                if it.id not in self._items:
                    self._order.append(it.id)
                self._items[it.id] = it  # same id -> later wins
            gone = {i for i in gone if i in self._items and self._items[i].source == source.name}
            for i in gone:
                del self._items[i]
            if gone:
                self._order = [i for i in self._order if i not in gone]
        if gone:
            self.index.remove(gone)
        self._changed()
        return len(items)

    def reload_source(self, source: DataSource | str) -> int:
        src = self._sources[source] if isinstance(source, str) else source
        return self.apply(src, self.prepare(src, src.load()), reset=True)

    def apply_changes(self, source: DataSource, changes: Changes | None) -> bool:
        """Apply what ``source.poll()`` returned. True if anything changed."""
        if not changes:
            return False
        if changes.reset:
            self.reload_source(source)
            return True
        self.apply(source, self.prepare(source, changes.updated), removed=changes.removed)
        return True

    def reload(self) -> int:
        """Reload every source."""
        n = 0
        for src in self.sources:
            n += self.reload_source(src)
        if not self._sources:
            self._changed()
        return n

    # -- convenience loaders (wrap a source) ------------------------------
    def load(self, path: str, recursive: bool = False, replace: bool = False) -> int:
        """Load a library JSON (``.json`` / ``.json.gz``), a folder of them, or a media folder."""
        if replace:
            self.clear()
        src = source_for_path(path, recursive=recursive)
        if src.name in self._sources:
            return self.reload_source(self._sources[src.name])
        return self.add_source(src)

    def add_records(self, records: Iterable[dict], source: str = "records") -> int:
        """Add in-memory records (added to a simple :class:`RecordsSource`)."""
        src = self._sources.get(source)
        if src is None:
            src = RecordsSource([], name=source)
            self.register_source(src)
        return self.apply(src, self.prepare(src, records))

    def add_items(self, items: Iterable[GalleryItem], source: str = "records") -> int:
        return self.add_records(list(items), source=source)

    def remove(self, item_ids: Iterable[str]) -> int:
        with self._lock:
            ids = {i for i in item_ids if i in self._items}
            for i in ids:
                del self._items[i]
            if ids:
                self._order = [i for i in self._order if i not in ids]
        if ids:
            self.index.remove(ids)
            self._changed()
        return len(ids)

    def update(self, item: GalleryItem) -> None:
        """Replace an item in memory (e.g. after editing its tags)."""
        with self._lock:
            if item.id not in self._items:
                self._order.append(item.id)
            self._items[item.id] = item
        self.index.upsert([item])
        self._changed()

    def set_tags(self, item: GalleryItem, tags: list[str]) -> None:
        """Write tags through the item's source (raises if it's read-only), then update."""
        src = self.source_of(item)
        if src is None or src.is_read_only():
            raise PermissionError(f"{item.name}: its source is read-only")
        src.set_tags(item, list(tags))
        item.tags = list(dict.fromkeys(t.strip() for t in tags if t.strip()))
        self.update(item)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._order.clear()
            self._sources.clear()
        self.index.clear()
        self._changed()

    # -- change notification -------------------------------------------
    def add_listener(self, fn: Listener) -> None:
        self._listeners.append(fn)

    def remove_listener(self, fn: Listener) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    def _changed(self) -> None:
        for fn in list(self._listeners):
            try:
                fn()
            except Exception:  # a broken listener must not break the library
                log.exception("Gallery listener failed")

    # -- saving --------------------------------------------------------
    def save(self, path: str, item_ids: Iterable[str] | None = None) -> None:
        """Write items as a library file (gzip-compressed if the path ends in .gz)."""
        ids = list(item_ids) if item_ids is not None else self.ids()
        write_json(path, {"items": [self._items[i].to_dict() for i in ids if i in self._items]})

    # -- search --------------------------------------------------------
    def search(self, query: str, limit: int | None = None) -> list[str]:
        """Return matching ids, best match first. Empty query -> all, in load order."""
        if not query.strip():
            return self.ids()[:limit] if limit else self.ids()
        return self.index.search(query, limit=limit)

    # -- facets for the UI (sidebar, completer) --------------------------
    def categories(self) -> dict[str, int]:
        return self._facet("categories")

    def tags(self) -> dict[str, int]:
        return self._facet("tags")

    def _facet(self, attr: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for it in self:
            for v in getattr(it, attr):
                counts[v] = counts.get(v, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: kv[0].lower()))


def scan_folder(folder: str, recursive: bool = True) -> list[dict]:
    """Build records for every media file in ``folder``.

    Categories come from the sub-folder names, which gives a usable
    library out of any existing folder tree.
    """
    folder = os.path.abspath(os.path.expanduser(folder))
    records = []
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        rel = os.path.relpath(root, folder)
        cats = [] if rel == "." else rel.split(os.sep)
        for f in sorted(files):
            full = os.path.join(root, f)
            mtype = guess_media_type(full)
            if mtype in MEDIA_TYPES:
                records.append({"path": full, "type": mtype, "categories": cats, "tags": []})
        if not recursive:
            break
    return records
