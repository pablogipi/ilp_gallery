"""GalleryBrowser: the full tool window around GalleryWidget.

View menu, Export selected / Rescan library / Settings, an Export log
dock and a status bar. Works standalone or parented inside a DCC.
"""

from __future__ import annotations

import json
import os
import time

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QDockWidget, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from ..core.item import GalleryItem
from . import theme as theme_mod
from .widget import GalleryWidget

ORG, APP = "ILPvfx", "ILPGallery"


def settings() -> QSettings:
    return QSettings(ORG, APP)


def setting(key: str, default: str = "") -> str:
    env = {"export/command": "ILP_GALLERY_EXPORT_CMD", "megascans/root": "ILP_MEGASCANS_ROOT",
           "library/sources": "ILP_GALLERY_LIBRARY"}.get(key)
    value = settings().value(key, "")
    return str(value or (os.environ.get(env, "") if env else "") or default)


class SettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Gallery settings")
        self.setMinimumWidth(620)
        self.cmd = QLineEdit(setting("export/command"))
        self.cmd.setPlaceholderText("e.g. stockpile export --asset {asset_dir}")
        self.sources = QLineEdit(setting("library/sources"))
        self.sources.setPlaceholderText("library JSON files or folders, ':' separated")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        src_row = QHBoxLayout()
        src_row.addWidget(self.sources, 1)
        src_row.addWidget(browse)
        from ..sources.megascans import DEFAULT_ROOT
        self.ms_root = QLineEdit(setting("megascans/root", DEFAULT_ROOT))

        help_ = QLabel("Placeholders: {id} {name} {asset_dir} {json} {path} {type_label} {thumbnail}<br>"
                       "Runs once per asset without a shell; output goes to the Export log.",
                       objectName="muted")
        help_.setWordWrap(True)
        form = QFormLayout()
        form.addRow("Export command", self.cmd)
        form.addRow("", help_)
        form.addRow("Default library", src_row)
        form.addRow("Megascans root", self.ms_root)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(buttons)

    def _browse(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Library JSON", "", "JSON (*.json)")
        if f:
            self.sources.setText(f)

    def accept(self) -> None:
        s = settings()
        s.setValue("export/command", self.cmd.text().strip())
        s.setValue("library/sources", self.sources.text().strip())
        s.setValue("megascans/root", self.ms_root.text().strip())
        super().accept()


class LogPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.status = QLabel("Idle", objectName="muted")
        self.stop_btn = QPushButton("Stop export")
        self.stop_btn.setEnabled(False)
        self.clear_btn = QPushButton("Clear log")
        self.text = QPlainTextEdit(readOnly=True)
        self.text.setMaximumBlockCount(20000)
        top = QHBoxLayout()
        top.addWidget(self.status, 1)
        top.addWidget(self.stop_btn)
        top.addWidget(self.clear_btn)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 8)
        lay.addLayout(top)
        lay.addWidget(self.text, 1)
        self.clear_btn.clicked.connect(self.text.clear)

    def log(self, line: str) -> None:
        self.text.appendPlainText(line)


