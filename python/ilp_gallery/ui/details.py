"""Right-hand panel: preview, name, actions and metadata of the current item."""

from __future__ import annotations

import dataclasses
import html
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from ..core.item import MEDIA_IMAGE, GalleryItem
from . import theme
from .delegate import subtitle
from .names import format_name
from .player import PlayerWidget
from .tree import display_name, item_paths

# metadata keys that are plumbing, not information
HIDDEN_META = {
    "source", "importer_version", "tile_aspect", "type_label", "category", "category_paths", "display",
    "asset_dir", "json", "json_mtime", "maps", "resolutions", "megascans_type",
    "images", "image_label", "image_group",          # the preview pane's own fields
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
        # More images of the item (metadata["images"], e.g. Megascans' Clay and
        # Tiled renders): arrows under the preview, as in Quixel Bridge.
        # (path or None for the item's own preview, label, group); the arrows step
        # through the current group's images, one button per group switches group
        self._images: list[tuple[str | None, str, str]] = []
        self._image_index = 0                                # within the current group
        self._group = ""
        self._group_btns: dict[str, QPushButton] = {}
        self.images_bar = QWidget(objectName="imagesBar")
        bar = QHBoxLayout(self.images_bar)
        bar.setContentsMargins(0, 4, 0, 2)
        bar.setSpacing(4)
        self.image_prev = QPushButton("‹", objectName="flat")
        self.image_next = QPushButton("›", objectName="flat")
        for b, tip in ((self.image_prev, "Previous image (Left)"), (self.image_next, "Next image (Right)")):
            b.setProperty("squareTool", True)            # no padding: the SGI button rule hid the arrow
            b.setFixedSize(22, 22)
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
        self.image_label = QLabel("", objectName="muted")
        self._group_lay = QHBoxLayout()
        self._group_lay.setSpacing(2)
        bar.addLayout(self._group_lay)
        bar.addSpacing(6)
        bar.addWidget(self.image_label)
        bar.addStretch(1)
        bar.addWidget(self.image_prev)
        bar.addWidget(self.image_next)
        self.images_bar.hide()
        self.image_prev.clicked.connect(lambda: self.step_image(-1))
        self.image_next.clicked.connect(lambda: self.step_image(1))

        self.title = QLabel("", objectName="title")
        self.title.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.sub = QLabel("", objectName="muted")
        self.sub.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.export_btn = QPushButton("Export", objectName="primary")
        self.more_btn = QPushButton("▾", objectName="primary")
        self.more_btn.setFixedWidth(24)
        self.more_btn.setProperty("menuButton", True)   # SGI: plain, the ring is Export's
        self.more_btn.setStyleSheet("padding: 5px 0px;")
        self.more_btn.setToolTip("Other exporters")
        self.more_btn.setVisible(False)
        self.copy_btn = QPushButton("Copy path")       # "Copy command" with a command template
        self.folder_btn = QPushButton("Open folder")
        self.preview_btn = QPushButton("View")
        self.preview_btn.setToolTip("Open in the viewer (RV); also double-click")
        self.preview_btn.setVisible(False)
        self._preview_on = False
        self.can_preview = lambda item: item.motion is not None   # set by the gallery
        self.name_format = None           # set by the gallery: detail rows, see ui/names.py
        self.show_paths = True            # Location and "Open folder"
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        export_group = QHBoxLayout()
        export_group.setSpacing(1)
        export_group.addWidget(self.export_btn)
        export_group.addWidget(self.more_btn)
        buttons.addLayout(export_group)
        buttons.addWidget(self.copy_btn)                # beside the main action
        buttons.addWidget(self.preview_btn)             # View
        for b in (self.export_btn, self.more_btn, self.copy_btn, self.folder_btn, self.preview_btn):
            b.setCursor(Qt.PointingHandCursor)
        buttons.addStretch(1)
        # utilities on a second row, so the panel stays narrow
        tools = QHBoxLayout()
        tools.setSpacing(6)
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
        lay.addWidget(self.images_bar)
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
        self.folder_btn.setVisible(self.show_paths)            # also before anything is selected
        self.player.set_item(item.motion_item() if item is not None else None, autoplay=autoplay)
        self._set_images(item)
        self.preview_btn.setVisible(self._preview_on and item is not None and self.can_preview(item))
        if not has:
            self.title.setText("Select an asset to see its details.")
            self.title.setStyleSheet("font-size: 10pt; font-weight: 600;")
            self.sub.setText("")
            self.info.setText("")
            return
        self.title.setStyleSheet("")
        parts = format_name(self.name_format, item)
        if parts.details:
            # the host's short title; the full name is in the rows' tooltip and copyable
            self.title.setText(parts.title)
            self.title.setToolTip(item.name)
        else:
            # let long names_like__this wrap at their separators (zero-width spaces)
            self.title.setText(item.name.replace("__", "__\u200b").replace("/", "/\u200b"))
            self.title.setToolTip("")
        self.folder_btn.setVisible(self.show_paths)
        if format_name(self.name_format, item).details:
            self.sub.setText("")                         # the detail rows say it all
        else:
            ident = item.id if item.id not in (item.path, item.name) else ""
            self.sub.setText(f"{ident}   {subtitle(item)}".strip())
        self.sub.setVisible(bool(self.sub.text()))
        self.info.setText(self._html(item))

    # -- more images ---------------------------------------------------------
    def _set_images(self, item: GalleryItem | None) -> None:
        """The item's images: its own preview, then ``metadata["images"]`` entries,
        ``{"path", "label", "group"}`` dicts or bare paths. ``image_label`` and
        ``image_group`` name the item's own preview."""
        self._images, self._image_index = [], 0
        if item is not None:
            m = item.metadata
            own_group = str(m.get("image_group") or "")
            self._images.append((None, str(m.get("image_label") or ""), own_group))
            for entry in m.get("images") or ():
                if isinstance(entry, dict) and entry.get("path"):
                    self._images.append((str(entry["path"]), str(entry.get("label") or ""),
                                         str(entry.get("group") or own_group)))
                elif isinstance(entry, str) and entry:
                    self._images.append((entry, "", own_group))
        groups = list(dict.fromkeys(g for _p, _l, g in self._images))
        self._group = groups[0] if groups else ""
        for b in self._group_btns.values():
            b.deleteLater()
        self._group_btns = {}
        if len(groups) > 1:                              # one button per group: Renders / Maps
            for g in groups:
                b = QPushButton(g or "Images", objectName="flat", checkable=True)
                b.setProperty("imageGroup", True)          # the themes style the checked group
                b.setCursor(Qt.PointingHandCursor)
                b.clicked.connect(lambda _c=False, g=g: self.set_image_group(g))
                self._group_lay.addWidget(b)
                self._group_btns[g] = b
        self.images_bar.setVisible(len(self._images) > 1)
        self._update_image_label()

    def _group_images(self) -> list[tuple[str | None, str, str]]:
        return [im for im in self._images if im[2] == self._group]

    def image_count(self, group: str | None = None) -> int:
        """Images of ``group`` (default: all), the item's own preview included."""
        return len(self._images) if group is None else sum(1 for im in self._images if im[2] == group)

    def image_groups(self) -> list[str]:
        return list(dict.fromkeys(g for _p, _l, g in self._images))

    def set_image_group(self, group: str) -> None:
        """Switch to ``group``'s images (e.g. "Maps"), showing its first one."""
        if group in self.image_groups():
            self._group = group
            self.show_image(0)

    def show_image(self, index: int) -> None:
        """Show the current group's ``index``-th image, wrapping round."""
        images = self._group_images()
        if self.item is None or not images or len(self._images) < 2:
            return
        self._image_index = index % len(images)
        path = images[self._image_index][0]
        if path is None:
            self.player.set_item(self.item.motion_item(), autoplay=False, keep_image=True)
        else:
            meta = {k: v for k, v in self.item.metadata.items() if k not in ("preview", "movie")}
            still = dataclasses.replace(self.item, path=path, type=MEDIA_IMAGE, metadata=meta)
            self.player.set_item(still, autoplay=False, keep_image=True)
        self._update_image_label()

    def step_image(self, step: int) -> None:
        self.show_image(self._image_index + step)

    def _update_image_label(self) -> None:
        images = self._group_images()
        for g, b in self._group_btns.items():
            b.setChecked(g == self._group)
        many = len(images) > 1
        self.image_prev.setEnabled(many)
        self.image_next.setEnabled(many)
        if len(self._images) < 2 or not images:
            self.image_label.setText("")
            return
        label = images[self._image_index][1] or images[0][1] or "Image"
        self.image_label.setText(f"{label}  {self._image_index + 1} / {len(images)}")

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Left, Qt.Key_Right) and len(self._images) > 1:
            self.step_image(-1 if event.key() == Qt.Key_Left else 1)
            return
        super().keyPressEvent(event)

    def set_copy_label(self, label: str, tooltip: str) -> None:
        self.copy_btn.setText(label)
        self.copy_btn.setToolTip(tooltip)

    def set_theme(self, t: theme.Theme) -> None:
        self._theme = t
        self.player.set_icon_color(t.c("text"))
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

        detail = format_name(self.name_format, it).details
        if detail:
            # The host's own rows (e.g. Category, Action, Variations) replace the
            # category breadcrumbs and tags; the record's display rows follow.
            rows = [*detail, *(it.metadata.get("display") or [])]
            parts.append("<table cellspacing='0'>" + "".join(
                f"<tr><td class='k' valign='top'>{esc(str(k))}</td><td>{esc(str(v))}</td></tr>"
                for k, v in rows) + "</table>")
            if it.description:
                parts.append(f"<p>{esc(it.description)}</p>")
            if self.show_paths:
                folder = it.metadata.get("asset_dir") or os.path.dirname(it.path)
                parts.append(f"<h4>Location</h4><span class='k'>{esc(folder)}</span>")
            return "".join(parts)

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

        if self.show_paths:
            folder = it.metadata.get("asset_dir") or os.path.dirname(it.path)
            parts.append(f"<h4>Location</h4><span class='k'>{esc(folder)}</span>")
        return "".join(parts)

    def _on_link(self, href: str) -> None:
        kind, _, value = href.partition(":")
        if kind == "cat":
            self.categoryClicked.emit(tuple(value.split("/")))
        elif kind == "tag":
            self.tagClicked.emit(value)
