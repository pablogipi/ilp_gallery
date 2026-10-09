"""Asynchronous thumbnail + scrub-frame cache.

* Posters are generated in a private QThreadPool (never blocks the DCC UI).
* Results are written to a disk cache keyed by path+mtime+size, so they
  survive restarts and can be shared (point ``ILP_GALLERY_CACHE`` at a
  network location to share between artists).
* Posters live in memory in an LRU with a byte budget; scrub frames are
  loaded on hover for the few items being scrubbed.
"""

from __future__ import annotations

import dataclasses
import hashlib
import itertools
import logging
import os
from collections import OrderedDict

from PySide6.QtCore import QObject, QRunnable, QSize, QThreadPool, Qt, Signal
from PySide6.QtGui import QImage, QImageReader, QPixmap

from ..core.item import MEDIA_GIF, MEDIA_MOVIE, GalleryItem
from . import ffmpeg

log = logging.getLogger(__name__)

DEFAULT_CACHE = os.environ.get(
    "ILP_GALLERY_CACHE", os.path.join(os.path.expanduser("~"), ".cache", "ilp_gallery", "thumbs")
)
THUMB_WIDTH = 384      # generated resolution; tiles can scale down from this
SCRUB_FRAMES = 12      # frames sampled per movie / gif for hover scrub
_DONE = "done"


def _cache_key(path: str, width: int, frames: int) -> str | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    raw = f"{path}|{st.st_mtime_ns}|{st.st_size}|{width}|{frames}"
    return hashlib.sha1(raw.encode()).hexdigest()


def _read_scaled(path: str, width: int) -> QImage:
    """Decode with Qt at reduced size (JPEG decodes much faster this way)."""
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and size.width() > width:
        reader.setScaledSize(size.scaled(width, 10 ** 6, Qt.KeepAspectRatio))
    return reader.read()


def _gif_frames(path: str, out_dir: str, count: int, width: int) -> list[str]:
    reader = QImageReader(path)
    total = reader.imageCount()
    if total <= 0:
        total = 1
    wanted = sorted({min(total - 1, int(total * (i + 0.5) / count)) for i in range(count)})
    files = []
    for idx in range(total):
        img = reader.read()
        if img.isNull():
            break
        if idx in wanted:
            if img.width() > width:
                img = img.scaledToWidth(width, Qt.SmoothTransformation)
            dst = os.path.join(out_dir, f"f{len(files) + 1:03d}.jpg")
            img.convertToFormat(QImage.Format_RGB888).save(dst, "JPG", 85)
            files.append(dst)
        if idx >= wanted[-1]:
            break
    return files


def _provided_thumbnail(item: GalleryItem, provider, cache_dir: str) -> str | None:
    """Ask the data source for a thumbnail (bytes or path); bytes are cached to disk."""
    try:
        data = provider(item)
    except Exception:
        log.exception("thumbnail() failed for %s", item.id)
        return None
    if not data:
        return None
    if isinstance(data, str):
        return data if os.path.exists(data) else None
    key = hashlib.sha1(f"provided|{item.source}|{item.id}".encode()).hexdigest()
    dst = os.path.join(cache_dir, "provided", key[:2], key + ".img")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as fh:
        fh.write(bytes(data))
    return dst


def generate(item: GalleryItem, cache_dir: str, width: int = THUMB_WIDTH,
             frames: int = SCRUB_FRAMES, provider=None) -> list[str]:
    """Create (or reuse) cached frames for ``item``. Returns jpg paths; [0] is the poster.

    Pure function, safe to call from worker threads (QImage is thread-safe).
    """
    if provider is not None and not (item.thumbnail and os.path.exists(item.thumbnail)):
        provided = None
        if not os.path.exists(item.path) or not item.is_animated:
            provided = _provided_thumbnail(item, provider, cache_dir)
        if provided:
            item = GalleryItem(**{**item.__dict__, "thumbnail": provided})
            if not os.path.exists(item.path):  # remote item: the provided image is all we have
                img = _read_scaled(provided, width)
                if img.isNull():
                    return []
                key = hashlib.sha1(f"{provided}|{os.path.getmtime(provided)}|{width}".encode()).hexdigest()
                out = os.path.join(cache_dir, key[:2], key)
                os.makedirs(out, exist_ok=True)
                dst = os.path.join(out, "f001.jpg")
                img.convertToFormat(QImage.Format_RGB888).save(dst, "JPG", 88)
                return [dst]
    # Items that don't move themselves (a published clip, a model...) can carry a
    # preview: a movie / GIF gives the scrub frames and, without a thumbnail, the
    # poster; a still image is used as the thumbnail.
    if not item.is_animated and item.preview and os.path.exists(item.preview):
        if item.motion is not None:
            item = item.motion_item()
        elif not (item.thumbnail and os.path.exists(item.thumbnail)):
            item = dataclasses.replace(item, thumbnail=item.preview)
    n = frames if item.is_animated else 1
    key = _cache_key(item.path, width, n)
    if key is None:
        return []
    out = os.path.join(cache_dir, key[:2], key)
    if os.path.exists(os.path.join(out, _DONE)):
        return sorted(os.path.join(out, f) for f in os.listdir(out) if f.endswith(".jpg"))
    os.makedirs(out, exist_ok=True)

    files: list[str] = []
    if item.type == MEDIA_MOVIE:
        files = ffmpeg.extract_frames(item.path, out, n, width)
    elif item.type == MEDIA_GIF:
        files = _gif_frames(item.path, out, n, width)
    elif item.thumbnail and os.path.exists(item.thumbnail):
        pass  # still image with its own thumbnail: don't decode the (bigger) main file
    else:
        dst = os.path.join(out, "f001.jpg")
        img = _read_scaled(item.path, width)
        if not img.isNull():
            img.convertToFormat(QImage.Format_RGB888).save(dst, "JPG", 88)
            files = [dst]
        elif ffmpeg.extract_still(item.path, dst, width):  # EXR, DPX, ...
            files = [dst]

    # A user supplied thumbnail wins as poster, scrub frames are kept.
    if item.thumbnail and os.path.exists(item.thumbnail):
        img = _read_scaled(item.thumbnail, width)
        if not img.isNull():
            dst = os.path.join(out, "f000.jpg")
            img.convertToFormat(QImage.Format_RGB888).save(dst, "JPG", 88)
            files.insert(0, dst)

    if files:
        open(os.path.join(out, _DONE), "w").close()
    return files


