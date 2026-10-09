"""Background jobs for the browser: export (a command per item) and rescan.

The export is a command template, so each studio plugs in its own tool::

    stockpile export --asset {asset_dir}
    python /tools/ms_export.py --id {id} --json {json}

Placeholders: {id} {name} {path} {asset_dir} {json} {type} {type_label}
{thumbnail}. Values are shell-quoted; the command runs without a shell.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import threading
from typing import Callable

from PySide6.QtCore import QThread, Signal

from ..core.item import GalleryItem


class _Fields(dict):
    def __missing__(self, key):
        return "{" + key + "}"  # unknown placeholders are left as-is


def item_fields(item: GalleryItem) -> dict[str, str]:
    m = item.metadata
    return {
        "id": item.id, "name": item.name, "path": item.path, "type": item.type,
        "asset_dir": m.get("asset_dir") or os.path.dirname(item.path),
        "json": m.get("json") or "", "type_label": m.get("type_label") or item.type,
        "thumbnail": item.thumbnail or "",
        "preview": (item.motion or (item.preview, ""))[0] or item.preview,
    }


def format_command(template: str, item: GalleryItem) -> str:
    fields = _Fields({k: shlex.quote(str(v)) for k, v in item_fields(item).items()})
    return template.format_map(fields)


class TaskThread(QThread):
    """Runs ``fn(log, cancelled)`` off the GUI thread; ``log`` is thread-safe."""

    logged = Signal(str)
    progress = Signal(int, int)
    finishedOk = Signal(bool)

    def __init__(self, fn: Callable, name: str, parent=None) -> None:
        super().__init__(parent)
        self.fn, self.name = fn, name
        self._cancel = threading.Event()
        self.on_cancel: Callable[[], None] | None = None

    def cancel(self) -> None:
        self._cancel.set()
        if self.on_cancel:
            self.on_cancel()

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> None:
        ok = False
        try:
            ok = bool(self.fn(self.logged.emit, self.cancelled, self))
        except Exception as exc:  # report, never crash the host app
            self.logged.emit(f"ERROR: {type(exc).__name__}: {exc}")
        self.finishedOk.emit(ok and not self.cancelled())


def make_export_job(items: list[GalleryItem], template: str) -> Callable:
    def job(log, cancelled, thread: TaskThread) -> bool:
        failures = 0
        for n, it in enumerate(items, 1):
            if cancelled():
                log("Export stopped.")
                return False
            cmd = format_command(template, it)
            log(f"[{n}/{len(items)}] {it.name} ({it.id})")
            log(f"$ {cmd}")
            try:
                proc = subprocess.Popen(shlex.split(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, bufsize=1)
            except OSError as exc:
                log(f"   failed to start: {exc}")
                failures += 1
                continue
            thread.on_cancel = proc.kill
            if cancelled():  # Stop pressed while the process was starting
                proc.kill()
            for line in proc.stdout:
                log("   " + line.rstrip())
            rc = proc.wait()
            thread.on_cancel = None
            if rc != 0 and not cancelled():
                failures += 1
                log(f"   exit code {rc}")
            thread.progress.emit(n, len(items))
        log(f"Export finished: {len(items) - failures} ok, {failures} failed.")
        return failures == 0
    return job
