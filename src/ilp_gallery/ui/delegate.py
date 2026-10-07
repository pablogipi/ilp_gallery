"""Tile painting: image (poster or hover-scrub frame), name and type subtitle."""

from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from ..core.item import MEDIA_GIF, MEDIA_MOVIE, GalleryItem
from ..media.thumbs import ThumbnailCache
from . import theme
from .model import ItemRole

BADGE = {MEDIA_MOVIE: "MOV", MEDIA_GIF: "GIF"}
TYPE_NAMES = {"image": "Image", "gif": "GIF", "movie": "Movie"}


def subtitle(item: GalleryItem) -> str:
    return item.metadata.get("type_label") or TYPE_NAMES.get(item.type, item.type)


class TileDelegate(QStyledItemDelegate):
    MARGIN = 5          # space around the card
    PAD = 5             # inside the card
    LABEL_H = 36        # name + subtitle
    RADIUS = 5

    def __init__(self, cache: ThumbnailCache, parent=None) -> None:
        super().__init__(parent)
        self.cache = cache
        self.tile_width = 170
        self.aspect = 16 / 9
        self.hover_id: str | None = None
        self.hover_fraction = 0.0
        self._tile = theme.c(theme.TILE)
        self._tile_hover = theme.c(theme.TILE_HOVER)
        self._image_bg = theme.c(theme.IMAGE_BG)
        self._accent = theme.c(theme.ACCENT)
        self._text = theme.c(theme.TEXT)
        self._muted = theme.c(theme.MUTED)

    # -- geometry ------------------------------------------------------
    def image_height(self) -> int:
        return int(self.tile_width / self.aspect)

    def sizeHint(self, option, index) -> QSize:
        return QSize(self.tile_width + 2 * (self.MARGIN + self.PAD),
                     self.image_height() + self.LABEL_H + 2 * (self.MARGIN + self.PAD))

    def card_rect(self, cell: QRect) -> QRect:
        return cell.adjusted(self.MARGIN, self.MARGIN, -self.MARGIN, -self.MARGIN)

    def image_rect(self, cell: QRect) -> QRect:
        card = self.card_rect(cell)
        return QRect(card.x() + self.PAD, card.y() + self.PAD,
                     card.width() - 2 * self.PAD, self.image_height())

    # -- painting ------------------------------------------------------
    def paint(self, p: QPainter, option, index) -> None:
        item: GalleryItem = index.data(ItemRole)
        if item is None:
            return
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        selected = bool(option.state & QStyle.State_Selected)
        hovered = item.id == self.hover_id
        card = QRectF(self.card_rect(option.rect))
        img_rect = self.image_rect(option.rect)

        path = QPainterPath()
        path.addRoundedRect(card, self.RADIUS, self.RADIUS)
        p.fillPath(path, self._tile_hover if (hovered or selected) else self._tile)

        # image: scrub frame while hovering animated media, poster otherwise
        pix = None
        if hovered and item.is_animated:
            frames = self.cache.frames(item)
            if frames:
                pix = frames[min(len(frames) - 1, int(self.hover_fraction * len(frames)))]
        if pix is None:
            pix = self.cache.poster(item)  # queues generation if missing

        clip = QPainterPath()
        clip.addRoundedRect(QRectF(img_rect), 3, 3)
        p.setClipPath(clip)
        p.fillRect(img_rect, self._image_bg)
        if pix is not None and not pix.isNull():
            scaled = pix.size().scaled(img_rect.size(), Qt.KeepAspectRatio)
            target = QRect(0, 0, scaled.width(), scaled.height())
            target.moveCenter(img_rect.center())
            p.drawPixmap(target, pix)
        else:
            p.setPen(self._muted)
            p.drawText(img_rect, Qt.AlignCenter, "no preview" if self.cache.failed(item.id) else "")
        p.setClipping(False)

        if hovered and item.is_animated:
            p.fillRect(QRect(img_rect.left(), img_rect.bottom() - 2,
                             int(img_rect.width() * self.hover_fraction), 3), self._accent)

        badge = BADGE.get(item.type)
        if badge:
            f = QFont(option.font)
            f.setPointSizeF(max(6.5, f.pointSizeF() * 0.75))
            f.setBold(True)
            p.setFont(f)
            fm = QFontMetrics(f)
            r = QRect(img_rect.left() + 5, img_rect.top() + 5, fm.horizontalAdvance(badge) + 8, fm.height() + 2)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 160))
            p.drawRoundedRect(r, 3, 3)
            p.setPen(QColor(235, 235, 235))
            p.drawText(r, Qt.AlignCenter, badge)

        # name + subtitle
        name_font = QFont(option.font)
        name_font.setWeight(QFont.DemiBold)
        sub_font = QFont(option.font)
        sub_font.setPointSizeF(max(7.0, option.font.pointSizeF() * 0.85))
        nfm, sfm = QFontMetrics(name_font), QFontMetrics(sub_font)
        x, w = img_rect.left() + 1, img_rect.width() - 2
        y = img_rect.bottom() + 5
        p.setFont(name_font)
        p.setPen(self._text)
        p.drawText(QRect(x, y, w, nfm.height()), Qt.AlignLeft | Qt.AlignVCenter,
                   nfm.elidedText(item.name, Qt.ElideRight, w))
        p.setFont(sub_font)
        p.setPen(self._muted)
        p.drawText(QRect(x, y + nfm.height(), w, sfm.height()), Qt.AlignLeft | Qt.AlignVCenter,
                   sfm.elidedText(subtitle(item), Qt.ElideRight, w))

        if selected:
            p.setPen(QPen(self._accent, 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(card.adjusted(1, 1, -1, -1), self.RADIUS, self.RADIUS)
        p.restore()
