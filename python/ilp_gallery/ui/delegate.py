"""Tile painting: image (poster or hover-scrub frame), name and type subtitle."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from ..core.item import MEDIA_GIF, MEDIA_MOVIE, GalleryItem
from ..media.thumbs import ThumbnailCache
from . import theme
from .model import ItemRole
from .names import format_name, paint_chip, split_title

BADGE = {MEDIA_MOVIE: "MOV", MEDIA_GIF: "GIF"}
MARK_OK = QColor("#3fae5a")       # drawer badge: in the drawer, path up to date
MARK_OUTDATED = QColor("#e0a030")  # the library has another path (matches the drawer text)
FAVOURITE = QColor("#f2c230")      # the favourite star (fixed, like the drawer badge)


def star_path(r: QRectF) -> QPainterPath:
    """A five-pointed star filling ``r``."""
    import math
    cx, cy = r.center().x(), r.center().y() + r.height() * 0.04
    outer, inner = min(r.width(), r.height()) / 2, min(r.width(), r.height()) / 2 * 0.45
    path = QPainterPath()
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rad = outer if i % 2 == 0 else inner
        pt = (cx + rad * math.cos(a), cy + rad * math.sin(a))
        path.moveTo(*pt) if i == 0 else path.lineTo(*pt)
    path.closeSubpath()
    return path


def paint_star(p: QPainter, r: QRectF, filled: bool = True, colour: QColor = FAVOURITE) -> None:
    """The favourite badge: a gold star with a dark rim, readable on any image."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    path = star_path(r)
    p.setPen(QPen(QColor(0, 0, 0, 170), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    p.setPen(QPen(colour, 1.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(colour if filled else Qt.NoBrush)
    p.drawPath(path)
    p.restore()


def star_icon(filled: bool, colour: QColor | None = None):
    """A star QIcon for buttons: filled gold, or an outline in ``colour``."""
    from PySide6.QtGui import QIcon, QPixmap
    pix = QPixmap(32, 32)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    path = star_path(QRectF(3, 3, 26, 26))
    if filled:
        p.setPen(QPen(FAVOURITE.darker(150), 1.5))
        p.setBrush(FAVOURITE)
    else:
        p.setPen(QPen(colour or QColor("#999999"), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    p.end()
    return QIcon(pix)
TYPE_NAMES = {"image": "Image", "gif": "GIF", "movie": "Movie"}
SEP = " · "                       # between a list row's details, as in the motion clips names


def category_label(item: GalleryItem) -> str:
    """The item's category path under its top level, as the tree shows it:
    "Asphalt / Rough" for a surface in surface > asphalt > rough."""
    from .tree import display_name, item_paths
    paths = item_paths(item)
    if not paths:
        return ""
    segs = [s for s in paths[0][1:] if s and s != "other"]
    return " / ".join(display_name(s, depth) for depth, s in enumerate(segs, start=1))


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
        self.favourites: set[str] = set()                # item ids with the star
        self.media_badges = True                         # the MOV / GIF corner labels
        self.name_format = None                          # see ui/names.py
        self.subtitles = "always"                        # "always" | "when_needed" | "never"
        self.mode = "grid"                               # "grid" (tiles) or "list" (rows)
        self.labels = "always"                           # tiles: "always" or "hover" (Bridge style)
        self.list_details = True                         # list rows: the category path
        self.list_extras = None                          # list rows: (item, shared) -> [str]
        self.row_height = 33                             # list mode
        self.shared_titles: set[str] = set()             # titles used by more than one item
        self.set_theme(theme.ILP)

    def set_theme(self, t: theme.Theme) -> None:
        self._tile = t.c("tile")
        self._tile_hover = t.c("tile_hover")
        self._image_bg = t.c("image_bg")
        self._accent = t.c("accent")
        self._text = t.c("text")
        self._muted = t.c("muted")
        self._bevel = t.tile_bevel()          # SGI themes: Motif-bevelled square tiles
        # list rows take the list panes' colours (the tree, the drawer)
        self._list_text = t.list_color("text")
        self._list_muted = t.list_color("muted")
        self._list_sel_bg, self._list_sel_text = t.list_selection()
        hover = QColor(self._list_text)
        hover.setAlpha(28)
        self._list_hover = hover

    # -- geometry ------------------------------------------------------
    def image_height(self) -> int:
        return int(self.tile_width / self.aspect)

    def sizeHint(self, option, index) -> QSize:
        if self.mode == "list":
            return QSize(200, self.row_height)        # rows span the view; only the height counts
        label_h = 0 if self.labels == "hover" else self.LABEL_H   # hover: image-only tiles
        return QSize(self.tile_width + 2 * (self.MARGIN + self.PAD),
                     self.image_height() + label_h + 2 * (self.MARGIN + self.PAD))

    def card_rect(self, cell: QRect) -> QRect:
        return cell.adjusted(self.MARGIN, self.MARGIN, -self.MARGIN, -self.MARGIN)

    def image_rect(self, cell: QRect) -> QRect:
        if self.mode == "list":                       # the row's thumbnail, on the left
            h = max(8, cell.height() - 6)
            return QRect(cell.x() + 6, cell.y() + 3, int(round(h * self.aspect)), h)
        card = self.card_rect(cell)
        return QRect(card.x() + self.PAD, card.y() + self.PAD,
                     card.width() - 2 * self.PAD, self.image_height())

    # -- painting ------------------------------------------------------
    def paint(self, p: QPainter, option, index) -> None:
        item: GalleryItem = index.data(ItemRole)
        if item is None:
            return
        if self.mode == "list":
            self._paint_row(p, option, item)
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

        badge = BADGE.get(item.motion[1] if moves else item.type) if self.media_badges else None
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
        if item.id in self.favourites:                    # top right, beside the drawer mark
            d = max(13, min(20, img_rect.height() // 6))
            right = img_rect.right() - 5 - (d + 6 if mark is not None else 0)
            paint_star(p, QRectF(right - d, img_rect.top() + 5, d, d))

        parts = format_name(self.name_format, item)
        if parts.chip:
            paint_chip(p, parts, QPoint(img_rect.left() + 5, img_rect.top() + 5), option.font)

        if self.labels == "hover":
            if hovered or selected:                       # the name over the image, as in Bridge
                self._paint_overlay_label(p, option, item, parts, img_rect)
            if self._bevel:
                self._paint_bevel(p, option.rect, selected)
            elif selected:
                p.setPen(QPen(self._accent, 2))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(card.adjusted(1, 1, -1, -1), self.RADIUS, self.RADIUS)
            p.restore()
            return

        # name + subtitle
        name_font = QFont(option.font)
        name_font.setWeight(QFont.DemiBold)
        sub_font = QFont(option.font)
        sub_font.setPointSizeF(max(7.0, option.font.pointSizeF() * 0.85))
        nfm, sfm = QFontMetrics(name_font), QFontMetrics(sub_font)
        x, w = img_rect.left() + 1, img_rect.width() - 2
        y = img_rect.bottom() + 5
        title, overflow = parts.title, ""
        if parts.wrap:                                   # the rest of a long title goes on line two
            title, overflow = split_title(parts.title, lambda t: nfm.horizontalAdvance(t) <= w)
        p.setFont(name_font)
        p.setPen(self._text)
        p.drawText(QRect(x, y, w, nfm.height()), Qt.AlignLeft | Qt.AlignVCenter,
                   nfm.elidedText(title, Qt.ElideRight, w))
        p.setFont(sub_font)
        p.setPen(self._muted)
        if overflow:
            sub = overflow
            if (parts.subtitle and self.subtitles != "never"
                    and (self.subtitles == "always" or parts.title in self.shared_titles)):
                sub = f"{overflow} · {parts.subtitle}"   # still tell shared titles apart
        else:
            sub = parts.subtitle if parts.subtitle is not None else subtitle(item)
            if self.subtitles == "never" or (self.subtitles == "when_needed"
                                             and parts.title not in self.shared_titles):
                sub = ""                                 # the title alone tells it apart
        p.drawText(QRect(x, y + nfm.height(), w, sfm.height()), Qt.AlignLeft | Qt.AlignVCenter,
                   sfm.elidedText(sub, Qt.ElideRight, w))

        if self._bevel:
            self._paint_bevel(p, option.rect, selected)
        elif selected:
            p.setPen(QPen(self._accent, 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(card.adjusted(1, 1, -1, -1), self.RADIUS, self.RADIUS)
        p.restore()

    def _paint_overlay_label(self, p: QPainter, option, item: GalleryItem, parts, img: QRect) -> None:
        """Hover labels: the name (and its second line when there's room) on a dark
        band along the bottom of the image."""
        name_font = QFont(option.font)
        name_font.setWeight(QFont.DemiBold)
        sub_font = QFont(option.font)
        sub_font.setPointSizeF(max(7.0, option.font.pointSizeF() * 0.85))
        nfm, sfm = QFontMetrics(name_font), QFontMetrics(sub_font)
        sub = parts.subtitle if parts.subtitle is not None else subtitle(item)
        if self.subtitles == "never" or img.height() < 3 * (nfm.height() + sfm.height()):
            sub = ""                                      # small tiles: the name alone
        band_h = nfm.height() + (sfm.height() if sub else 0) + 8
        band = QRect(img.left(), img.bottom() - band_h + 1, img.width(), band_h)
        p.save()
        p.setClipRect(img)
        g = QLinearGradient(band.topLeft(), band.bottomLeft())
        g.setColorAt(0, QColor(0, 0, 0, 120))
        g.setColorAt(1, QColor(0, 0, 0, 200))
        p.fillRect(band, g)
        x, w = band.left() + 6, band.width() - 12
        p.setFont(name_font)
        p.setPen(QColor(245, 245, 245))
        p.drawText(QRect(x, band.top() + 4, w, nfm.height()), Qt.AlignLeft | Qt.AlignVCenter,
                   nfm.elidedText(parts.title, Qt.ElideRight, w))
        if sub:
            p.setFont(sub_font)
            p.setPen(QColor(200, 200, 200))
            p.drawText(QRect(x, band.top() + 4 + nfm.height(), w, sfm.height()), Qt.AlignLeft | Qt.AlignVCenter,
                       sfm.elidedText(sub, Qt.ElideRight, w))
        p.restore()

    def _thumb(self, item: GalleryItem, hovered: bool):
        """The pixmap to show: the hover-scrub frame for moving media, else the poster."""
        if hovered and item.motion is not None:
            frames = self.cache.frames(item)
            if frames:
                return frames[min(len(frames) - 1, int(self.hover_fraction * len(frames)))]
        return self.cache.poster(item)

    def _paint_row(self, p: QPainter, option, item: GalleryItem) -> None:
        """List mode, like the drawer's rows: a small thumbnail (hover-scrubbed for
        movies and GIFs), the name and its second line, the media badge and the
        drawer mark on the right."""
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        row = option.rect
        selected = bool(option.state & QStyle.State_Selected)
        hovered = item.id == self.hover_id
        if selected:
            p.fillRect(row, self._list_sel_bg)
        elif hovered:
            p.fillRect(row, self._list_hover)
        text = self._list_sel_text if selected else self._list_text
        muted = self._list_sel_text if selected else self._list_muted

        img = self.image_rect(row)
        p.fillRect(img, self._image_bg)
        pix = self._thumb(item, hovered)
        if pix is not None and not pix.isNull():
            scaled = pix.size().scaled(img.size(), Qt.KeepAspectRatio)
            target = QRect(0, 0, scaled.width(), scaled.height())
            target.moveCenter(img.center())
            p.drawPixmap(target, pix)
        elif self.cache.failed(item.id):
            self._paint_placeholder(p, img)
        moves = item.motion is not None
        if hovered and moves:
            p.fillRect(QRect(img.left(), img.bottom() - 1, int(img.width() * self.hover_fraction), 2), self._accent)
        if self._bevel:                                   # SGI: the thumbnail sits in a sunken well
            hl, _hl2, _sh2, sh = self._bevel
            p.setRenderHint(QPainter.Antialiasing, False)
            fr = img.adjusted(-1, -1, 0, 0)
            p.setPen(QPen(sh, 1))
            p.drawLine(fr.topLeft(), fr.topRight())
            p.drawLine(fr.topLeft(), fr.bottomLeft())
            p.setPen(QPen(hl, 1))
            p.drawLine(fr.bottomLeft(), fr.bottomRight())
            p.drawLine(fr.topRight(), fr.bottomRight())
            p.setRenderHint(QPainter.Antialiasing)

        parts = format_name(self.name_format, item)
        right = row.right() - 8
        mark = self.marks.get(item.id)                    # drawer mark, at the far right
        if mark is not None:
            d = max(12, min(18, row.height() - 12))
            self._paint_mark(p, QRect(right - d - 5 + 5, row.center().y() - d // 2 - 5, d + 5, d + 10), *mark)
            right -= d + 8
        if item.id in self.favourites:
            d = 14
            paint_star(p, QRectF(right - d, row.center().y() - d / 2, d, d))
            right -= d + 8
        badge = BADGE.get(item.motion[1] if moves else item.type) if self.media_badges else None
        small = QFont(option.font)
        small.setPointSizeF(max(7.0, option.font.pointSizeF() * 0.85))
        sfm = QFontMetrics(small)
        if badge:
            p.setFont(small)
            p.setPen(muted)
            w = sfm.horizontalAdvance(badge)
            p.drawText(QRect(right - w, row.top(), w, row.height()), Qt.AlignRight | Qt.AlignVCenter, badge)
            right -= w + 10

        x = img.right() + 10
        if parts.chip:
            x += paint_chip(p, parts, QPoint(x, row.center().y() - sfm.height() // 2 - 1), option.font) + 6
        name_font = QFont(option.font)
        name_font.setWeight(QFont.DemiBold)
        nfm = QFontMetrics(name_font)
        sub = parts.subtitle if parts.subtitle is not None else subtitle(item)
        if self.subtitles == "never" or (self.subtitles == "when_needed" and parts.title not in self.shared_titles):
            sub = ""
        shared = parts.title in self.shared_titles
        rest = []
        if self.list_details:                             # the category path, minus what the name says
            said = f"{parts.title} {sub}".lower()
            path = category_label(item)
            if path and path.lower() not in said:
                rest.append(path)
        if sub:
            rest.append(sub)
        if self.list_extras is not None:
            try:
                rest += [str(x) for x in self.list_extras(item, shared) if x]
            except Exception:                             # a host's helper must never break painting
                pass
        sub = SEP.join(rest)
        avail = max(0, right - x)
        p.setFont(name_font)
        p.setPen(text)
        two_lines = bool(sub) and row.height() >= nfm.height() + sfm.height() + 8
        if two_lines:                                     # taller rows: the subtitle under the name
            top = row.center().y() - (nfm.height() + sfm.height()) // 2
            p.drawText(QRect(x, top, avail, nfm.height()), Qt.AlignLeft | Qt.AlignVCenter,
                       nfm.elidedText(parts.title, Qt.ElideRight, avail))
            p.setFont(small)
            p.setPen(muted)
            p.drawText(QRect(x, top + nfm.height(), avail, sfm.height()), Qt.AlignLeft | Qt.AlignVCenter,
                       sfm.elidedText(sub, Qt.ElideRight, avail))
        else:                                             # compact rows: on one line, like the drawer
            title = nfm.elidedText(parts.title, Qt.ElideRight, avail)
            p.drawText(QRect(x, row.top(), avail, row.height()), Qt.AlignLeft | Qt.AlignVCenter, title)
            x2 = x + nfm.horizontalAdvance(title)
            if sub and x2 < right - 30:
                p.setFont(small)
                p.setPen(muted)
                line = SEP + sub                          # "Sandy Asphalt · Asphalt / Rough · Surface"
                p.drawText(QRect(x2, row.top(), right - x2, row.height()), Qt.AlignLeft | Qt.AlignVCenter,
                           sfm.elidedText(line, Qt.ElideRight, right - x2))
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
        disabled; green when the drawer's path is current, orange when it's outdated.
        Fixed colours, not the theme accent: hosts like Houdini have an orange accent."""
        d = max(12, min(20, img_rect.height() // 6))
        r = QRectF(img_rect.right() - d - 5, img_rect.top() + 5, d, d)
        color = MARK_OUTDATED if state == "outdated" else MARK_OK
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
