"""Grid view with hover-scrub for movies and GIFs."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QSize, Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QListView

from .delegate import TileDelegate
from .model import IdRole


class GalleryView(QListView):
    hoverChanged = Signal(str)  # item id ("" when leaving)

    def __init__(self, delegate: TileDelegate, parent=None) -> None:
        super().__init__(parent)
        self._delegate = delegate
        self.setItemDelegate(delegate)
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setUniformItemSizes(True)
        self.setLayoutMode(QListView.Batched)  # stay responsive with 10k+ items
        self.setBatchSize(200)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setMouseTracking(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.setSpacing(2)
        self.setStyleSheet("QListView { background: #1c1c1f; border: none; }")
        self._hover_index = QModelIndex()

    # list mode: thumbnail height range (px) the size control maps onto
    ROW_MIN, ROW_MAX = 22, 56

    def mode(self) -> str:
        return self._delegate.mode

    def set_mode(self, mode: str) -> None:
        """"grid" (tiles) or "list" (rows, like the drawer)."""
        if mode not in ("grid", "list"):
            raise ValueError(f"mode must be 'grid' or 'list', not {mode!r}")
        self._delegate.mode = mode
        if mode == "list":
            self.setViewMode(QListView.ListMode)
            self.setFlow(QListView.TopToBottom)
            self.setWrapping(False)
            self.setSpacing(0)
            self.setGridSize(QSize())
        else:
            self.setViewMode(QListView.IconMode)
            self.setFlow(QListView.LeftToRight)          # setViewMode keeps the list's flow and
            self.setWrapping(True)                       # no-wrap: one column of tiles otherwise
            self.setSpacing(2)
        self.setMovement(QListView.Static)            # setViewMode resets it
        self.setResizeMode(QListView.Adjust)
        self.setProperty("listMode", mode == "list")  # the themes colour list mode like the list panes
        self.style().unpolish(self)
        self.style().polish(self)
        self.viewport().update()

    def set_tile_width(self, width: int, lo: int = 90, hi: int = 420) -> None:
        """The size control's value: the tile width in grid mode; in list mode the
        same range maps onto a small band of row heights (``ROW_MIN``-``ROW_MAX``
        thumbnails), so rows grow a little, never to tile size."""
        d = self._delegate
        d.tile_width = width
        if d.mode == "list":
            t = (min(max(width, lo), hi) - lo) / max(1, hi - lo)
            d.row_height = round(self.ROW_MIN + t * (self.ROW_MAX - self.ROW_MIN)) + 6
            self.setGridSize(QSize())
            self.doItemsLayout()
            return
        # A fixed grid size makes the layout pick up the new tile size at once.
        self.setGridSize(d.sizeHint(None, QModelIndex()))

    # -- hover scrub -----------------------------------------------------
    def mouseMoveEvent(self, event) -> None:
        super().mouseMoveEvent(event)
        pos = event.position().toPoint()
        index = self.indexAt(pos)
        d = self._delegate
        if index != self._hover_index:
            if self._hover_index.isValid():
                self.update(self._hover_index)
            self._hover_index = index
            d.hover_id = index.data(IdRole) if index.isValid() else None
            self.hoverChanged.emit(d.hover_id or "")
        if index.isValid():
            # grid: scrub across the image; list: across the whole row (the thumbnail is small)
            img = self.visualRect(index) if d.mode == "list" else d.image_rect(self.visualRect(index))
            x = min(max(pos.x() - img.left(), 0), max(1, img.width() - 1))
            d.hover_fraction = x / max(1, img.width())
            self.update(index)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        if self._hover_index.isValid():
            self.update(self._hover_index)
        self._hover_index = QModelIndex()
        self._delegate.hover_id = None
        self.hoverChanged.emit("")

    def refresh_item(self, index: QModelIndex) -> None:
        if index.isValid():
            self.update(index)
