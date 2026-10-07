"""GalleryWidget: the embeddable component (tree | grid | details).

    from ilp_gallery import GalleryWidget
    w = GalleryWidget()
    w.add_source(MySource())                               # or w.load("/path/megascans.json.gz")
    w.register_exporter(ingest, label="Ingest", arg="json", default=True)
    w.register_drag_handler(lambda items: [i.path for i in items])
    w.add_action("Open in RV", lambda items: ...)          # extra context-menu entries

For the full app window (menu, export log, rescan, settings) use
``ilp_gallery.ui.browser.GalleryBrowser``. No DCC imports here.
"""

from __future__ import annotations

import os
import subprocess
from typing import Callable, Iterable

import time

from PySide6.QtCore import QItemSelectionModel, QModelIndex, QObject, QRunnable, QThreadPool, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMenu, QPushButton, QSlider, QSplitter, QVBoxLayout, QWidget,
)

from ..core.item import GalleryItem
from ..core.library import GalleryLibrary
from ..core.source import DataSource, source_for_path
from .actions import CommandExporter, DragHandler, Exporter, build_mime
from ..media.thumbs import ThumbnailCache
from . import theme as theme_mod
from .delegate import TileDelegate
from .details import DetailPanel
from .model import GalleryModel, ItemRole
from .search import SearchField, quote_value
from .tasks import TaskThread, format_command
from .tree import CategoryTree
from .view import GalleryView

ItemsCallback = Callable[[list[GalleryItem]], None]


class _JobSignals(QObject):
    done = Signal(object, object, object)  # token, result, error


class _Job(QRunnable):
    """Run ``fn()`` on the pool; deliver (token, result, error) on the GUI thread."""

    def __init__(self, fn, token, signals: _JobSignals) -> None:
        super().__init__()
        self.fn, self.token, self.signals = fn, token, signals

    def run(self) -> None:
        try:
            self.signals.done.emit(self.token, self.fn(), None)
        except Exception as exc:
            self.signals.done.emit(self.token, None, exc)


