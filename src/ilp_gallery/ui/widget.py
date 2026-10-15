"""GalleryWidget: the embeddable component (tree | grid | details).

    from ilp_gallery import GalleryWidget
    w = GalleryWidget()
    w.add_source(MySource())                               # or w.load("/path/megascans.json.gz")
    w.register_exporter(ingest, label="Ingest", arg="json", default=True)
    w.register_drag_handler(lambda items: [i.path for i in items])
    w.add_action("Open in RV", lambda items: ...)          # extra context-menu entries

The four panes (categories tree, tiles, details, drawer) collapse to a narrow
vertical tab, Maya style: ``collapsed=("tree", "details")`` builds them
collapsed, ``set_collapsed("drawer", True)`` collapses one later.

With ``drawer=True`` a drawer pane on the right collects chosen items (see
``ilp_gallery.ui.drawer``): ``drawerChanged`` reports edits, ``set_drawer()``
loads a list, and tiles in the drawer get a badge.

For the full app window (menu, export log, rescan, settings) use
``ilp_gallery.ui.browser.GalleryBrowser``. No DCC imports here.
"""

from __future__ import annotations

from collections import Counter
import json
import logging
import os
import shlex
import subprocess
import traceback
from typing import Callable, Iterable

import time

from PySide6.QtCore import QEvent, QItemSelectionModel, QModelIndex, QObject, QRect, QRunnable, QThreadPool, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMenu, QPushButton, QSlider, QSplitter, QStyle, QVBoxLayout, QWidget,
)

from ..core.item import GalleryItem
from ..core.library import GalleryLibrary
from ..core.source import DataSource, source_for_path
from .actions import MIME_ITEMS, CommandExporter, DragHandler, Exporter, build_mime
from ..media.thumbs import ThumbnailCache
from . import theme as theme_mod
from .delegate import TileDelegate
from .collapsible import CollapsiblePane, PaneArea
from .details import DetailPanel
from .names import format_name
from .drawer import MIME_DRAWER, DrawerEntry, DrawerWidget, build_key_index, entry_state
from .model import GalleryModel, ItemRole
from .search import SearchField, quote_value
from .tasks import BUILTIN_FIELDS, TaskThread, format_command, template_keys
from .tree import CategoryTree

log = logging.getLogger("ilp_gallery")

PLACEHOLDER_SVG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", "missing.svg")


def default_placeholder() -> QIcon:
    """Icon for items without a thumbnail: Houdini's own warning icon inside Houdini
    (loaded from the Houdini install), the bundled one elsewhere."""
    if theme_mod.detect_host() == "houdini":
        try:
            import hou
            icon = hou.qt.Icon("BUTTONS_list_warning_up")
            if icon is not None and not icon.isNull():
                return icon
        except Exception:
            pass
    return QIcon(PLACEHOLDER_SVG)


def format_traceback(exc: BaseException) -> str:
    """The full traceback of ``exc``, as Python prints it (works after the exception
    has crossed threads: the traceback travels with the exception object)."""
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip()
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


class _AppPaletteWatcher(QObject):
    """Calls ``callback`` after the application's palette changes.

    Qt 6 only sends ApplicationPaletteChange events to the application and
    top-level windows, and the gallery is usually embedded in a host pane, so
    listen to the application's paletteChanged signal instead (an event filter
    on the application would run Python for every event in the host). If a
    future Qt drops the signal, the refresh on show still applies.
    """

    def __init__(self, callback: Callable[[], None], parent: QObject) -> None:
        super().__init__(parent)
        self._callback = callback
        app = QGuiApplication.instance()
        signal = getattr(app, "paletteChanged", None) if app is not None else None
        if signal is not None:
            signal.connect(self._on_palette)

    def _on_palette(self, *_args) -> None:
        QTimer.singleShot(0, self, self._callback)  # after the host finishes; dropped if we are deleted


