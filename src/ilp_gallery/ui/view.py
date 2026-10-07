"""Grid view with hover-scrub for movies and GIFs."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, Qt, Signal
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

    def set_tile_width(self, width: int) -> None:
        self._delegate.tile_width = width
        # A fixed grid size makes the layout pick up the new tile size at once.
        self.setGridSize(self._delegate.sizeHint(None, QModelIndex()))

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
            img = d.image_rect(self.visualRect(index))
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
