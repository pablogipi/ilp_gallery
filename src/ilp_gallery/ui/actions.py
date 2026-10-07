"""Export callbacks and drag handlers: how tools plug their own code into the gallery.

Exporters
---------
::

    def ingest(json_file, log):              # `log` is optional: add it to get the Export log
        log(f"ingesting {json_file}")
        my_pipeline.ingest_megascans(json_file)

    gallery.register_exporter(ingest, label="Ingest into project", arg="json", default=True)

* ``arg`` picks what the callback receives per item: ``"item"`` (the GalleryItem),
  a field (``"path"``, ``"id"``, ``"name"``, ``"thumbnail"``) or a metadata key
  (``"json"``, ``"asset_dir"``, anything your source put in ``metadata``).
* ``batch=True`` calls it once with the list of values instead of once per item.
* ``threaded=True`` (default) runs it on a worker thread so the UI stays responsive
  and Stop works between items. Use ``threaded=False`` for code that must run on
  the host's main thread (``hou``, ``maya.cmds``, ``nuke`` scene edits).
* ``sources=["megascans"]`` limits it to items from those data sources.
* Return ``False`` or raise to mark the item as failed. ``print`` output is not
  captured; use ``log``.

Drag handlers
-------------
::

    def nuke_read_paths(items):
        return [texture_files(it, ["albedo"])["albedo"] for it in items]

    gallery.register_drag_handler(nuke_read_paths)

A handler gets the dragged items and returns any of:

* a list of file paths: sent as file URLs + plain text (what DCCs accept for drops)
* a ``str``: plain text
* a ``dict`` ``{mime_type: str | bytes}``
* a ``QMimeData``
* ``None``: nothing to add

Results of all handlers are merged on top of the default (file URLs of
``item.path`` + the JSON records); ``replace_default=True`` drops the default.
"""

from __future__ import annotations

import inspect
import json
import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from PySide6.QtCore import QMimeData, QUrl

from ..core.item import GalleryItem
from .tasks import format_command

MIME_ITEMS = "application/x-ilp-gallery-items"


def _accepts_kw(fn: Callable, name: str) -> bool:
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    return name in sig.parameters or any(p.kind is p.VAR_KEYWORD for p in sig.parameters.values())


@dataclass
class Exporter:
    fn: Callable
    label: str = "Export"
    arg: str = "item"
    batch: bool = False
    threaded: bool = True
    sources: tuple[str, ...] | None = None

    def accepts(self, item: GalleryItem) -> bool:
        if self.sources is not None and item.source not in self.sources:
            return False
        try:
            item.value(self.arg)
        except KeyError:
            return False
        return True

    def run(self, items: list[GalleryItem], log: Callable[[str], None],
            cancelled: Callable[[], bool] = lambda: False, on_progress=None,
            set_cancel_hook=None) -> tuple[int, int]:
        """Run on ``items``. Returns (ok, failed). Never raises."""
        items = [it for it in items if self.accepts(it)]
        kw = {"log": log} if _accepts_kw(self.fn, "log") else {}
        ok = failed = 0
        batches = [items] if self.batch else [[it] for it in items]
        for n, batch in enumerate(batches, 1):
            if cancelled():
                log("Export stopped.")
                break
            values = [it.value(self.arg) for it in batch]
            if not self.batch:
                log(f"[{n}/{len(batches)}] {batch[0].name} ({batch[0].id})")
            try:
                result = self.fn(values if self.batch else values[0], **kw)
            except Exception as exc:  # report and carry on with the next item
                log(f"   ERROR {type(exc).__name__}: {exc}")
                result = False
            if result is False:
                failed += len(batch)
            else:
                ok += len(batch)
            if on_progress:
                on_progress(n, len(batches))
        log(f"{self.label}: {ok} ok, {failed} failed.")
        return ok, failed


class CommandExporter(Exporter):
    """Runs a shell-free command template per item (the Settings "Export command")."""

    def __init__(self, template: str, label: str = "Run export command") -> None:
        super().__init__(fn=self._run_one, label=label, arg="item", threaded=True)
        self.template = template
        self._proc: subprocess.Popen | None = None

    def _run_one(self, item: GalleryItem, log) -> bool:
        cmd = format_command(self.template, item)
        log(f"$ {cmd}")
        try:
            self._proc = subprocess.Popen(shlex.split(cmd), stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT, text=True, bufsize=1)
        except OSError as exc:
            log(f"   failed to start: {exc}")
            return False
        if self._cancelled:
            self._proc.kill()
        for line in self._proc.stdout:
            log("   " + line.rstrip())
        rc = self._proc.wait()
        self._proc = None
        if rc and not self._cancelled:
            log(f"   exit code {rc}")
            return False
        return True

    _cancelled = False

    def run(self, items, log, cancelled=lambda: False, on_progress=None, set_cancel_hook=None):
        self._cancelled = False

        def kill():
            self._cancelled = True
            if self._proc:
                self._proc.kill()
        if set_cancel_hook:
            set_cancel_hook(kill)
        return super().run(items, log, cancelled, on_progress)


@dataclass
class DragHandler:
    fn: Callable[[list[GalleryItem]], Any]
    replace_default: bool = False
    sources: tuple[str, ...] | None = None


def default_mime(items: list[GalleryItem]) -> QMimeData:
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(it.path) for it in items])
    md.setText("\n".join(it.path for it in items))
    md.setData(MIME_ITEMS, json.dumps([it.to_dict() for it in items]).encode())
    return md


def _merge(md: QMimeData, result: Any) -> None:
    if result is None:
        return
    if isinstance(result, QMimeData):
        for fmt in result.formats():
            md.setData(fmt, result.data(fmt))
        if result.hasUrls():
            md.setUrls(result.urls())
        return
    if isinstance(result, str):
        md.setText(result)
        return
    if isinstance(result, dict):
        for mime, value in result.items():
            if mime == "text/plain":
                md.setText(str(value))
            elif mime == "text/uri-list" and isinstance(value, (list, tuple)):
                md.setUrls([QUrl.fromLocalFile(str(v)) for v in value])
            else:
                md.setData(mime, value if isinstance(value, bytes) else str(value).encode())
        return
    if isinstance(result, (list, tuple)):
        paths = [str(p) for p in result if p]
        md.setUrls([QUrl.fromLocalFile(p) for p in paths])
        md.setText("\n".join(paths))
        return
    raise TypeError(f"Drag handler returned unsupported {type(result).__name__}")


def build_mime(items: list[GalleryItem], handlers: Iterable[DragHandler], log=None) -> QMimeData:
    handlers = list(handlers)
    replace = any(h.replace_default for h in handlers)
    md = QMimeData() if replace else default_mime(items)
    for h in handlers:
        chosen = [it for it in items if h.sources is None or it.source in h.sources]
        if not chosen:
            continue
        try:
            _merge(md, h.fn(chosen))
        except Exception as exc:  # a broken handler must not kill the drag
            if log:
                log(f"Drag handler {getattr(h.fn, '__name__', h.fn)} failed: {exc}")
    return md
