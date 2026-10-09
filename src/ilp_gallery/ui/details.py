"""Right-hand panel: preview, name, actions and metadata of the current item."""

from __future__ import annotations

import html
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from ..core.item import GalleryItem
from . import theme
from .delegate import subtitle
from .player import PlayerWidget
from .tree import display_name, item_paths

# metadata keys that are plumbing, not information
HIDDEN_META = {
    "source", "importer_version", "tile_aspect", "type_label", "category_paths", "display",
    "asset_dir", "json", "json_mtime", "maps", "resolutions", "megascans_type",
}


def _res_label(res: str) -> str:
    try:
        w = int(str(res).lower().split("x")[0])
    except ValueError:
        return str(res)
    return f"{w // 1024}K" if w >= 1024 and w % 1024 == 0 else str(w)


class DetailPanel(QWidget):
    exportRequested = Signal(list)          # [GalleryItem] -> default exporter
    exportMenuRequested = Signal(list, object)  # [GalleryItem], global QPoint -> choose exporter
    copyCommandRequested = Signal(list)
    previewRequested = Signal(list)         # [GalleryItem] -> the preview command
    openFolderRequested = Signal(object)    # GalleryItem
    categoryClicked = Signal(tuple)
    tagClicked = Signal(str)

    def __init__(self, cache=None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("details")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.item: GalleryItem | None = None

        self.player = PlayerWidget(cache)
        self._theme = theme.ILP
        self.player.canvas.background = self._theme.c("image_bg")
        self.player.setMinimumHeight(260)
        self.player.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.title = QLabel("", objectName="title")
        self.title.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.sub = QLabel("", objectName="muted")
        self.sub.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.export_btn = QPushButton("Export", objectName="primary")
        self.more_btn = QPushButton("▾", objectName="primary")
        self.more_btn.setFixedWidth(24)
        self.more_btn.setStyleSheet("padding: 5px 0px;")
        self.more_btn.setToolTip("Other exporters")
        self.more_btn.setVisible(False)
        self.copy_btn = QPushButton("Copy command")
        self.folder_btn = QPushButton("Open folder")
        self.preview_btn = QPushButton("Preview")
        self.preview_btn.setToolTip("Open the preview movie with the preview command")
        self.preview_btn.setVisible(False)
        self._preview_on = False
        self.can_preview = lambda item: item.motion is not None   # set by the gallery
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        export_group = QHBoxLayout()
        export_group.setSpacing(1)
        export_group.addWidget(self.export_btn)
        export_group.addWidget(self.more_btn)
        buttons.addLayout(export_group)
        for b in (self.export_btn, self.more_btn, self.copy_btn, self.folder_btn, self.preview_btn):
            b.setCursor(Qt.PointingHandCursor)
        buttons.addWidget(self.preview_btn)
        buttons.addStretch(1)
        # utilities on a second row, so the panel stays narrow
        tools = QHBoxLayout()
        tools.setSpacing(6)
        tools.addWidget(self.copy_btn)
        tools.addWidget(self.folder_btn)
        tools.addStretch(1)

        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.RichText)
        self.info.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.info.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.info.setOpenExternalLinks(False)
        self.info.linkActivated.connect(self._on_link)

        self.empty = QLabel("Select an asset", objectName="muted")
        self.empty.setAlignment(Qt.AlignCenter)

        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(12, 10, 12, 12)
        bl.setSpacing(6)
        bl.addWidget(self.title)
        bl.addWidget(self.sub)
        bl.addSpacing(4)
        bl.addLayout(buttons)
        bl.addLayout(tools)
        bl.addSpacing(6)
        bl.addWidget(self.info)
        bl.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        self._body = scroll

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 0)
        lay.setSpacing(0)
        lay.addWidget(self.player, 5)
        lay.addWidget(scroll, 6)
        lay.addWidget(self.empty, 1)

        self.export_btn.clicked.connect(lambda: self.item and self.exportRequested.emit([self.item]))
        self.more_btn.clicked.connect(lambda: self.item and self.exportMenuRequested.emit(
            [self.item], self.more_btn.mapToGlobal(self.more_btn.rect().bottomLeft())))
        self.copy_btn.clicked.connect(lambda: self.item and self.copyCommandRequested.emit([self.item]))
        self.folder_btn.clicked.connect(lambda: self.item and self.openFolderRequested.emit(self.item))
        self.preview_btn.clicked.connect(lambda: self.item and self.previewRequested.emit([self.item]))
        self.set_export_enabled(False)
        self.set_item(None)

    def set_export_enabled(self, on: bool, reason: str = "") -> None:
        self._export_on = on
        self.export_btn.setVisible(on)
        self.copy_btn.setVisible(on)
        self.more_btn.setVisible(on and self._more)
        self.export_btn.setToolTip(reason)

    _export_on = False
    _more = False

    def set_preview_enabled(self, on: bool) -> None:
        """Show the Preview button (for items with a preview movie) when a command is set."""
        self._preview_on = on
        self.preview_btn.setVisible(on and self.item is not None and self.can_preview(self.item))

    def set_export_label(self, label: str, more: bool = False) -> None:
        """Label of the default exporter; ``more`` shows the ▾ menu of other exporters."""
        # Very long names would push the other buttons off the panel: elide, full text in the tooltip.
        self.export_btn.setText(label if len(label) <= 24 else label[:23].rstrip() + "…")
        self.export_btn.setToolTip(label)
        self._more = more
        self.more_btn.setVisible(self._export_on and more)

    # -- content ---------------------------------------------------------
    def set_item(self, item: GalleryItem | None, autoplay: bool = False) -> None:
        self.item = item
        has = item is not None
        self.player.setVisible(has)
        self.empty.setVisible(False)
        for b in (self.export_btn, self.more_btn, self.copy_btn, self.folder_btn, self.preview_btn):
            b.setEnabled(has)
        self.player.set_item(item.motion_item() if item is not None else None, autoplay=autoplay)
        self.preview_btn.setVisible(self._preview_on and item is not None and self.can_preview(item))
        if not has:
            self.title.setText("Select an asset to see its details.")
            self.title.setStyleSheet("font-size: 10pt; font-weight: 600;")
            self.sub.setText("")
            self.info.setText("")
            return
        self.title.setStyleSheet("")
        self.title.setText(item.name)
        self.sub.setText(f"{item.id if item.id != item.path else ''}   {subtitle(item)}".strip())
        self.info.setText(self._html(item))

    def set_theme(self, t: theme.Theme) -> None:
        self._theme = t
        self.player.canvas.background = t.c("image_bg")
        self.player.canvas.update()
        if self.item is not None:
            self.info.setText(self._html(self.item))

    def _html(self, it: GalleryItem) -> str:
        link = self._theme.link
        muted = self._theme.muted
        esc = html.escape
        parts = [f"<style>a {{ color: {link}; text-decoration: none; }} td {{ padding: 2px 14px 2px 0; }}"
                 f" .k {{ color: {muted}; }} h4 {{ margin: 12px 0 4px 0; }}</style>"]

        # categories as clickable breadcrumbs
        crumbs = []
        for path in item_paths(it):
            segs = []
            for d in range(len(path)):
                segs.append(f'<a href="cat:{esc("/".join(path[:d + 1]))}">{esc(display_name(path[d], d))}</a>')
            crumbs.append(" <span class='k'>›</span> ".join(segs))
        if crumbs:
            parts.append("<h4>Category</h4>" + "<br>".join(crumbs))

        # key / value table
        rows = it.metadata.get("display")
        if not rows:
            rows = [[k.replace("_", " ").capitalize(), v] for k, v in it.metadata.items()
                    if k not in HIDDEN_META and isinstance(v, (str, int, float, bool))
                    and str(v).strip().lower() not in ("", "none", "undefined", "null")]
            rows = [[k, "Yes" if v is True else "No" if v is False else v] for k, v in rows]
        if rows:
            parts.append("<table cellspacing='0'>" + "".join(
                f"<tr><td class='k' valign='top'>{esc(str(k))}</td><td>{esc(str(v))}</td></tr>" for k, v in rows
            ) + "</table>")

        shown = {str(v).lower() for _k, v in rows or []}
        if it.description and not all(p.strip().lower() in shown for p in it.description.split(",")):
            parts.append(f"<p>{esc(it.description)}</p>")

        if it.tags:
            parts.append("<h4>Tags</h4>" + ", ".join(
                f'<a href="tag:{esc(t)}">{esc(t)}</a>' for t in it.tags))

        maps = it.metadata.get("maps") or []
        res = it.metadata.get("resolutions") or []
        if maps or res:
            tex = []
            if maps:
                tex.append(", ".join(esc(m.capitalize() if m.islower() else m) for m in maps))
            if res:
                tex.append("<span class='k'>Resolutions:</span> " + " · ".join(esc(_res_label(r)) for r in res))
            parts.append("<h4>Textures</h4>" + "<br>".join(tex))

        folder = it.metadata.get("asset_dir") or os.path.dirname(it.path)
        parts.append(f"<h4>Location</h4><span class='k'>{esc(folder)}</span>")
        return "".join(parts)

    def _on_link(self, href: str) -> None:
        kind, _, value = href.partition(":")
        if kind == "cat":
            self.categoryClicked.emit(tuple(value.split("/")))
        elif kind == "tag":
            self.tagClicked.emit(value)