class GalleryBrowser(QMainWindow):
    def __init__(self, parent: QWidget | None = None, theme: "theme_mod.Theme | str | None" = None,
                 style: str | None = None, **widget_kwargs) -> None:
        """``theme``: "auto" (default; match Houdini / Nuke / Maya, ILP look
        standalone), "host", "ilp", "sgi", "sgi-dark", "sgi-slate", "fusion",
        "fusion-dark", "fusion-slate" or a :class:`~ilp_gallery.ui.theme.Theme`.
        ``style``: "sgi" or "fusion" to draw an SGI palette in that widget style."""
        super().__init__(parent)
        self.setObjectName("ILPGalleryRoot")
        self.setWindowTitle("ILP Gallery")
        self.setDockOptions(QMainWindow.AnimatedDocks)

        self.gallery = GalleryWidget(parent=self, styled=False, theme=theme, style=style, **widget_kwargs)
        self.setCentralWidget(self.gallery)
        self._restyle(self.theme)
        self.gallery.themeChanged.connect(self._restyle)
        self.gallery.set_export_enabled(True)
        self.gallery.command_template = setting("export/command")

        # toolbar buttons (right of the search bar)
        self.export_btn = self.gallery.add_toolbar_button("Export selected", self.export_selected)
        self.rescan_btn = self.gallery.add_toolbar_button("Rescan library", self.rescan)
        self.gallery.add_toolbar_button("Settings", self.open_settings)

        # export log dock
        self.log_panel = LogPanel()
        self.log_dock = QDockWidget("Export log", self)
        self.log_dock.setObjectName("exportLog")
        self.log_dock.setWidget(self.log_panel)
        self.log_dock.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log_dock)
        self.resizeDocks([self.log_dock], [170], Qt.Vertical)
        self.log_panel.stop_btn.clicked.connect(self.stop_task)

        # status bar
        self.count_lbl = QLabel("")
        self.statusBar().addWidget(self.count_lbl)
        self.gallery.countChanged.connect(self._on_count)
        self._on_count(len(self.gallery.model.ids()), len(self.gallery.library))

        g = self.gallery
        g.logMessage.connect(self.log)
        g.taskStarted.connect(self._on_task_started)
        g.taskProgress.connect(self._on_task_progress)
        g.taskFinished.connect(self._on_task_finished)
        g.sourceLoaded.connect(self._on_source_loaded)
        self._status = ""
        self._upgraded: set[str] = set()
        self._build_menu()
        self.resize(1500, 950)

        if self.gallery.command_template:
            self.log(f"Export command: {self.gallery.command_template}")

    # -- API passthrough (so tools can treat the window like the widget) ------
    def add_source(self, source, background: bool = True) -> None:
        self.gallery.add_source(source, background=background)

    def register_exporter(self, *args, **kwargs):
        return self.gallery.register_exporter(*args, **kwargs)

    def register_drag_handler(self, *args, **kwargs):
        return self.gallery.register_drag_handler(*args, **kwargs)

    def add_action(self, *args, **kwargs):
        return self.gallery.add_action(*args, **kwargs)

    # -- loading -----------------------------------------------------------
    def load(self, source: str, background: bool = False, **kwargs) -> int:
        """Load a library path (JSON, .json.gz, folder). Outdated Megascans caches upgrade themselves."""
        n = self.gallery.load(source, background=background, **kwargs)
        return n

    auto_upgrade = True

    def _on_source_loaded(self, src, n: int) -> None:
        self.log(f"Loaded {n:,} assets from {src.label}")
        outdated = getattr(src, "outdated", None)
        if self.auto_upgrade and outdated and src.can_rebuild():
            items = [it for it in self.gallery.library if it.source == src.name]
            k = outdated(items)
            if k and src.name not in self._upgraded:
                self._upgraded.add(src.name)
                self.log(f"{k:,} assets were scanned by an older version of the importer: "
                         "rescanning to add categories and metadata (one time, runs in the background).")
                QTimer.singleShot(0, lambda: self.rescan([src]))

    # -- menu --------------------------------------------------------------
    def _build_menu(self) -> None:
        view = self.menuBar().addMenu("View")

        def toggle(label, visible, fn, shortcut=None):
            a = QAction(label, self, checkable=True, checked=visible)
            a.toggled.connect(fn)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            view.addAction(a)
            return a

        g = self.gallery
        self.act_tree = toggle("Categories", g.tree.isVisibleTo(g), g.set_sidebar_visible, "Ctrl+1")
        self.act_details = toggle("Details", g.details.isVisibleTo(g), g.set_details_visible, "Ctrl+2")
        self.act_log = self.log_dock.toggleViewAction()
        self.act_log.setText("Export log")
        self.act_log.setShortcut(QKeySequence("Ctrl+3"))
        view.addAction(self.act_log)
        view.addSeparator()
        for label, w in (("Small tiles", 120), ("Medium tiles", 170), ("Large tiles", 260)):
            view.addAction(label, lambda w=w: g.set_tile_width(w))
        view.addSeparator()
        a = view.addAction("Reload library", self.reload)
        a.setShortcut(QKeySequence("F5"))

    # -- actions -----------------------------------------------------------
    def log(self, line: str) -> None:
        self.log_panel.log(line)

    def export_selected(self) -> None:
        self.gallery.export(self.gallery.selected_items())

    def export(self, items: list[GalleryItem], exporter=None) -> bool:
        return self.gallery.export(items, exporter)

    def rescan(self, sources=None) -> None:
        """Rebuild sources that support it (e.g. Megascans rescans disk), then reload them."""
        sources = [s for s in (sources or self.gallery.library.sources)]
        rebuildable = [s for s in sources if s.can_rebuild()]
        if not rebuildable:
            self.log("No source can rescan (plain JSON / in-memory): reloading instead.")
            self.gallery.reload()
            return

        def job(log, cancelled, thread) -> bool:
            for src in rebuildable:
                log(f"Rescanning {src.label}…")
                src.rebuild(progress=lambda a, b: thread.progress.emit(a, b), log=log)
            return True

        def done(ok):
            if ok:
                for src in rebuildable:
                    self.gallery.reload_source(src)

        self.gallery.run_task(job, "Rescanning library…", stoppable=False, on_done=done)

    def reload(self) -> None:
        self.gallery.reload()

    @property
    def theme(self) -> "theme_mod.Theme":
        return self.gallery.theme

    def _restyle(self, t: "theme_mod.Theme") -> None:
        old = getattr(self, "_polished_theme", None)
        if old is not None and type(old) is not type(t):
            old.unpolish(self)
        self.setPalette(t.qpalette())
        self.setStyleSheet(t.stylesheet())
        t.polish(self)                  # e.g. the SGI theme's proxy style and menu marks
        self._polished_theme = t

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.theme.polish(self)         # widgets created since the theme was applied

    def set_theme(self, theme: "theme_mod.Theme | str | None", style: str | None = None) -> None:
        """Switch the look live (see ``__init__``)."""
        self.gallery.set_theme(theme, style)  # themeChanged restyles the window

    def open_settings(self) -> None:
        dlg = SettingsDialog(self)
        dlg.setStyleSheet(self.theme.stylesheet())
        self.theme.polish(dlg)
        if dlg.exec():
            self.gallery.command_template = setting("export/command")
            self.log(f"Export command: {self.gallery.command_template or '(none)'}")

    def stop_task(self) -> None:
        self.gallery.stop_task()

    # -- task status -------------------------------------------------------
    def _on_task_started(self, status: str, stoppable: bool) -> None:
        self._status = status
        self.log_dock.show()
        self.log_panel.status.setText(status)
        self.log_panel.stop_btn.setEnabled(stoppable)
        self.export_btn.setEnabled(False)
        self.rescan_btn.setEnabled(False)

    def _on_task_progress(self, a: int, b: int) -> None:
        self.log_panel.status.setText(f"{self._status}  {a}/{b}")

    def _on_task_finished(self, ok: bool) -> None:
        self.log_panel.status.setText("Idle" if ok else "Idle (last job had errors or was stopped)")
        self.log_panel.stop_btn.setEnabled(False)
        self.export_btn.setEnabled(True)
        self.rescan_btn.setEnabled(True)

    def _on_count(self, visible: int, total: int) -> None:
        self.count_lbl.setText(f"{total:,} assets" if visible == total else f"{visible:,} of {total:,} assets")

    def closeEvent(self, e) -> None:
        self.gallery.stop_task()
        if self.gallery._task is not None:
            self.gallery._task.wait(3000)
        self.gallery.details.player.stop()
        self.gallery.library.remove_listener(self.gallery._on_library_changed)
        super().closeEvent(e)
