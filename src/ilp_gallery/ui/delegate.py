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
OUTDATED = QColor("#e0a030")      # matches the drawer's outdated colour
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
        self.placeholder = None                # QIcon for items without a thumbnail
        self.hover_fraction = 0.0
        self.marks: dict[str, tuple[bool, str]] = {}   # item id -> (enabled, drawer state)
        self.set_theme(theme.ILP)

    def set_theme(self, t: theme.Theme) -> None:
        self._tile = t.c("tile")
        self._tile_hover = t.c("tile_hover")
        self._image_bg = t.c("image_bg")
        self._accent = t.c("accent")
        self._text = t.c("text")
        self._muted = t.c("muted")
        self._bevel = t.tile_bevel()          # SGI themes: Motif-bevelled square tiles

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

        radius = 0 if self._bevel else self.RADIUS
        path = QPainterPath()
        path.addRoundedRect(card, radius, radius)
        p.fillPath(path, self._tile_hover if (hovered or selected) else self._tile)

        # image: scrub frame while hovering animated media, poster otherwise
        pix = None
        moves = item.motion is not None          # a movie / GIF itself, or a preview movie
        if hovered and moves:
            frames = self.cache.frames(item)
            if frames:
                pix = frames[min(len(frames) - 1, int(self.hover_fraction * len(frames)))]
        if pix is None:
            pix = self.cache.poster(item)  # queues generation if missing

        clip = QPainterPath()
        clip.addRoundedRect(QRectF(img_rect), 0 if self._bevel else 3, 0 if self._bevel else 3)
        p.setClipPath(clip)
        p.fillRect(img_rect, self._image_bg)
        if pix is not None and not pix.isNull():
            scaled = pix.size().scaled(img_rect.size(), Qt.KeepAspectRatio)
            target = QRect(0, 0, scaled.width(), scaled.height())
            target.moveCenter(img_rect.center())
            p.drawPixmap(target, pix)
        elif self.cache.failed(item.id):
            self._paint_placeholder(p, img_rect)
        p.setClipping(False)

        if hovered and moves:
            p.fillRect(QRect(img_rect.left(), img_rect.bottom() - 2,
                             int(img_rect.width() * self.hover_fraction), 3), self._accent)

        badge = BADGE.get(item.motion[1] if moves else item.type)
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

        mark = self.marks.get(item.id)
        if mark is not None:
            self._paint_mark(p, img_rect, *mark)

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

        if self._bevel:
            self._paint_bevel(p, option.rect, selected)
        elif selected:
            p.setPen(QPen(self._accent, 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(card.adjusted(1, 1, -1, -1), self.RADIUS, self.RADIUS)
        p.restore()

    def _paint_bevel(self, p: QPainter, cell: QRect, selected: bool) -> None:
        """Two-step Motif bevel round the card (pressed in when selected, with an accent
        frame) and a sunken frame round the image."""
        hl, hl2, sh2, sh = self._bevel
        card = self.card_rect(cell).adjusted(0, 0, -1, -1)
        img = self.image_rect(cell).adjusted(-2, -2, 1, 1)
        p.save()
        p.setRenderHint(QPainter.Antialiasing, False)
        p.setBrush(Qt.NoBrush)

        def bevel(r: QRect, outer_tl, inner_tl, inner_br, outer_br) -> None:
            for i, (tl, br) in enumerate(((outer_tl, outer_br), (inner_tl, inner_br))):
                rr = r.adjusted(i, i, -i, -i)
                p.setPen(QPen(tl, 1))
                p.drawLine(rr.topLeft(), rr.topRight())
                p.drawLine(rr.topLeft(), rr.bottomLeft())
                p.setPen(QPen(br, 1))
                p.drawLine(rr.bottomLeft(), rr.bottomRight())
                p.drawLine(rr.topRight(), rr.bottomRight())

        if selected:
            bevel(card, sh, sh2, hl2, hl)
        else:
            bevel(card, hl, hl2, sh2, sh)
        bevel(img, sh, sh2, hl2, hl)
        if selected:
            p.setPen(QPen(self._accent, 2))
            p.drawRect(card.adjusted(3, 3, -2, -2))
        p.restore()

    def _paint_mark(self, p: QPainter, img_rect: QRect, enabled: bool, state: str) -> None:
        """Drawer badge, top right: a filled disc with a tick when enabled, a ring when
        disabled; orange instead of the accent when the drawer's path is outdated."""
        d = max(12, min(20, img_rect.height() // 6))
        r = QRectF(img_rect.right() - d - 5, img_rect.top() + 5, d, d)
        color = OUTDATED if state == "outdated" else self._accent
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(0, 0, 0, 150), 1.0))
        p.setBrush(QColor(0, 0, 0, 120))
        p.drawEllipse(r.adjusted(-1.5, -1.5, 1.5, 1.5))      # dark halo: readable on any image
        if enabled:
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawEllipse(r)
            tick = QPainterPath()
            tick.moveTo(r.left() + d * 0.27, r.top() + d * 0.53)
            tick.lineTo(r.left() + d * 0.44, r.top() + d * 0.70)
            tick.lineTo(r.left() + d * 0.75, r.top() + d * 0.33)
            p.setPen(QPen(QColor(255, 255, 255), max(1.5, d / 8), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(tick)
        else:
            p.setPen(QPen(color, max(1.5, d / 7)))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(r.adjusted(1, 1, -1, -1))
        p.restore()

    def _paint_placeholder(self, p: QPainter, img_rect: QRect) -> None:
        """The 'no thumbnail' icon, centred at a modest size; text if there's no icon."""
        icon = self.placeholder
        if icon is None or icon.isNull():
            p.setPen(self._muted)
            p.drawText(img_rect, Qt.AlignCenter, "no preview")
            return
        side = max(16, min(64, int(img_rect.height() * 0.42)))
        target = QRect(0, 0, side, side)
        target.moveCenter(img_rect.center())
        icon.paint(p, target)
