"""Qt list model over a GalleryLibrary + the current search result."""

from __future__ import annotations

import json

from PySide6.QtCore import QAbstractListModel, QMimeData, QModelIndex, Qt, QUrl

from ..core.item import GalleryItem
from ..core.library import GalleryLibrary
from .names import format_name, tooltip_html

ItemRole = Qt.UserRole + 1
IdRole = Qt.UserRole + 2
MIME_ITEMS = "application/x-ilp-gallery-items"


class GalleryModel(QAbstractListModel):
    def __init__(self, library: GalleryLibrary, parent=None) -> None:
        super().__init__(parent)
        self.library = library
        self._ids: list[str] = []
        self._rows: dict[str, int] = {}
        # set by GalleryWidget: items -> QMimeData (merges registered drag handlers)
        self.mime_builder = None
        self.name_format = None          # set by GalleryWidget (tooltips, see ui/names.py)
        self.show_paths = True

    # -- result set ----------------------------------------------------
    def set_ids(self, ids: list[str]) -> None:
        self.beginResetModel()
        self._ids = [i for i in ids if i in self.library]
        self._rows = {iid: r for r, iid in enumerate(self._ids)}
        self.endResetModel()

    def ids(self) -> list[str]:
        return list(self._ids)

    def index_of(self, item_id: str) -> QModelIndex:
        row = self._rows.get(item_id)
        return self.index(row) if row is not None else QModelIndex()

    def item(self, index: QModelIndex) -> GalleryItem | None:
        if not index.isValid() or index.row() >= len(self._ids):
            return None
        return self.library.get(self._ids[index.row()])

    # -- Qt model API --------------------------------------------------
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._ids)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        it = self.item(index)
        if it is None:
            return None
        if role == Qt.DisplayRole:
            return it.name
        if role == ItemRole:
            return it
        if role == IdRole:
            return it.id
        if role == Qt.ToolTipRole:
            if self.name_format is not None:
                parts = format_name(self.name_format, it)
                if parts.details:
                    return tooltip_html(it.name, parts, [it.path] if self.show_paths else [])
            lines = [f"<b>{it.name}</b>"] + ([it.path] if self.show_paths else [])
            if it.categories:
                lines.append("Categories: " + ", ".join(it.categories))
            if it.tags:
                lines.append("Tags: " + ", ".join(it.tags))
            if it.description:
                lines.append(it.description)
            return "<br>".join(lines)
        return None

    def flags(self, index: QModelIndex):
        f = super().flags(index)
        return f | Qt.ItemIsDragEnabled if index.isValid() else f

    # Drag out: file URLs (drop into Nuke / Houdini / a file browser) plus the
    # JSON records for tools that want the full metadata.
    def mimeTypes(self) -> list[str]:
        return ["text/uri-list", "text/plain", MIME_ITEMS]

    def mimeData(self, indexes) -> QMimeData:
        items = [it for it in (self.item(i) for i in indexes) if it]
        if self.mime_builder is not None:
            return self.mime_builder(items)
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(it.path) for it in items])
        md.setText("\n".join(it.path for it in items))
        md.setData(MIME_ITEMS, json.dumps([it.to_dict() for it in items]).encode())
        return md

    def supportedDragActions(self):
        return Qt.CopyAction