class GalleryWidget(QWidget):
    itemActivated = Signal(object)        # GalleryItem (double-click / Enter)
    exportLabelChanged = Signal(str)      # label of the main action ("Export", "Ingest", ...)
    themeChanged = Signal(object)         # Theme
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
    drawerChanged = Signal(list)          # list[DrawerEntry], after an edit in the drawer UI
    uiStateChanged = Signal(dict)         # see ui_state(); debounced, only real changes
    _libraryChanged = Signal()            # thread-safe hop for library listeners

    TILE_MIN, TILE_MAX = 90, 420           # tile width range of the size control

    def __init__(self, library: GalleryLibrary | None = None, cache: ThumbnailCache | None = None,
                 parent: QWidget | None = None, show_sidebar: bool = True,
                 show_details: bool = True, tile_aspect: float | None = None,
                 styled: bool = True, theme: "theme_mod.Theme | str | None" = None,
                 style: str | None = None, drawer: bool = False, drawer_key: str = "name",
                 drawer_title: str = "Drawer", drawer_path: str = "path",
                 collapsed: Iterable[str] = (), tile_size: int = 170,
                 size_control: str = "slider", size_label: bool = True,
                 media_badges: bool = True, ui_state: "dict | str | None" = None,
                 name_format: "Callable[[str], object] | None" = None,
                 subtitles: str = "always", show_paths: bool = True,
                 grid_title: str | None = None, drawer_category: str | None = "Assigned",
                 **_ignored) -> None:
        """``theme``: "auto" (default; match Houdini / Nuke / Maya, ILP look
        standalone), "host", "ilp", "sgi", "sgi-dark", "sgi-slate", "fusion",
        "fusion-dark", "fusion-slate" or a :class:`~ilp_gallery.ui.theme.Theme`.
        ``style``: "sgi" or "fusion" to draw an SGI palette in that widget style.
        ``drawer``: show the drawer pane; ``drawer_key`` is the record field that
        identifies its entries ("name" survives new versions, "id" or "path" don't
        have to) and ``drawer_path`` the field stored as each entry's path.
        ``collapsed``: panes to build collapsed, any of "tree", "grid", "details", "drawer".
        ``tile_size``: starting tile width (90 to 420; 90 is the most compact).
        ``size_control``: "slider" or "wheel" (an SGI thumbwheel, like ivview's Dolly);
        ``size_label=False`` drops the "Size" label in front of it.
        ``media_badges=False`` hides the MOV / GIF labels on the tiles.
        ``ui_state``: a dict or JSON string from :meth:`ui_state` to start from.
        ``name_format``: how names are shown on tiles and in the drawer (title, second
        line, category chip), see :mod:`ilp_gallery.ui.names`.
        ``subtitles``: the tiles' second line "always", "never", or "when_needed":
        only on tiles whose title another item shares (e.g. variants of one move).
        ``show_paths=False`` keeps file paths out of the UI (tooltips, the details
        panel's Location and "Open folder"), e.g. when a publishing system owns them.
        ``grid_title``: the tiles pane's tab; default: the data source's ``label`` when
        there's one source, else "Assets".
        ``drawer_category``: label of a pinned category listing the drawer's items
        ("Assigned"); None: no such category."""
        super().__init__(parent)
        self.setObjectName("ILPGallery")
        self._styled_root = styled and parent is None
        if self._styled_root:
            self.setProperty("ilpRoot", True)
            self.setObjectName("ILPGalleryRoot")
        self._theme_spec = (theme, style)
        self.theme = theme_mod.resolve(theme, style)
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
        self._export_label: str | None = None          # set_export_label() override
        self.preview_command = ""                       # e.g. "rv {preview}"
        self._path_filter: tuple = ()
        self._search_ids: list[str] = []

        # -- top bar -----------------------------------------------------
        self.search = SearchField()
        self.search.setPlaceholderText("Search names, tags, IDs…    tag:rock  cat:cliff  meta:tileable  -moss")
        self.search.setMaximumWidth(560)
        if size_control == "wheel":
            from .sgi.thumbwheel import ThumbWheel
            self.size_slider = ThumbWheel(Qt.Horizontal, self.theme)
        elif size_control == "slider":
            self.size_slider = QSlider(Qt.Horizontal)
        else:
            raise ValueError(f"size_control must be 'slider' or 'wheel', not {size_control!r}")
        self.size_slider.setRange(self.TILE_MIN, self.TILE_MAX)
        self.size_slider.setSingleStep(10)
        self.size_slider.setValue(max(self.TILE_MIN, min(self.TILE_MAX, int(tile_size))))
        self.size_slider.setFixedWidth(140)
        self.size_slider.setToolTip("Tile size (Ctrl + wheel)")
        self.toolbar_left = QHBoxLayout()               # add_toolbar_widget(): beside the size control
        self.toolbar_left.setSpacing(6)
        self.toolbar = QHBoxLayout()                    # add_toolbar_button(): right-hand side
        self.toolbar.setSpacing(6)
        self._right_widgets = 0                         # add_toolbar_widget(side="right") count
        self._framed_labels: list[QLabel] = []
        top = QHBoxLayout()
        top.setContentsMargins(8, 6, 8, 6)
        top.setSpacing(10)
        top.addWidget(self.search, 3)
        self.size_label = QLabel("Size")
        self.size_label.setVisible(size_label)
        top.addWidget(self.size_label)
        top.addWidget(self.size_slider)
        top.addLayout(self.toolbar_left)
        top.addStretch(1)
        top.addLayout(self.toolbar)
        # A second row under the search bar for host controls (add_toolbar_widget(row=1));
        # hidden until something is added to it.
        self.row2 = QWidget()
        row2 = QHBoxLayout(self.row2)
        row2.setContentsMargins(8, 0, 8, 6)
        row2.setSpacing(14)
        self.row2_left = QHBoxLayout()
        self.row2_left.setSpacing(14)
        self.row2_right = QHBoxLayout()
        self.row2_right.setSpacing(14)
        row2.addLayout(self.row2_left)
        row2.addStretch(1)
        row2.addLayout(self.row2_right)
        self.row2.hide()

        # -- panes -------------------------------------------------------
        self.tree = CategoryTree()
        self.model = GalleryModel(self.library, self)
        self.model.mime_builder = lambda items: build_mime(items, self._drag_handlers, self.logMessage.emit,
                                                           host=theme_mod.detect_host())
        self.cache.thumbnail_provider = self._source_thumbnail
        self.delegate = TileDelegate(self.cache, self)
        self.delegate.placeholder = default_placeholder()
        self.delegate.media_badges = media_badges
        self._name_format = name_format
        if subtitles not in ("always", "when_needed", "never"):
            raise ValueError(f"subtitles must be 'always', 'when_needed' or 'never', not {subtitles!r}")
        self.delegate.subtitles = subtitles
        self.model.show_paths = show_paths
        self.view = GalleryView(self.delegate)
        self.view.setStyleSheet("")
        self.view.setModel(self.model)
        self._auto_aspect = tile_aspect is None
        if tile_aspect:
            self.delegate.aspect = tile_aspect
        self.view.set_tile_width(self.size_slider.value())
        self.details = DetailPanel(self.cache)
        self.details.setMinimumWidth(300)
        self.drawer = DrawerWidget(key=drawer_key, title=drawer_title, path_key=drawer_path)
        self.details.show_paths = self.drawer.show_paths = show_paths
        self._drawer_index: dict[str, GalleryItem] = {}
        self._drawer_dupes: tuple[str, ...] = ()

        # Panes: categories | tiles | right column (drawer above details). Each one
        # collapses to a tab in the strip on its side (see ui/collapsible.py).
        self.drawer.header.hide()                       # its text is the pane title instead
        self.area = PaneArea()
        self.panes: dict[str, CollapsiblePane] = {}
        for name, content, title, edge, where, size, stretch in (
                ("tree", self.tree, "Categories", "left", "left", 270, 0),
                ("grid", self.view, "Assets", "left", "center", 860, 1),
                ("drawer", self.drawer, drawer_title, "right", "right", 260, 1),
                ("details", self.details, "Preview", "right", "right", 340, 1)):
            self.panes[name] = self.area.add_pane(name, CollapsiblePane(content, title, edge),
                                                  where, size, stretch)
        self.area.set_sizes({"tree": 270, "grid": 860}, column_width=360)
        self._grid_title = grid_title
        self._drawer_category = drawer_category
        self.splitter = self.area.splitter
        self.set_name_format(self._name_format)
        self.drawer.titleChanged.connect(self.panes["drawer"].set_title)
        self.panes["drawer"].set_title(self.drawer.header.text())
        self.panes["details"].collapsedChanged.connect(self._on_details_collapsed)
        self.area.set_enabled("tree", show_sidebar)
        self.area.set_enabled("details", show_details)
        self.area.set_enabled("drawer", drawer)
        for name in collapsed:
            self.set_collapsed(name, True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(top)
        lay.addWidget(self.row2)
        lay.addWidget(self.area, 1)

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
        self.details.previewRequested.connect(self.preview)
        self.details.openFolderRequested.connect(lambda it: _open(_folder(it)))
        self.details.categoryClicked.connect(self.tree.select_path)
        self.details.tagClicked.connect(lambda t: self.search.toggle_token(f"tag:{quote_value(t)}"))
        self.cache.thumbnailReady.connect(self._on_thumb_ready)
        self.drawer.resolver = self._drawer_index.get
        self.drawer.by_id = self.library.get
        self._drawer_placeholder_icon = None
        self.drawer.icon_for = self._drawer_icon
        self.drawer.entriesChanged.connect(self._on_drawer_edited)
        self.drawer.showRequested.connect(lambda it: self.select(it.id))
        self.drawer.currentChanged.connect(self._on_drawer_current)
        self.drawer.runRequested.connect(lambda: self.export_drawer())
        self.exportLabelChanged.connect(lambda _label: self._update_drawer_run())
        self.drawer.logMessage.connect(self.logMessage)
        self.setAcceptDrops(True)   # swallow our own drags (see dragEnterEvent)
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
        self._apply_theme(self.theme)
        self._palette_watcher = _AppPaletteWatcher(self.refresh_theme, self)

        # UI state: report user changes (debounced), restore from ui_state.
        self._pending_category: tuple | None = None
        self._state_quiet = False
        self._sizes_moved = False             # a splitter was dragged since the last report
        self._state_timer = QTimer(self, singleShot=True, interval=400)
        self._state_timer.timeout.connect(self._emit_ui_state)
        changed = self._state_timer.start
        self.search.queryChanged.connect(lambda _t: changed())
        self.tree.pathSelected.connect(self._on_user_category)
        self.size_slider.valueChanged.connect(lambda _v: changed())
        self.area.splitter.splitterMoved.connect(self._on_splitter_moved)
        self.area.column.splitterMoved.connect(self._on_splitter_moved)
        for pane in self.panes.values():
            pane.collapsedChanged.connect(lambda _c: changed())
        if ui_state:
            self.set_ui_state(ui_state)
        self._last_state = self.ui_state()

    # -- public API ------------------------------------------------------
    # ---------------------------------------------------------------- look
    def set_theme(self, theme: "theme_mod.Theme | str | None", style: str | None = None) -> None:
        """Switch the look live: a theme name or Theme, optionally with a widget
        ``style`` ("sgi" or "fusion") for the SGI palettes.

        "auto" / "host" keep following the host: the colours are worked out again
        when the host's palette changes and whenever the gallery is shown.
        """
        self._theme_spec = (theme, style)
        self._apply_theme(theme_mod.resolve(theme, style))

    def refresh_theme(self) -> None:
        """Re-read the host's colours (no-op for fixed themes or if nothing changed)."""
        if isinstance(self._theme_spec[0], theme_mod.Theme):
            return
        new = theme_mod.resolve(*self._theme_spec)
        if new != self.theme:
            self._apply_theme(new)

    def _apply_theme(self, t: "theme_mod.Theme") -> None:
        old = getattr(self, "_polished_theme", None)
        if old is not None and type(old) is not type(t):
            old.unpolish(self)
            self._polished_theme = None
        self.theme = t
        if self._styled_root:
            self.setPalette(self.theme.qpalette())
            self.setStyleSheet(self.theme.stylesheet())
            self.theme.polish(self)
            self._polished_theme = self.theme
        self.delegate.set_theme(self.theme)
        self.tree.set_theme(self.theme)
        self.details.set_theme(self.theme)
        self.drawer.set_colors(self.theme.c("text"), self.theme.c("muted"), self.theme.c("accent"))
        self.area.set_theme(self.theme)
        self._drawer_placeholder_icon = None              # rebuilt in the new colours
        if hasattr(self, "drawer"):
            self.drawer.refresh_icons()
        for label in self._framed_labels:
            self._style_framed(label)
        if hasattr(self.size_slider, "set_theme"):          # the thumbwheel
            self.size_slider.set_theme(self.theme)
        self.view.viewport().update()
        self.themeChanged.emit(self.theme)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh_theme()  # e.g. the host's scheme changed while the pane was hidden

    # ---------------------------------------------------------------- data
    def add_source(self, source: DataSource, background: bool = True) -> None:
        """Add a data source. With ``background`` the (possibly slow) load runs off the GUI thread;
        ``sourceLoaded`` / ``sourceFailed`` report the outcome."""
        self.library.register_source(source)
        self._update_grid_title()
        self._reload(source, background)

    def set_grid_title(self, title: str | None) -> None:
        """The tiles pane's tab text; None: from the data sources (see ``grid_title``)."""
        self._grid_title = title
        self._update_grid_title()

    def _update_grid_title(self) -> None:
        if self._grid_title:
            title = self._grid_title
        else:
            labels = [s.label or s.name for s in self.library.sources]
            title = labels[0] if len(labels) == 1 else "Assets"
        self.panes["grid"].set_title(title)

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
        self._update_grid_title()
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
        tb = format_traceback(exc)
        self.logMessage.emit(f"ERROR loading {src.label}: {msg}\n{tb}")
        log.error("loading %s failed:\n%s", src.label, tb)      # also to the terminal / host console
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
                tb = format_traceback(error)
                self.logMessage.emit(f"{src.label}: poll failed: {type(error).__name__}: {error}\n{tb}")
                log.error("polling %s failed:\n%s", src.label, tb)
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
            self.exportLabelChanged.emit(self.export_label())
        self.set_export_enabled(True)
        self._show_current(self.view.currentIndex(), force=True)
        self._update_drawer_run()
        return ex

    def unregister_exporter(self, exporter: Exporter) -> None:
        if exporter in self._exporters:
            self._exporters.remove(exporter)
        if self._default_exporter is exporter:
            self._default_exporter = self._exporters[0] if self._exporters else None
        self._update_drawer_run()

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
                              sources: Iterable[str] | None = None,
                              hosts: Iterable[str] | None = None) -> DragHandler:
        """Add data to drags out of the gallery (see ``ilp_gallery.ui.actions``).

        ``hosts``: only use this handler inside these applications ("houdini", "nuke",
        "maya", "standalone"), so one setup can register a different drop per DCC.
        """
        h = DragHandler(fn, replace_default, tuple(sources) if sources is not None else None,
                        tuple(x.lower() for x in hosts) if hosts is not None else None)
        self._drag_handlers.append(h)
        return h

    def unregister_drag_handler(self, handler: DragHandler) -> None:
        if handler in self._drag_handlers:
            self._drag_handlers.remove(handler)

    # ---------------------------------------------------------------- drawer
    def has_drawer(self) -> bool:
        """True when the drawer is part of the gallery, collapsed or not."""
        return self.panes["drawer"].is_enabled()

    def set_drawer_visible(self, on: bool) -> None:
        self.area.set_enabled("drawer", on)
        if on:
            self._update_drawer_index()
        else:
            self.tree.remove_special(self.DRAWER_PATH)

    # ---------------------------------------------------------------- panes
    def set_collapsed(self, pane: str, collapsed: bool = True) -> None:
        """Collapse ("tree", "grid", "details", "drawer") to a vertical tab, or expand it."""
        if pane not in self.panes:
            raise ValueError(f"unknown pane {pane!r}: use one of {', '.join(self.panes)}")
        self.area.set_collapsed(pane, collapsed)

    def is_collapsed(self, pane: str) -> bool:
        return self.panes[pane].is_collapsed()

    def set_drawer(self, entries: Iterable) -> None:
        """Load the drawer: DrawerEntry objects, ``{"key", "path", "enabled"}`` dicts,
        ``(key, path, enabled)`` tuples or bare keys. Silent: no ``drawerChanged``."""
        self.drawer.set_entries(entries)
        self._update_marks()

    def drawer_entries(self) -> list[DrawerEntry]:
        return self.drawer.entries()

    def add_to_drawer(self, items: list[GalleryItem] | None = None) -> int:
        """Add ``items`` (default: the selection). Emits ``drawerChanged`` if any were added."""
        return self.drawer.add_items(items if items is not None else self.selected_items())

    def export_drawer(self, exporter: Exporter | None = None, enabled_only: bool = True) -> bool:
        """Run ``exporter`` (default: the default one) on the drawer's items, in drawer order.
        Missing entries are skipped, and so are disabled ones unless ``enabled_only=False``."""
        items = self.drawer_items(enabled_only=enabled_only)
        if not items:
            self.logMessage.emit(f"{self.drawer.title}: nothing to run on.")
            return False
        return self.export(items, exporter)

    def drawer_items(self, enabled_only: bool = False) -> list[GalleryItem]:
        """Library items behind the drawer entries, in drawer order (missing ones skipped)."""
        out = []
        for e in self.drawer.entries():
            it = self._drawer_index.get(e.key)
            if it is not None and (e.enabled or not enabled_only):
                out.append(it)
        return out

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
        items = (i.data(ItemRole) for i in rows)
        return [it for it in items if it is not None]      # rows of items just removed: None

    def current_item(self) -> GalleryItem | None:
        return self.details.item

    def visible_items(self) -> list[GalleryItem]:
        return [it for it in (self.library.get(i) for i in self.model.ids()) if it is not None]

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

    def add_toolbar_widget(self, widget: QWidget, stretch: int = 0, side: str = "left",
                           row: int = 0) -> QWidget:
        """Put ``widget`` in the top bar: ``side="left"`` beside the tile size control,
        ``"right"`` right-aligned (before any toolbar buttons). ``row=1`` uses a second
        row under the search bar instead, left to right in the order added (e.g. a
        host node's own controls)."""
        if row == 1:
            if side not in ("left", "right"):
                raise ValueError(f"side must be 'left' or 'right', not {side!r}")
            (self.row2_left if side == "left" else self.row2_right).addWidget(widget, stretch)
            self.row2.show()
            return widget
        if row != 0:
            raise ValueError(f"row must be 0 or 1, not {row!r}")
        if side == "left":
            self.toolbar_left.addWidget(widget, stretch)
        elif side == "right":
            self.toolbar.insertWidget(self._right_widgets, widget, stretch)
            self._right_widgets += 1
        else:
            raise ValueError(f"side must be 'left' or 'right', not {side!r}")
        return widget

    def set_name_format(self, fmt) -> None:
        """How names are shown on tiles and in the drawer; None: the plain name."""
        self._name_format = fmt
        self.delegate.name_format = self.model.name_format = self.details.name_format = fmt
        self.drawer.set_name_format(fmt)
        self._update_shared_titles()
        if self.details.item is not None:
            self.details.set_item(self.details.item)

    def set_subtitles(self, mode: str) -> None:
        """The tiles' second line: "always", "never" or "when_needed"."""
        if mode not in ("always", "when_needed", "never"):
            raise ValueError(f"subtitles must be 'always', 'when_needed' or 'never', not {mode!r}")
        self.delegate.subtitles = mode
        self._update_shared_titles()

    def _update_shared_titles(self) -> None:
        """Titles used by more than one item in the library (for "when_needed")."""
        if self.delegate.subtitles == "when_needed":
            counts = Counter(format_name(self._name_format, it).title for it in self.library)
            self.delegate.shared_titles = {t for t, n in counts.items() if n > 1}
        else:
            self.delegate.shared_titles = set()
        self.view.viewport().update()

    def set_drawer_current(self, key: str | None) -> None:
        """Mark the drawer entry ``key`` as current (bold, tinted), e.g. a host node's
        current clip; None clears it."""
        self.drawer.set_current_key(key)

    def add_drawer_action(self, label: str, callback: Callable[[list[DrawerEntry]], None],
                          single: bool = False) -> None:
        """An entry at the top of the drawer rows' context menu; see DrawerWidget.add_action."""
        self.drawer.add_action(label, callback, single)

    def add_toolbar_label(self, text: str = "", side: str = "right", framed: bool = True) -> QLabel:
        """A label in the top bar, e.g. the host node's path. ``framed`` draws a border
        around it in the theme's colours so it stands out from the controls."""
        label = QLabel(text)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)    # the path can be copied
        if framed:
            self._framed_labels.append(label)
            self._style_framed(label)
        return self.add_toolbar_widget(label, side=side)

    def _style_framed(self, label: QLabel) -> None:
        t = self.theme
        label.setStyleSheet(f"QLabel {{ color: {t.c('text').name()}; background: {t.c('tile').name()};"
                            f" border: 1px solid {t.c('border').name()}; border-radius: 3px;"
                            f" padding: 2px 8px; }}")

    def add_toolbar_button(self, text: str, callback: Callable[[], None], tooltip: str = "",
                           icon: "QIcon | str | None" = None) -> QPushButton:
        """A flat button on the right of the top bar. ``icon``: a QIcon, or "reload" for
        the platform's refresh icon; with an icon, ``text`` can be "" (icon only)."""
        b = QPushButton(text, objectName="flat")
        if isinstance(icon, str):
            names = {"reload": QStyle.SP_BrowserReload, "refresh": QStyle.SP_BrowserReload}
            if icon not in names:
                raise ValueError(f"unknown icon name {icon!r}: use a QIcon or one of {', '.join(names)}")
            icon = self.style().standardIcon(names[icon])
        if icon is not None:
            b.setIcon(icon)
            if not text:
                b.setFixedSize(26, 24)
        b.setToolTip(tooltip)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(callback)
        self.toolbar.addWidget(b)
        return b

    # ---------------------------------------------------------------- main action label
    def export_label(self) -> str:
        """Label of the main action: the override, else the default exporter's."""
        d = self._default_exporter
        return self._export_label or (d.label if d is not None else "Export")

    def set_export_label(self, label: str | None) -> None:
        """Rename the main action in the UI ("Ingest", "Assign"...); None restores
        the default exporter's own label."""
        self._export_label = label or None
        self._show_current(self.view.currentIndex(), force=True)
        self.exportLabelChanged.emit(self.export_label())

    # ---------------------------------------------------------------- placeholder
    def set_placeholder(self, icon: "QIcon | QPixmap | str | None") -> None:
        """Icon for items without a thumbnail: a QIcon, QPixmap or image path;
        None restores the default."""
        if icon is None:
            icon = default_placeholder()
        elif isinstance(icon, str):
            icon = QIcon(os.path.expandvars(os.path.expanduser(icon)))
        elif isinstance(icon, QPixmap):
            icon = QIcon(icon)
        self.delegate.placeholder = icon
        self.view.viewport().update()

    # ---------------------------------------------------------------- preview
    def set_preview_command(self, template: str | None) -> None:
        """Command that opens an item's preview movie, e.g. ``"rv {preview}"``.

        Placeholders as for the export command, plus ``{preview}`` (the preview
        movie, or the item itself if it's a movie). Use ``{previews}`` to open all
        selected previews in one call (``"rv {previews}"``). Runs without a shell,
        detached, so the player doesn't block the gallery.
        """
        self.preview_command = (template or "").strip()
        self.details.can_preview = self.can_preview
        self.details.set_preview_enabled(bool(self.preview_command))

    def can_preview(self, item: GalleryItem) -> bool:
        """Whether the preview command has what it needs for ``item``: something to play
        for ``{preview}`` / ``{previews}``, and the metadata keys it uses (``{movie}``)."""
        if not self.preview_command or item is None:
            return False
        for key in template_keys(self.preview_command):
            if key in ("preview", "previews"):
                if item.motion is None and not item.preview:
                    return False
            elif key not in BUILTIN_FIELDS and not item.metadata.get(key):
                return False
        return True

    def preview(self, items: list[GalleryItem] | None = None) -> int:
        """Open the preview of ``items`` (default: the selection). Returns how many
        commands were started."""
        items = [it for it in (items if items is not None else self.selected_items()) if self.can_preview(it)]
        if not items or not self.preview_command:
            if not self.preview_command:
                self.logMessage.emit("No preview command set (Settings, or set_preview_command()).")
            return 0
        template = self.preview_command
        if "{previews}" in template:
            joined = " ".join(shlex.quote((it.motion or (it.preview, ""))[0]) for it in items)
            cmds = [format_command(template.replace("{previews}", joined.replace("{", "{{").replace("}", "}}")),
                                   items[0])]
        else:
            cmds = [format_command(template, it) for it in items]
        started = 0
        for cmd in cmds:
            try:
                subprocess.Popen(shlex.split(cmd), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
                self.logMessage.emit(f"Preview: {cmd}")
                started += 1
            except (OSError, ValueError) as exc:
                self.logMessage.emit(f"ERROR preview: {cmd}: {exc}")
        return started

    def set_export_enabled(self, on: bool) -> None:
        """Show Export / Copy command (automatic once an exporter is registered)."""
        self._export_enabled = on
        self.details.set_export_enabled(on)

    def set_sidebar_visible(self, on: bool) -> None:
        """Show or remove the categories pane (``set_collapsed`` folds it instead)."""
        self.area.set_enabled("tree", on)
        if on:
            self.area.set_collapsed("tree", False)

    def set_details_visible(self, on: bool) -> None:
        """Show or remove the details pane (``set_collapsed`` folds it instead)."""
        self.area.set_enabled("details", on)
        if on:
            self.area.set_collapsed("details", False)
            self._show_current(self.view.currentIndex(), force=True)
        else:
            self.details.player.stop()

    def _on_details_collapsed(self, collapsed: bool) -> None:
        if collapsed:
            self.details.player.stop()
        else:
            self._show_current(self.view.currentIndex(), force=True)

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

    # ---------------------------------------------------------------- UI state
    UI_STATE_VERSION = 1

    def ui_state(self) -> dict:
        """What a user set up, as plain JSON-able data: the search text, the selected
        category, collapsed panes, tile size and pane sizes. A host can store it (a
        node parameter, a file) and give it back with :meth:`set_ui_state`."""
        sizes = {}
        for name, pane in self.panes.items():
            sizes[name] = self._pane_size(pane)
        sizes["column"] = self._pane_size(self.area.column)
        category = self._pending_category if self._pending_category is not None else self.tree.current_path()
        return {
            "version": self.UI_STATE_VERSION,
            "search": self.search.text(),
            "category": list(category),
            "collapsed": [n for n, p in self.panes.items() if p.is_collapsed()],
            "tile_size": self.size_slider.value(),
            "sizes": sizes,
        }

    def set_ui_state(self, state: "dict | str") -> None:
        """Restore :meth:`ui_state` output (a dict or its JSON). Missing keys are left
        as they are; a category that isn't loaded yet is selected once it appears.
        Doesn't emit ``uiStateChanged``."""
        if isinstance(state, str):
            state = json.loads(state) if state.strip() else {}
        if not isinstance(state, dict):
            raise ValueError("ui state must be a dict or a JSON object")
        self._state_quiet = True
        try:
            if "collapsed" in state:
                folded = set(state["collapsed"] or ())
                for name in self.panes:
                    if self.panes[name].is_enabled():
                        self.area.set_collapsed(name, name in folded)
            sizes = state.get("sizes") or {}
            for name, pane in self.panes.items():          # after collapsing, which records
                if isinstance(sizes.get(name), int) and sizes[name] > 0:   # the current sizes
                    self.area._saved[id(pane)] = sizes[name]
            if isinstance(sizes.get("column"), int) and sizes["column"] > 0:
                self.area._saved[id(self.area.column)] = sizes["column"]
            if sizes:
                self._apply_sizes()
            if isinstance(state.get("tile_size"), int):
                self.size_slider.setValue(state["tile_size"])
            if "search" in state:
                self.search.blockSignals(True)              # no debounced echo of the restore
                self.search.setText(str(state["search"] or ""))
                self.search.blockSignals(False)
                self._run_query(self.search.text())
            if "category" in state:
                self._pending_category = tuple(state["category"] or ())
                self._apply_pending_category()
        finally:
            self._state_quiet = False
            self._state_timer.stop()
            self._sizes_moved = False
            self._last_state = self.ui_state()

    def save_ui_state(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.ui_state(), f, indent=2)

    def load_ui_state(self, path: str) -> None:
        with open(path, encoding="utf-8") as f:
            self.set_ui_state(json.load(f))

    def _pane_size(self, w: QWidget) -> int:
        s = w.parentWidget()
        if isinstance(s, QSplitter) and not w.isHidden():
            size = s.sizes()[s.indexOf(w)]
            if size > 0:
                return size
        return int(self.area._saved.get(id(w), 0))

    def _apply_sizes(self) -> None:
        for s in (self.area.splitter, self.area.column):
            sizes = [0 if s.widget(i).isHidden() else self.area._saved.get(id(s.widget(i)), 0) or s.sizes()[i]
                     for i in range(s.count())]
            if any(sizes):
                s.setSizes(sizes)

    def _apply_pending_category(self) -> None:
        path = self._pending_category
        if path is None or path not in self.tree._rows:
            return                                          # not loaded yet: try after the next load
        self._pending_category = None
        self.tree.blockSignals(True)
        self.tree.select_path(path)
        self.tree.blockSignals(False)
        self._path_filter = path
        self._apply_filters()

    def _on_user_category(self, _path) -> None:
        self._pending_category = None                       # the user chose: forget the restore
        self._state_timer.start()

    def _on_splitter_moved(self, *_args) -> None:
        self._sizes_moved = True
        self._state_timer.start()

    @staticmethod
    def _without_sizes(state: dict) -> dict:
        return {k: v for k, v in state.items() if k != "sizes"}

    def _emit_ui_state(self) -> None:
        """Report a change. Pane sizes alone count only when a splitter was dragged:
        the layout also resizes panes by itself (window resizes, settling after a
        restore), and that isn't something the user did."""
        if self._state_quiet:
            return
        state = self.ui_state()
        moved, self._sizes_moved = self._sizes_moved, False
        if moved or self._without_sizes(state) != self._without_sizes(self._last_state):
            self._last_state = state
            self.uiStateChanged.emit(state)

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
        self._update_drawer_index()
        if hasattr(self, "_name_format"):
            self._update_shared_titles()
        self._run_query(self.search.text())
        if getattr(self, "_pending_category", None) is not None:
            self._apply_pending_category()
        self._refresh_details()

    def _refresh_details(self) -> None:
        """After the library changed (a reload, a poll): the details panel shows the
        item it had, as it is now, e.g. a new version's rows. Unchanged items are left
        alone so a playing preview isn't restarted; a removed one clears the panel."""
        shown = self.details.item
        if shown is None:
            return
        current = self.library.get(shown.id)
        if current is None:
            self.details.set_item(None)
        elif current != shown:
            self.details.set_item(current)
    def _update_drawer_index(self) -> None:
        if not self.has_drawer():
            return
        index, dupes = build_key_index(self.library, self.drawer.key)
        self._drawer_index.clear()
        self._drawer_index.update(index)   # in place: the drawer holds index.get
        if dupes and tuple(dupes) != self._drawer_dupes:
            shown = ", ".join(dupes[:10]) + (" …" if len(dupes) > 10 else "")
            self.logMessage.emit(f"Drawer: {len(dupes)} {self.drawer.key} value(s) are used by more "
                                 f"than one item, the last loaded wins: {shown}")
        self._drawer_dupes = tuple(dupes)
        self.drawer.refresh()
        self._update_marks()

    DRAWER_PATH = ("@drawer",)                     # the pinned "Assigned" category

    def _update_drawer_category(self, members: set[str]) -> None:
        if not getattr(self, "_drawer_category", None) or not self.has_drawer():
            return
        before = self.tree.ids.get(self.DRAWER_PATH)
        self.tree.set_special(self.DRAWER_PATH, self._drawer_category, members)
        if self._path_filter == self.DRAWER_PATH and before != members:
            self._apply_filters()                  # showing it: add / drop the tiles now

    def _update_marks(self) -> None:
        """Tile badges: {item id: (enabled, state)} for items in the drawer."""
        marks = {}
        for e in self.drawer.entries():
            it = self._drawer_index.get(e.key)
            if it is not None:
                marks[it.id] = (e.enabled, entry_state(e, it, self.drawer.path_key))
        if marks != self.delegate.marks:
            self.delegate.marks = marks
            self.view.viewport().update()
        self._update_drawer_category(set(marks))
        self._update_drawer_run()

    def _update_drawer_run(self) -> None:
        """The drawer's run button: the main action on the drawer, when there is one."""
        if not hasattr(self, "drawer"):
            return
        has = bool(self._exporters or self.command_template)
        self.drawer.run_btn.setVisible(has)
        if has:
            n = len(self.drawer_items(enabled_only=True))
            self.drawer.run_btn.setText(f"{self.export_label()} ({n})")
            self.drawer.run_btn.setEnabled(n > 0)

    def _on_drawer_current(self, item: GalleryItem | None) -> None:
        """A row clicked in the drawer plays in the details panel (when it's open)."""
        if item is not None and self.details.isVisible():
            self.details.set_item(item)

    def _on_drawer_edited(self, entries: list) -> None:
        self._update_marks()
        self.drawerChanged.emit(entries)

    def _drawer_icon(self, item: GalleryItem | None):
        """A drawer row's icon: the thumbnail; the tiles' "no preview" icon when the
        item has none or isn't in the library; None (blank) while it's loading."""
        if item is None or self.cache.failed(item.id):
            return self._drawer_placeholder()
        pix = self.cache.poster(item)
        return QIcon(pix) if pix is not None and not pix.isNull() else None

    def _drawer_placeholder(self) -> QIcon | None:
        if self._drawer_placeholder_icon is None and self.delegate.placeholder is not None:
            pix = QPixmap(48, 27)
            pix.fill(self.theme.c("image_bg"))
            p = QPainter(pix)
            self.delegate.placeholder.paint(p, QRect(15, 4, 19, 19))
            p.end()
            self._drawer_placeholder_icon = QIcon(pix)
        return self._drawer_placeholder_icon

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
            label = self._export_label or (d.label if d in exs else (exs[0].label if exs else "Export"))
            self.details.set_export_label(label, more=len(exs) > 1)
        self.details.set_item(item)
        self.currentChanged.emit(item)

    def _on_thumb_ready(self, item_id: str) -> None:       # also sent when one fails
        self.view.refresh_item(self.model.index_of(item_id))
        if item_id in self.delegate.marks:
            self.drawer.refresh_icons()
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
        self.theme.polish_menu(menu)
        if self.has_drawer():
            title = self.drawer.title.lower()
            menu.addAction(f"Add to {title} ({n})" if n > 1 else f"Add to {title}",
                           lambda: self.add_to_drawer(items))
            menu.addSeparator()
        for ex in self.exporters(items):
            menu.addAction(f"{ex.label} ({n})" if n > 1 else ex.label,
                           lambda ex=ex: self.export(items, ex))
        for label, cb in self._actions:
            menu.addAction(label, lambda cb=cb, items=items: cb(items))
        moving = [it for it in items if self.can_preview(it)]
        if self.preview_command and moving:
            menu.addAction(f"Preview ({len(moving)})" if len(moving) > 1 else "Preview",
                           lambda: self.preview(moving))
        menu.addSeparator()
        if self._export_enabled:
            menu.addAction("Copy command", lambda: self.copy_command(items))
        menu.addAction("Copy path" + ("s" if n > 1 else ""), self.copy_paths)
        menu.addAction("Copy ID" + ("s" if n > 1 else ""),
                       lambda: QGuiApplication.clipboard().setText("\n".join(i.id for i in items)))
        menu.addAction("Open folder", lambda: _open(_folder(items[0])))
        if items[0].tags:
            sub = menu.addMenu("Filter by tag")
            self.theme.polish_menu(sub)
            for t in items[0].tags[:30]:
                sub.addAction(t, lambda t=t: self.search.toggle_token(f"tag:{quote_value(t)}"))
        menu.addSeparator()
        menu.addAction("Refresh thumbnail", lambda: self._refresh_thumbs(items))
        menu.exec(self.view.viewport().mapToGlobal(pos))

    def _export_menu(self, items: list[GalleryItem], pos) -> None:
        menu = QMenu(self)
        self.theme.polish_menu(menu)
        for ex in self.exporters(items):
            menu.addAction(ex.label, lambda ex=ex: self.export(items, ex))
        menu.exec(pos)

    def _refresh_thumbs(self, items: list[GalleryItem]) -> None:
        for it in items:
            self.cache.invalidate(it.id)
            self.view.refresh_item(self.model.index_of(it.id))

    # Our own tiles dragged and dropped back onto the gallery (anywhere but the
    # drawer) are swallowed. Otherwise the drop travels up to the host window and
    # a DCC may try to open the file URL (Houdini: "Save and open" the scene).
    # Drawer rows dragged out of the drawer onto the gallery are removed from it.
    def _own_drag(self, event) -> bool:
        return event.source() is self.view and event.mimeData().hasFormat(MIME_ITEMS)

    def _drawer_drag_out(self, event) -> bool:
        if event.source() is not self.drawer.list or not event.mimeData().hasFormat(MIME_DRAWER):
            return False
        pos = self.drawer.mapFrom(self, event.position().toPoint())
        return not self.drawer.rect().contains(pos)

    def dragEnterEvent(self, event) -> None:
        if self._own_drag(event) or event.source() is self.drawer.list:
            self.dragMoveEvent(event)
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if self._drawer_drag_out(event):
            event.setDropAction(Qt.CopyAction)   # not Move: the drawer rebuilds its rows itself
            event.accept()
        elif self._own_drag(event) or event.source() is self.drawer.list:
            event.setDropAction(Qt.IgnoreAction)
            event.accept()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if self._drawer_drag_out(event):
            event.setDropAction(Qt.CopyAction)
            event.accept()
            try:
                keys = json.loads(bytes(event.mimeData().data(MIME_DRAWER)).decode("utf-8"))
            except ValueError:
                return
            # After the drag finishes: if Qt also removes the dragged rows (a Move),
            # the rebuild that follows puts the list back in line with the entries.
            keys = [str(k) for k in keys]
            QTimer.singleShot(0, lambda: self.drawer.remove_keys(keys))
        elif self._own_drag(event) or event.source() is self.drawer.list:
            event.setDropAction(Qt.IgnoreAction)
            event.accept()
        else:
            super().dropEvent(event)

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