class _Signals(QObject):
    finished = Signal(str, list)  # item_id, frame paths


class _Job(QRunnable):
    def __init__(self, item: GalleryItem, cache_dir: str, signals: _Signals, provider=None) -> None:
        super().__init__()
        self.item = item
        self.cache_dir = cache_dir
        self.signals = signals
        self.provider = provider
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            files = generate(self.item, self.cache_dir, provider=self.provider)
        except Exception:  # never let a bad file kill a pool thread
            log.exception("Thumbnail failed for %s", self.item.path)
            files = []
        self.signals.finished.emit(self.item.id, files)


class _LRU(OrderedDict):
    def __init__(self, budget: int, cost=lambda v: 1) -> None:
        super().__init__()
        self.budget, self.cost, self.used = budget, cost, 0

    def get_(self, k):
        if k in self:
            self.move_to_end(k)
            return self[k]
        return None

    def put(self, k, v) -> None:
        if k in self:
            self.used -= self.cost(self.pop(k))
        self[k] = v
        self.used += self.cost(v)
        while self.used > self.budget and len(self) > 1:
            _, old = self.popitem(last=False)
            self.used -= self.cost(old)


def _pix_bytes(p) -> int:
    if isinstance(p, list):
        return sum(_pix_bytes(x) for x in p)
    return p.width() * p.height() * 4 if p is not None else 0


class ThumbnailCache(QObject):
    """Owns the worker pool and in-memory LRUs. One per gallery widget (or shared)."""

    thumbnailReady = Signal(str)  # item_id

    def __init__(self, cache_dir: str = DEFAULT_CACHE, threads: int | None = None,
                 memory_mb: int = 256, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.cache_dir = cache_dir
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(threads or max(2, min(6, (os.cpu_count() or 4) // 2)))
        self._signals = _Signals()
        self._signals.finished.connect(self._on_finished)
        self._posters = _LRU(memory_mb * 1024 * 1024, _pix_bytes)
        self._scrub = _LRU(24)                   # items with scrub frames in memory
        self._paths: dict[str, list[str]] = {}   # item_id -> frame files on disk
        self._pending: set[str] = set()
        self._failed: set[str] = set()
        self._prio = itertools.count()
        #: optional callable(item) -> bytes | path | None (the item's DataSource.thumbnail)
        self.thumbnail_provider = None

    # -- public --------------------------------------------------------
    def poster(self, item: GalleryItem) -> QPixmap | None:
        """Poster pixmap if ready, else None (and a job is queued)."""
        pix = self._posters.get_(item.id)
        if pix is not None:
            return pix
        files = self._paths.get(item.id)
        if files:
            pix = QPixmap(files[0])
            if not pix.isNull():
                self._posters.put(item.id, pix)
                return pix
        self.request(item)
        return None

    def frames(self, item: GalleryItem) -> list[QPixmap]:
        """Scrub frames (excluding a custom poster). Empty until generated."""
        cached = self._scrub.get_(item.id)
        if cached is not None:
            return cached
        files = [f for f in self._paths.get(item.id, []) if not f.endswith("f000.jpg")]
        if not files:
            return []
        pix = [QPixmap(f) for f in files]
        pix = [p for p in pix if not p.isNull()]
        self._scrub.put(item.id, pix)
        return pix

    def failed(self, item_id: str) -> bool:
        return item_id in self._failed

    def request(self, item: GalleryItem) -> None:
        if item.id in self._pending or item.id in self._failed or item.id in self._paths:
            return
        self._pending.add(item.id)
        # Newer requests get higher priority: what the user is looking at *now* comes first.
        self.pool.start(_Job(item, self.cache_dir, self._signals, self.thumbnail_provider),
                        next(self._prio) % 2**30)

    def cancel_pending(self) -> None:
        """Drop queued (not running) jobs, e.g. when the search result changes."""
        self.pool.clear()
        self._pending.clear()

    def invalidate(self, item_id: str, remove_from_disk: bool = True) -> None:
        files = self._paths.pop(item_id, None)
        if remove_from_disk and files:
            import shutil
            shutil.rmtree(os.path.dirname(files[0]), ignore_errors=True)
        self._posters.pop(item_id, None)
        self._scrub.pop(item_id, None)
        self._failed.discard(item_id)

    def shutdown(self) -> None:
        self.pool.clear()
        self.pool.waitForDone(2000)

    # -- internal ------------------------------------------------------
    def _on_finished(self, item_id: str, files: list) -> None:
        self._pending.discard(item_id)
        if files:
            self._paths[item_id] = files
        else:
            self._failed.add(item_id)
        self.thumbnailReady.emit(item_id)

    @staticmethod
    def preferred_size() -> QSize:
        return QSize(THUMB_WIDTH, THUMB_WIDTH * 9 // 16)