class GalleryWidget(QWidget):
    itemActivated = Signal(object)        # GalleryItem (double-click / Enter)
    selectionChanged = Signal(list)       # list[GalleryItem]
    currentChanged = Signal(object)       # GalleryItem | None (shown in details)
    queryChanged = Signal(str)
    countChanged = Signal(int, int)       # visible, total
    exportRequested = Signal(list)        # list[GalleryItem] (emitted before exporting)
    logMessage = Signal(str)              # export / load / poll messages
    taskStarted = Signal(str, bool)       # status text, stoppable
    taskProgress = Signal(int, int)
    taskFinished = Signal(bool)           # ok
    sourceLoaded = Signal(object, int)    # DataSource, item count
    sourceFailed = Signal(object, str)    # DataSource, error
    _libraryChanged = Signal()            # thread-safe hop for library listeners

    def __init__(self, library: GalleryLibrary | None = None, cache: ThumbnailCache | None = None,
                 parent: QWidget | None = None, show_sidebar: bool = True,
                 show_details: bool = True, tile_aspect: float | None = None,
                 styled: bool = True, theme: "theme_mod.Theme | str | None" = None,
                 **_ignored) -> None:
        """``theme``: "auto" (default; match Houdini / Nuke / Maya, ILP look
        standalone), "host", "ilp" or a :class:`~ilp_gallery.ui.theme.Theme`."""
        super().__init__(parent)
        self.setObjectName("ILPGallery")
        self._styled_root = styled and parent is None
        if self._styled_root:
            self.setProperty("ilpRoot", True)
            self.setObjectName("ILPGalleryRoot")
        self.theme = theme_mod.resolve(theme)
        self.library = library or GalleryLibrary()
        self.cache = cache or ThumbnailCache(parent=self)
        self.command_template = ""
        self._actions: list[tuple[str, ItemsCallback]] = []
        self._exporters: list[Exporter] = []
        self._default_exporter: Exporter | None = None
        self._drag_handlers: list[DragHandler] = []
        self._task: TaskThread | None = None
        self._loading: set[str] = set()
        self._polling: set[str] = set()
        self._last_poll: dict[str, float] = {}
        self._jobs = _JobSignals()          # no Qt parent: see PlayerWidget for why
        self._jobs.done.connect(self._on_job_done)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(4)
        self._export_enabled = False
        self._path_filter: tuple = ()
        self._search_ids: list[str] = []

        # -- top bar -----------------------------------------------------
        self.search = SearchField()
        self.search.setPlaceholderText("Search names, tags, IDs…    tag:rock  cat:cliff  meta:tileable  -moss")
        self.search.setMaximumWidth(560)
        self.size_slider = QSlider(Qt.Horizontal, minimum=90, maximum=420, value=170)
        self.size_slider.setFixedWidth(140)
        self.size_slider.setToolTip("Tile size (Ctrl + wheel)")
        self.toolbar = QHBoxLayout()
        self.toolbar.setSpacing(2)
        top = QHBoxLayout()
        top.setContentsMargins(8, 6, 8, 6)
        top.setSpacing(10)
        top.addWidget(self.search, 3)
        top.addWidget(QLabel("Size"))
        top.addWidget(self.size_slider)
        top.addStretch(1)
        top.addLayout(self.toolbar)

        # -- panes -------------------------------------------------------
        self.tree = CategoryTree()
        self.model = GalleryModel(self.library, self)
        self.model.mime_builder = lambda items: build_mime(items, self._drag_handlers, self.logMessage.emit)
        self.cache.thumbnail_provider = self._source_thumbnail
        self.delegate = TileDelegate(self.cache, self)
        self.view = GalleryView(self.delegate)
        self.view.setStyleSheet("")
        self.view.setModel(self.model)
        self._auto_aspect = tile_aspect is None
        if tile_aspect:
            self.delegate.aspect = tile_aspect
        self.view.set_tile_width(self.size_slider.value())
        self.details = DetailPanel(self.cache)
        self.details.setMinimumWidth(300)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        for w in (self.tree, self.view, self.details):
            self.splitter.addWidget(w)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([270, 860, 380])
        self.tree.setVisible(show_sidebar)
        self.details.setVisible(show_details)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(top)
        lay.addWidget(self.splitter, 1)

        # -- wiring ------------------------------------------------------
        self.search.queryChanged.connect(self._run_query)
        self.size_slider.valueChanged.connect(self.view.set_tile_width)
        self.tree.pathSelected.connect(self._on_path_selected)
        self.view.activated.connect(self._on_activated)
        self.view.doubleClicked.connect(self._on_activated)
        self.view.selectionModel().selectionChanged.connect(self._on_selection)
        self.view.selectionModel().currentChanged.connect(lambda cur, _p: self._show_current(cur))
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.view.viewport().installEventFilter(self)
        self.details.exportRequested.connect(lambda items: self.export(items))
        self.details.exportMenuRequested.connect(self._export_menu)
        self.details.copyCommandRequested.connect(self.copy_command)
        self.details.openFolderRequested.connect(lambda it: _open(_folder(it)))
        self.details.categoryClicked.connect(self.tree.select_path)
        self.details.tagClicked.connect(lambda t: self.search.toggle_token(f"tag:{quote_value(t)}"))
        self.cache.thumbnailReady.connect(self._on_thumb_ready)
        self._libraryChanged.connect(lambda: self._library_dirty.start())
        self.library.add_listener(self._on_library_changed)
        self._poll_timer = QTimer(self, interval=1000)
        self._poll_timer.timeout.connect(self._poll_sources)
        self._poll_timer.start()

        QShortcut(QKeySequence("Ctrl+F"), self, self.search.setFocus, context=Qt.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence("Ctrl+C"), self.view, self.copy_paths, context=Qt.WidgetShortcut)

        self._library_dirty = QTimer(self, singleShot=True, interval=50)
        self._library_dirty.timeout.connect(self._refresh_from_library)
        self._refresh_from_library()
        self.set_theme(self.theme)

    # -- public API ------------------------------------------------------
    # ---------------------------------------------------------------- look
    def set_theme(self, theme: "theme_mod.Theme | str | None") -> None:
        """Switch colours live: "auto", "host", "ilp" or a Theme."""
        self.theme = theme_mod.resolve(theme)
        if self._styled_root:
            self.setStyleSheet(self.theme.stylesheet())
        self.delegate.set_theme(self.theme)
        self.tree.set_theme(self.theme)
        self.details.set_theme(self.theme)
        self.view.viewport().update()

    # ---------------------------------------------------------------- data
    def add_source(self, source: DataSource, background: bool = True) -> None:
        """Add a data source. With ``background`` the (possibly slow) load runs off the GUI thread;
        ``sourceLoaded`` / ``sourceFailed`` report the outcome."""
        self.library.register_source(source)
        self._reload(source, background)

    def reload(self, background: bool = True) -> None:
        for src in self.library.sources:
            self._reload(src, background)

    def reload_source(self, source: DataSource | str, background: bool = True) -> None:
        src = self.library.source(source) if isinstance(source, str) else source
        if src is not None:
            self._reload(src, background)

    def load(self, path: str, recursive: bool = False, replace: bool = False,
             background: bool = False) -> int:
        """Load a library JSON (.json / .json.gz), folder of them, or a media folder.

        Blocking by default (returns the item count); ``background=True`` returns 0 at once.
        """
        if replace:
            self.library.clear()
        src = source_for_path(path, recursive=recursive)
        src = self.library.source(src.name) or src
        self.library.register_source(src)
        if background:
            self._reload(src, True)
            return 0
        n = self.library.reload_source(src)
        self.sourceLoaded.emit(src, n)
        return n

    def is_loading(self) -> bool:
        return bool(self._loading)

    def set_items(self, records: Iterable[dict | GalleryItem]) -> None:
        self.library.clear()
        self.library.add_records(list(records))

    def _reload(self, src: DataSource, background: bool) -> None:
        if not background:
            try:
                n = self.library.reload_source(src)
            except Exception as exc:
                self._report_failure(src, exc)
                return
            self.sourceLoaded.emit(src, n)
            return
        if src.name in self._loading:
            return
        self._loading.add(src.name)
        self.logMessage.emit(f"Loading {src.label}…")
        lib = self.library
        self._pool.start(_Job(lambda: lib.prepare(src, src.load()), ("load", src), self._jobs))

    def _report_failure(self, src: DataSource, exc: Exception) -> None:
        msg = f"{type(exc).__name__}: {exc}"
        self.logMessage.emit(f"ERROR loading {src.label}: {msg}")
        self.sourceFailed.emit(src, msg)

    def _poll_sources(self) -> None:
        now = time.monotonic()
        for src in self.library.sources:
            iv = getattr(src, "poll_interval", 0) or 0
            if iv <= 0 or src.name in self._polling or src.name in self._loading:
                continue
            if now - self._last_poll.setdefault(src.name, now) < iv:
                continue
            self._last_poll[src.name] = now
            self._polling.add(src.name)
            self._pool.start(_Job(src.poll, ("poll", src), self._jobs))

    def _on_job_done(self, token, result, error) -> None:
        kind, src = token
        if kind == "load":
            self._loading.discard(src.name)
            if error is not None:
                self._report_failure(src, error)
                return
            n = self.library.apply(src, result, reset=True)
            self.sourceLoaded.emit(src, n)
        elif kind == "poll":
            self._polling.discard(src.name)
            if error is not None:
                self.logMessage.emit(f"{src.label}: poll failed: {error}")
            elif result and result.reset:
                self.logMessage.emit(f"{src.label} changed on disk: reloading.")
                self._reload(src, True)
            elif result:
                self.library.apply_changes(src, result)

    def _source_thumbnail(self, item: GalleryItem):
        src = self.library.source_of(item)
        return src.thumbnail(item) if src is not None else None

    # ------------------------------------------------------------ exporters
    def register_exporter(self, fn: Callable, label: str = "Export", arg: str = "item",
                          default: bool = False, batch: bool = False, threaded: bool = True,
                          sources: Iterable[str] | None = None) -> Exporter:
        """Register an export callback (see ``ilp_gallery.ui.actions``).

        ``arg`` = what ``fn`` receives: "item", a field ("path", "id") or a metadata key ("json").
        The first exporter, or the one with ``default=True``, runs from the Export button.
        """
        ex = Exporter(fn=fn, label=label, arg=arg, batch=batch, threaded=threaded,
                      sources=tuple(sources) if sources is not None else None)
        self._exporters.append(ex)
        if default or self._default_exporter is None:
            self._default_exporter = ex
        self.set_export_enabled(True)
        self._show_current(self.view.currentIndex(), force=True)
        return ex

    def unregister_exporter(self, exporter: Exporter) -> None:
        if exporter in self._exporters:
            self._exporters.remove(exporter)
        if self._default_exporter is exporter:
            self._default_exporter = self._exporters[0] if self._exporters else None

    def exporters(self, items: list[GalleryItem] | None = None) -> list[Exporter]:
        """Registered exporters (only those accepting all ``items`` when given)."""
        exs = list(self._exporters)
        if self.command_template:
            exs.append(CommandExporter(self.command_template))
        if items:
            exs = [e for e in exs if all(e.accepts(it) for it in items)]
        return exs

    def export(self, items: list[GalleryItem] | None = None, exporter: Exporter | None = None) -> bool:
        """Run ``exporter`` (default: the default one) on ``items`` (default: selection)."""
        items = items if items is not None else self.selected_items()
        if not items:
            self.logMessage.emit("Nothing selected.")
            return False
        if exporter is None:
            cands = self.exporters(items)
            exporter = self._default_exporter if self._default_exporter in cands else (cands[0] if cands else None)
        if exporter is None:
            self.logMessage.emit("No exporter for these assets: register one with register_exporter() "
                                 "or set an export command in Settings.")
            return False
        self.exportRequested.emit(items)
        n = len(items)
        status = f"{exporter.label}: {n} asset{'s' if n != 1 else ''}…"
        if not exporter.threaded:  # host scene APIs: must run on the GUI thread
            self.taskStarted.emit(status, False)
            ok, failed = exporter.run(items, self.logMessage.emit, on_progress=self.taskProgress.emit)
            self.taskFinished.emit(failed == 0)
            return failed == 0

        def job(log, cancelled, thread):
            ok, failed = exporter.run(items, log, cancelled, on_progress=thread.progress.emit,
                                      set_cancel_hook=lambda fn: setattr(thread, "on_cancel", fn))
            return failed == 0
        return self.run_task(job, status, stoppable=True)

    # ---------------------------------------------------------------- drag
    def register_drag_handler(self, fn: Callable[[list[GalleryItem]], object],
                              replace_default: bool = False,
                              sources: Iterable[str] | None = None) -> DragHandler:
        """Add data to drags out of the gallery (see ``ilp_gallery.ui.actions``)."""
        h = DragHandler(fn, replace_default, tuple(sources) if sources is not None else None)
        self._drag_handlers.append(h)
        return h

    def unregister_drag_handler(self, handler: DragHandler) -> None:
        if handler in self._drag_handlers:
            self._drag_handlers.remove(handler)

    # ---------------------------------------------------------------- tasks
    def run_task(self, fn: Callable, status: str, stoppable: bool = True,
                 on_done: Callable[[bool], None] | None = None) -> bool:
        """Run ``fn(log, cancelled, thread)`` on a worker thread, one task at a time."""
        if self._task is not None and self._task.isRunning():
            self.logMessage.emit("Busy: wait for the current job or press Stop.")
            return False
        t = TaskThread(fn, status, self)
        t.logged.connect(self.logMessage)
        t.progress.connect(self.taskProgress)

        def done(ok: bool) -> None:
            self.taskFinished.emit(ok)
            if on_done:
                on_done(ok)
        t.finishedOk.connect(done)
        old, self._task = self._task, t
        if old is not None and not old.isRunning():
            old.setParent(None)  # see TaskThread notes: never deleteLater()
        self.taskStarted.emit(status, stoppable)
        t.start()
        return True

    def stop_task(self) -> None:
        if self._task is not None and self._task.isRunning():
            self._task.cancel()

    def task_running(self) -> bool:
        return self._task is not None and self._task.isRunning()

    def set_query(self, query: str) -> None:
        self.search.setText(query)
        self._run_query(query)

    def query(self) -> str:
        return self.search.text()

    def selected_items(self) -> list[GalleryItem]:
        rows = sorted(self.view.selectionModel().selectedIndexes(), key=lambda i: i.row())
        return [i.data(ItemRole) for i in rows]

    def current_item(self) -> GalleryItem | None:
        return self.details.item

    def visible_items(self) -> list[GalleryItem]:
        return [self.library.get(i) for i in self.model.ids()]

    def select(self, item_id: str) -> None:
        idx = self.model.index_of(item_id)
        if idx.isValid():
            self.view.selectionModel().setCurrentIndex(idx, QItemSelectionModel.ClearAndSelect)
            self.view.scrollTo(idx)

    def select_category(self, path: Iterable[str]) -> None:
        self.tree.select_path(tuple(p.lower() for p in path))

    def add_action(self, label: str, callback: ItemsCallback, default: bool = False) -> None:
        """Context-menu action receiving the selected items. ``default`` also runs on double-click."""
        self._actions.append((label, callback))
        if default:
            self._default_action = callback

    def add_toolbar_button(self, text: str, callback: Callable[[], None], tooltip: str = "") -> QPushButton:
        b = QPushButton(text, objectName="flat")
        b.setToolTip(tooltip)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(callback)
        self.toolbar.addWidget(b)
        return b

    def set_export_enabled(self, on: bool) -> None:
        """Show Export / Copy command (automatic once an exporter is registered)."""
        self._export_enabled = on
        self.details.set_export_enabled(on)

    def set_sidebar_visible(self, on: bool) -> None:
        self.tree.setVisible(on)

    def set_details_visible(self, on: bool) -> None:
        self.details.setVisible(on)
        if on:
            self._show_current(self.view.currentIndex())
        else:
            self.details.player.stop()

    def set_tile_width(self, width: int) -> None:
        self.size_slider.setValue(width)

    def set_tile_aspect(self, aspect: float) -> None:
        self._auto_aspect = False
        self.delegate.aspect = aspect
        self.view.set_tile_width(self.size_slider.value())

    def copy_paths(self) -> None:
        paths = [it.path for it in self.selected_items()]
        if paths:
            QGuiApplication.clipboard().setText("\n".join(paths))

    def copy_command(self, items: list[GalleryItem] | None = None) -> str:
        items = items or self.selected_items()
        if not items:
            return ""
        if self.command_template:
            text = "\n".join(format_command(self.command_template, it) for it in items)
        else:
            text = "\n".join(_folder(it) for it in items)
        QGuiApplication.clipboard().setText(text)
        return text

    # -- internals -------------------------------------------------------
    _default_action: ItemsCallback | None = None

    def _on_library_changed(self) -> None:
        self._libraryChanged.emit()  # may come from any thread; coalesced on the GUI thread

    def _refresh_from_library(self) -> None:
        if self._auto_aspect and len(self.library):
            aspect = float(next(iter(self.library)).metadata.get("tile_aspect") or 16 / 9)
            if abs(aspect - self.delegate.aspect) > 1e-3:
                self.delegate.aspect = aspect
                self.view.set_tile_width(self.size_slider.value())
        self.tree.blockSignals(True)
        self.tree.build(self.library)
        self.tree.blockSignals(False)
        self._path_filter = self.tree.current_path()
        self.search.set_vocabulary(list(self.library.tags()), list(self.library.categories()))
        self._run_query(self.search.text())

    def _run_query(self, text: str) -> None:
        self._search_ids = self.library.search(text)
        self.tree.update_counts(set(self._search_ids) if text.strip() else None)
        self._apply_filters()
        self.queryChanged.emit(text)

    def _on_path_selected(self, path: tuple) -> None:
        self._path_filter = tuple(path)
        self._apply_filters()

    def _apply_filters(self) -> None:
        current = self.details.item.id if self.details.item else None
        keep = {it.id for it in self.selected_items()}
        self.cache.cancel_pending()  # thumbnails for tiles that just disappeared
        ids = self._search_ids
        if self._path_filter:
            members = self.tree.ids.get(self._path_filter, set())
            ids = [i for i in ids if i in members]
        self.model.set_ids(ids)
        sel = self.view.selectionModel()
        for iid in keep:
            idx = self.model.index_of(iid)
            if idx.isValid():
                sel.select(idx, QItemSelectionModel.Select)
        cur = self.model.index_of(current) if current else QModelIndex()
        if cur.isValid():
            sel.setCurrentIndex(cur, QItemSelectionModel.NoUpdate)
        self.countChanged.emit(len(ids), len(self.library))

    def _show_current(self, index: QModelIndex, force: bool = False) -> None:
        item = index.data(ItemRole) if index.isValid() else None
        if not self.details.isVisible():
            return
        if not force and item is not None and self.details.item is not None and item.id == self.details.item.id:
            return
        if item is not None:
            exs = self.exporters([item])
            d = self._default_exporter
            self.details.set_export_label(d.label if d in exs else (exs[0].label if exs else "Export"),
                                          more=len(exs) > 1)
        self.details.set_item(item)
        self.currentChanged.emit(item)

    def _on_thumb_ready(self, item_id: str) -> None:
        self.view.refresh_item(self.model.index_of(item_id))
        d = self.details
        if d.item and d.item.id == item_id and d.player.canvas._pix is None:
            d.player._show_first_poster()

    def _on_activated(self, index: QModelIndex) -> None:
        item = index.data(ItemRole)
        if item is None:
            return
        self.itemActivated.emit(item)
        if self._default_action:
            self._default_action([item])
            return
        if not self.details.isVisible():
            self.set_details_visible(True)
        self.details.set_item(item, autoplay=True)

    def _on_selection(self, *_):
        self.selectionChanged.emit(self.selected_items())

    def _context_menu(self, pos) -> None:
        items = self.selected_items()
        idx = self.view.indexAt(pos)
        if idx.isValid() and idx.data(ItemRole) not in items:
            self.view.selectionModel().setCurrentIndex(idx, QItemSelectionModel.ClearAndSelect)
            items = self.selected_items()
        if not items:
            return
        n = len(items)
        menu = QMenu(self)
        for ex in self.exporters(items):
            menu.addAction(f"{ex.label} ({n})" if n > 1 else ex.label,
                           lambda ex=ex: self.export(items, ex))
        for label, cb in self._actions:
            menu.addAction(label, lambda cb=cb, items=items: cb(items))
        menu.addSeparator()
        if self._export_enabled:
            menu.addAction("Copy command", lambda: self.copy_command(items))
        menu.addAction("Copy path" + ("s" if n > 1 else ""), self.copy_paths)
        menu.addAction("Copy ID" + ("s" if n > 1 else ""),
                       lambda: QGuiApplication.clipboard().setText("\n".join(i.id for i in items)))
        menu.addAction("Open folder", lambda: _open(_folder(items[0])))
        if items[0].tags:
            sub = menu.addMenu("Filter by tag")
            for t in items[0].tags[:30]:
                sub.addAction(t, lambda t=t: self.search.toggle_token(f"tag:{quote_value(t)}"))
        menu.addSeparator()
        menu.addAction("Refresh thumbnail", lambda: self._refresh_thumbs(items))
        menu.exec(self.view.viewport().mapToGlobal(pos))

    def _export_menu(self, items: list[GalleryItem], pos) -> None:
        menu = QMenu(self)
        for ex in self.exporters(items):
            menu.addAction(ex.label, lambda ex=ex: self.export(items, ex))
        menu.exec(pos)

    def _refresh_thumbs(self, items: list[GalleryItem]) -> None:
        for it in items:
            self.cache.invalidate(it.id)
            self.view.refresh_item(self.model.index_of(it.id))

    def eventFilter(self, obj, event) -> bool:
        if obj is self.view.viewport() and event.type() == event.Type.Wheel and event.modifiers() & Qt.ControlModifier:
            step = 16 if event.angleDelta().y() > 0 else -16
            self.size_slider.setValue(self.size_slider.value() + step)
            return True
        return super().eventFilter(obj, event)

    def closeEvent(self, e) -> None:
        self._poll_timer.stop()
        self.stop_task()
        if self._task is not None:
            self._task.wait(3000)
        self.details.player.stop()
        self.library.remove_listener(self._on_library_changed)
        super().closeEvent(e)


def _folder(item: GalleryItem) -> str:
    return item.metadata.get("asset_dir") or os.path.dirname(item.path)


def _open(path: str) -> None:
    subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
