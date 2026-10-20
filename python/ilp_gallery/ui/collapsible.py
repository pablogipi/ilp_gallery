"""Collapsible panes, in the style of Maya's side panels.

A :class:`PaneArea` lays panes out left to right: panes on the left, a centre
pane, and a right column where the right-hand panes share the height. Expanded,
a pane's title sits in a tab with a small collapse arrow (double-click the tab
also collapses). Collapsed, the pane leaves the layout and its tab stands
vertically in a strip on its side of the area; collapsed panes on the same side
stack their tabs in one strip, top to bottom. Clicking a tab expands the pane at
its previous size. A drag held over a tab expands it ("spring loading"), so a
drop can land in the pane.

    area = PaneArea()
    area.add_pane("tree", CollapsiblePane(tree, "Categories"), "left", 270)
    area.add_pane("grid", CollapsiblePane(view, "Assets"), "center", 860)
    area.add_pane("drawer", CollapsiblePane(drawer, "Clips", edge="right"), "right", 300)
    area.set_collapsed("drawer", True)
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF,
)
from PySide6.QtWidgets import (
    QHBoxLayout, QSizePolicy, QSplitter, QSplitterHandle, QVBoxLayout, QWidget,
)

STRIP = 22              # width of a collapsed pane
QWIDGETSIZE_MAX = (1 << 24) - 1


class _Colors:
    """Tab colours, shared by a pane's header tab and its collapsed tab."""

    def __init__(self) -> None:
        self.italic = False                       # titles in italics (SGI themes)
        self.bold = False                         # and bold: SGI tabs are bold italic
        self.sgi: tuple[QColor, QColor, QColor, QColor] | None = None  # (hilite, shadow, face, outline): IRIX tabs
        self.set(QColor("#2b303b"), QColor("#363c4a"), QColor("#363b47"), QColor("#d9dce3"),
                 QColor("#8a909c"), QColor("#1e2129"))

    def set(self, tab, hover, border, text, muted, strip) -> None:
        self.tab, self.hover, self.border, self.text, self.muted, self.strip = (
            QColor(tab), QColor(hover), QColor(border), QColor(text), QColor(muted), QColor(strip))
        # Host themes (Houdini) can give tabs the same grey as the strip behind them,
        # leaving only an outline: lift the tab towards the text colour until it reads.
        if _distance(self.tab, self.strip) < 18:
            self.tab = _mix(self.tab, self.text, 0.12)
        if _distance(self.hover, self.tab) < 12:
            self.hover = _mix(self.tab, self.text, 0.12)


# -- IRIX notebook tabs ---------------------------------------------------------------
# A trapezoid, wide where it joins its pane and narrow at the free end, lit from the
# top left (Motif bevel colours), and a few hatch lines where a row of tabs starts.
# Shared by the pane headers and the collapsed tabs; also meant for future tab rows.
SGI_SLANT = 6.0                                   # how far a side leans over the tab's depth
SGI_FLARE = 2.5                                   # the sides curve out into the pane's edge
SGI_ROUND = 5.0                                   # radius of the free end's corners
SGI_BLEND = 7.0                                   # how far the light-to-shadow blend reaches past the corner
SGI_SIDE = SGI_SLANT + SGI_FLARE                  # room a side takes, for laying out the label
SGI_HATCH = 14.0                                  # room for the hatch before the first tab

# per side joined to the pane: the colours of (first side, free end, second side),
# lit from the top left; True = hilite
_LIT = {"bottom": (True, True, False), "top": (True, False, False),
        "right": (True, True, False), "left": (True, False, False)}


def _tab_frame(r: QRectF, wide: str):
    """(length along the pane edge, depth, map(u, v) -> QPointF) for a tab in ``r``:
    u runs along the joined side, v from it to the free end."""
    if wide in ("bottom", "top"):
        y0, sign = (r.bottom(), -1) if wide == "bottom" else (r.top(), 1)
        return r.width(), r.height(), lambda u, v: QPointF(r.left() + u, y0 + sign * v)
    x0, sign = (r.right(), -1) if wide == "right" else (r.left(), 1)
    return r.height(), r.width(), lambda u, v: QPointF(x0 + sign * v, r.top() + u)


def _tab_edges(r: QRectF, wide: str, slant: float = SGI_SLANT, flare: float = SGI_FLARE,
               radius: float = SGI_ROUND) -> list[QPainterPath]:
    """The free edges of a tab as paths, in order: first side (with its flare),
    its rounded corner, the free end, the other corner, the second side. Joined,
    they outline the tab."""
    length, depth, at = _tab_frame(r, wide)
    f = min(flare, depth / 3)                      # depth the flare takes
    rad = min(radius, depth * 0.45, length / 5)
    ratio = slant / depth if depth else 0

    def side_x(v):                                 # the first side's u at depth v
        return flare + ratio * v

    # each side is an S: a concave foot flaring into the pane's edge, a lean, and a
    # wide rounded corner into the free end (cubic curves, as IRIX's tabs read)
    k = 0.55                                       # circle-ish cubic handles
    first = QPainterPath(at(0, 0))
    first.cubicTo(at(flare * k, 0), at(side_x(f) - ratio * f * k, f * (1 - k)), at(side_x(f), f))
    first.lineTo(at(side_x(depth - rad), depth - rad))
    corner1 = QPainterPath(at(side_x(depth - rad), depth - rad))
    corner1.cubicTo(at(side_x(depth - rad * (1 - k)), depth - rad * (1 - k)),
                    at(side_x(depth) + rad * (1 - k), depth), at(side_x(depth) + rad, depth))
    end = QPainterPath(at(side_x(depth) + rad, depth))
    end.lineTo(at(length - side_x(depth) - rad, depth))
    corner2 = QPainterPath(at(length - side_x(depth) - rad, depth))
    corner2.cubicTo(at(length - side_x(depth) - rad * (1 - k), depth),
                    at(length - side_x(depth - rad * (1 - k)), depth - rad * (1 - k)),
                    at(length - side_x(depth - rad), depth - rad))
    second = QPainterPath(at(length - side_x(depth - rad), depth - rad))
    second.lineTo(at(length - side_x(f), f))
    second.cubicTo(at(length - side_x(f) + ratio * f * k, f * (1 - k)), at(length - flare * k, 0),
                   at(length, 0))
    return [first, corner1, end, corner2, second]


def _inset(r: QRectF, wide: str, d: float) -> QRectF:
    """``r`` pulled in by ``d`` on every side but the joined one."""
    return {"bottom": r.adjusted(d, d, -d, 0), "top": r.adjusted(d, 0, -d, -d),
            "right": r.adjusted(d, d, 0, -d), "left": r.adjusted(0, d, -d, -d)}[wide]


def sgi_tab_path(r: QRectF, wide: str, slant: float = SGI_SLANT) -> QPainterPath:
    """The tab's closed outline: wide where it joins its pane (``wide``: "bottom",
    "top", "left" or "right"), narrowing to a free end with rounded corners."""
    pieces = _tab_edges(r, wide, slant)
    path = QPainterPath(pieces[0])
    for piece in pieces[1:]:
        path.connectPath(piece)
    path.closeSubpath()
    return path


def paint_sgi_tab(p: QPainter, r: QRectF, wide: str, face: QColor, hilite: QColor, shadow: QColor,
                  current: bool = False, outline: QColor | None = None) -> None:
    """Fill and bevel one tab: a dark outline (``outline``) around the free edges
    with the light / dark bevel just inside it, as IRIX draws them. The joined side
    gets no edge, so the tab reads as part of its pane; the current tab's bevel
    is heavier."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(face)
    p.drawPath(sgi_tab_path(r, wide))
    p.setBrush(Qt.NoBrush)
    cap = (Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    if outline is not None:
        p.setPen(QPen(outline, 1.0, *cap))
        for edge in _tab_edges(r, wide):
            p.drawPath(edge)
        r = _inset(r, wide, 1.0)
    w = 1.4 if current else 1.0
    first, corner1, end, corner2, second = _tab_edges(r, wide)
    lit1, lit_end, lit2 = (hilite if on else shadow for on in _LIT[wide])
    # Where the light turns into shadow (one corner of every tab), the bevel colour
    # blends gradually: from a little before that corner to a little after it,
    # instead of switching at a point. The gradient pads, so the rest of each edge
    # keeps its plain colour.
    if lit1 != lit_end:
        before, corner, after, c0, c1 = first, corner1, end, lit1, lit_end
        plain = [(second, lit2), (corner2, lit2)]
    else:
        before, corner, after, c0, c1 = end, corner2, second, lit_end, lit2
        plain = [(first, lit1), (corner1, lit1)]
    reach = SGI_BLEND

    def along(path, dist):                         # point ``dist`` along ``path`` (clamped)
        length = path.length()
        return path.pointAtPercent(path.percentAtLength(max(0.0, min(length, dist))))

    start = along(before, before.length() - reach)
    stop = along(after, reach)
    for edge, colour in plain:
        p.setPen(QPen(colour, w, *cap))
        p.drawPath(edge)
    if c0 == c1:
        pen = QPen(c0, w, *cap)
    else:
        g = QLinearGradient(start, stop)
        g.setColorAt(0, c0)
        g.setColorAt(1, c1)
        pen = QPen(QBrush(g), w, *cap)
    p.setPen(pen)
    for edge in (before, corner, after):
        p.drawPath(edge)
    p.restore()


def paint_sgi_hatch(p: QPainter, x: float, top: float, bottom: float, hilite: QColor, shadow: QColor,
                    slant: float = SGI_SLANT, lines: int = 3) -> None:
    """IRIX's hatch where a tab row starts: short lines parallel to the tab's side."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    for i in range(lines):
        x0 = x + i * 4
        p.setPen(QPen(shadow, 1))
        p.drawLine(QPointF(x0, bottom), QPointF(x0 + slant, top))
        p.setPen(QPen(hilite, 1))
        p.drawLine(QPointF(x0 + 1, bottom), QPointF(x0 + 1 + slant, top))
    p.restore()


def _title_font(widget: QWidget, colors: _Colors) -> QFont:
    f = QFont(widget.font())
    f.setItalic(colors.italic)
    if colors.bold:
        f.setWeight(QFont.DemiBold)               # same weight as the SGI menus' bold italic
    return f


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(round(a.red() + (b.red() - a.red()) * t), round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t))


def _distance(a: QColor, b: QColor) -> int:
    return abs(a.red() - b.red()) + abs(a.green() - b.green()) + abs(a.blue() - b.blue())


def _arrow(p: QPainter, center: QPointF, size: float, direction: str, color: QColor) -> None:
    """A small filled triangle pointing "left", "right", "up" or "down"."""
    h = size / 2
    pts = {
        "left": [QPointF(h * 0.6, -h), QPointF(h * 0.6, h), QPointF(-h * 0.6, 0)],
        "right": [QPointF(-h * 0.6, -h), QPointF(-h * 0.6, h), QPointF(h * 0.6, 0)],
        "up": [QPointF(-h, h * 0.6), QPointF(h, h * 0.6), QPointF(0, -h * 0.6)],
        "down": [QPointF(-h, -h * 0.6), QPointF(h, -h * 0.6), QPointF(0, h * 0.6)],
    }[direction]
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawPolygon(QPolygonF([center + q for q in pts]))
    p.restore()


def _tab_path(r: QRectF, radius: float, open_side: str) -> QPainterPath:
    """A tab outline: rounded on three sides, open (square) on ``open_side``."""
    path = QPainterPath()
    path.addRoundedRect(r, radius, radius)
    square = QPainterPath()
    if open_side == "bottom":
        square.addRect(QRectF(r.left(), r.center().y(), r.width(), r.height() / 2))
    elif open_side == "left":
        square.addRect(QRectF(r.left(), r.top(), r.width() / 2, r.height()))
    else:  # right
        square.addRect(QRectF(r.center().x(), r.top(), r.width() / 2, r.height()))
    return path.united(square)


class PaneHeader(QWidget):
    """An expanded pane's title, drawn as a tab, with a small collapse arrow in it.
    Clicking the arrow (or double-clicking the tab) collapses the pane."""
    collapseRequested = Signal()
    HEIGHT = 24
    ARROW_BOX = 18

    def __init__(self, title: str, edge: str, colors: _Colors, parent=None) -> None:
        super().__init__(parent)
        self._title = title
        self._edge = edge
        self._colors = colors
        self._hover_arrow = False
        self.setFixedHeight(self.HEIGHT)
        self.setMouseTracking(True)
        self.setToolTip("")

    def set_title(self, title: str) -> None:
        self._title = title
        self.update()

    def tab_rect(self) -> QRectF:
        fm = QFontMetrics(_title_font(self, self._colors))
        x, extra = (4 + SGI_HATCH, 2 * SGI_SIDE) if self._colors.sgi else (4, 0)
        w = min(self.width() - x - 4, fm.horizontalAdvance(self._title) + 22 + self.ARROW_BOX + extra)
        return QRectF(x, 3, max(w, self.ARROW_BOX + 10 + extra), self.HEIGHT - 3)

    def arrow_rect(self) -> QRectF:
        t = self.tab_rect()
        inset = SGI_SIDE if self._colors.sgi else 0
        return QRectF(t.right() - inset - self.ARROW_BOX - 2, t.top() + (t.height() - self.ARROW_BOX) / 2,
                      self.ARROW_BOX, self.ARROW_BOX)

    def paintEvent(self, _event) -> None:
        c = self._colors
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        tab = self.tab_rect()
        inset = 0.0
        if c.sgi:
            hilite, shadow, face, outline = c.sgi
            base = self.height() - 0.5
            p.setPen(QPen(hilite, 1))                  # the pane's raised top edge, broken by the tab
            p.drawLine(QPointF(0, base), QPointF(tab.left(), base))
            p.drawLine(QPointF(tab.right(), base), QPointF(self.width(), base))
            paint_sgi_hatch(p, 4, tab.top() + 4, base, hilite, shadow)
            paint_sgi_tab(p, tab.adjusted(0.5, 0.5, -0.5, 0.5), "bottom", face, hilite, shadow, current=True,
                          outline=outline)
            inset = SGI_SIDE
        else:
            p.setPen(QPen(c.border, 1))
            p.drawLine(QPointF(0, self.height() - 0.5), QPointF(self.width(), self.height() - 0.5))
            path = _tab_path(tab.adjusted(0.5, 0.5, -0.5, 0.5), 4, "bottom")
            p.fillPath(path, c.tab)
            p.drawPath(path)
        p.setPen(c.text)
        font = _title_font(self, c)
        p.setFont(font)
        text_r = QRectF(tab.left() + inset + 10, tab.top(), tab.width() - 2 * inset - self.ARROW_BOX - 14,
                        tab.height())
        p.drawText(text_r, Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(font).elidedText(self._title, Qt.ElideRight, int(text_r.width())))
        a = self.arrow_rect()
        if self._hover_arrow:
            p.setPen(Qt.NoPen)
            p.setBrush(c.hover)
            p.drawRoundedRect(a, 3, 3)
        _arrow(p, a.center(), 8, {"left": "left", "right": "right", "bottom": "down"}[self._edge],
               c.text if self._hover_arrow else c.muted)
        p.end()

    def mouseMoveEvent(self, event) -> None:
        over = self.arrow_rect().contains(event.position())
        if over != self._hover_arrow:
            self._hover_arrow = over
            self.setCursor(Qt.PointingHandCursor if over else Qt.ArrowCursor)
            self.setToolTip(f"Collapse {self._title}" if over else "")
            self.update()

    def leaveEvent(self, event) -> None:
        self._hover_arrow = False
        self.unsetCursor()
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.arrow_rect().contains(event.position()):
            self.collapseRequested.emit()

    def mouseDoubleClickEvent(self, event) -> None:
        if self.tab_rect().contains(event.position()):
            self.collapseRequested.emit()


class VerticalTab(QWidget):
    """A collapsed pane's tab in a :class:`DockStrip`: the title top to bottom (like
    Maya's side tabs) under an arrow pointing the way the pane opens. Click to expand;
    hold a drag over it to expand it too.

    A plain QWidget on purpose, not a button: host stylesheets (Houdini's) give
    buttons a minimum width and padding, which made the tab wider than its strip
    and cut it in half."""
    clicked = Signal()

    def __init__(self, text: str, edge: str = "left", colors: _Colors | None = None, parent=None) -> None:
        super().__init__(parent)
        self._text = ""
        self._down = False
        self._edge = edge
        self._colors = colors or _Colors()              # before setText: it sizes the tab
        self.setText(text)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedWidth(STRIP)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_Hover)
        self.setAcceptDrops(True)
        self._spring = QTimer(self, singleShot=True, interval=350)
        self._spring.timeout.connect(self.click)

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:
        self._text = text
        self.setFixedHeight(self.sizeHint().height())
        self.update()

    def click(self) -> None:
        self.clicked.emit()

    def isDown(self) -> bool:
        return self._down

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._down = True
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._down:
            self._down = False
            self.update()
            if self.rect().contains(event.position().toPoint()):
                self.clicked.emit()

    def enterEvent(self, event) -> None:
        self.update()

    def leaveEvent(self, event) -> None:
        self.update()

    def sizeHint(self) -> QSize:
        extra = int(2 * SGI_SIDE) if self._colors.sgi else 0
        return QSize(STRIP, QFontMetrics(_title_font(self, self._colors)).horizontalAdvance(self.text()) + 36 + extra)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _event) -> None:
        c = self._colors
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        hover = self.underMouse() or self.isDown()
        # a closed rounded tab, inset from both sides of the strip (an open side read
        # as a tab cut in half where the tab and strip colours are close)
        tab = QRectF(2.5, 0.5, self.width() - 5, self.height() - 1)
        if c.sgi:          # an IRIX tab, wide on the side the pane opens to
            hilite, shadow, _face, outline = c.sgi
            paint_sgi_tab(p, tab, "right" if self._edge == "left" else "left",
                          c.hover if hover else c.tab, hilite, shadow, outline=outline)
        else:
            path = QPainterPath()
            path.addRoundedRect(tab, 4, 4)
            p.setPen(QPen(c.border, 1))
            p.fillPath(path, c.hover if hover else c.tab)
            p.drawPath(path)
        _arrow(p, QPointF(tab.center().x(), tab.top() + 11 + (SGI_SIDE / 2 if c.sgi else 0)), 8,
               "right" if self._edge == "left" else "left", c.text if hover else c.muted)
        p.setPen(c.text)
        p.translate(tab.right(), 0)
        p.rotate(90)                                   # reads top to bottom
        inset = SGI_SIDE if c.sgi else 0
        avail = int(tab.height() - 28 - 2 * inset)
        font = _title_font(self, c)
        p.setFont(font)
        text = QFontMetrics(font).elidedText(self.text(), Qt.ElideRight, avail)
        p.drawText(QRectF(tab.top() + 22 + inset, 0, avail, tab.width()), Qt.AlignLeft | Qt.AlignVCenter, text)
        p.end()

    # a drag over the tab: accepted as "no drop" so the tab hears when it leaves
    def dragEnterEvent(self, event) -> None:
        event.setDropAction(Qt.IgnoreAction)
        event.accept()
        self._spring.start()

    def dragMoveEvent(self, event) -> None:
        event.setDropAction(Qt.IgnoreAction)
        event.accept()

    def dragLeaveEvent(self, event) -> None:
        self._spring.stop()

    def dropEvent(self, event) -> None:
        self._spring.stop()
        event.setDropAction(Qt.IgnoreAction)
        event.accept()


class HorizontalTab(VerticalTab):
    """A collapsed bottom pane's tab: an up arrow and the title, read normally."""

    def __init__(self, text: str, edge: str = "bottom", colors: _Colors | None = None, parent=None) -> None:
        super().__init__(text, edge, colors, parent)
        self.setText(text)                             # fixes the size the base class set

    def setText(self, text: str) -> None:
        self._text = text
        self.setFixedSize(self.sizeHint())
        self.update()

    def sizeHint(self) -> QSize:
        extra = int(2 * SGI_SIDE) if self._colors.sgi else 0
        return QSize(QFontMetrics(_title_font(self, self._colors)).horizontalAdvance(self.text()) + 36 + extra, STRIP)

    def paintEvent(self, _event) -> None:
        c = self._colors
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        hover = self.underMouse() or self.isDown()
        tab = QRectF(0.5, 2.5, self.width() - 1, self.height() - 5)
        inset = 0.0
        if c.sgi:          # hangs below the panes, like IRIX's bottom notebook tabs
            hilite, shadow, _face, outline = c.sgi
            tab = QRectF(0.5, 0.5, self.width() - 1, self.height() - 3)
            paint_sgi_tab(p, tab, "top", c.hover if hover else c.tab, hilite, shadow, outline=outline)
            inset = SGI_SIDE
        else:
            path = QPainterPath()
            path.addRoundedRect(tab, 4, 4)
            p.setPen(QPen(c.border, 1))
            p.fillPath(path, c.hover if hover else c.tab)
            p.drawPath(path)
        _arrow(p, QPointF(tab.left() + inset + 11, tab.center().y()), 8, "up", c.text if hover else c.muted)
        p.setPen(c.text)
        avail = int(tab.width() - 28 - 2 * inset)
        font = _title_font(self, c)
        p.setFont(font)
        p.drawText(QRectF(tab.left() + inset + 22, tab.top(), avail, tab.height()), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(font).elidedText(self.text(), Qt.ElideRight, avail))
        p.end()


class DockStrip(QWidget):
    """The narrow bar on one side of a :class:`PaneArea` holding the tabs of the
    collapsed panes on that side: stacked top to bottom on the left and right,
    side by side along the bottom. Hidden when empty."""

    def __init__(self, edge: str, colors: _Colors, parent=None) -> None:
        super().__init__(parent)
        self._edge = edge
        self._colors = colors
        if edge == "bottom":
            self.setFixedHeight(STRIP)
            self._lay = QHBoxLayout(self)
            self._lay.setContentsMargins(6, 0, 6, 0)
        else:
            self.setFixedWidth(STRIP)
            self._lay = QVBoxLayout(self)
            # the tabs hang below the panes' tab baseline, level with the pane contents
            self._lay.setContentsMargins(0, PaneHeader.HEIGHT + 3, 0, 4)
        self._lay.setSpacing(3)
        self._lay.addStretch(1)
        self.hide()

    def add_tab(self, tab: VerticalTab) -> None:
        self._lay.insertWidget(self._lay.count() - 1, tab)

    def refresh(self) -> None:
        tabs = [self._lay.itemAt(i).widget() for i in range(self._lay.count() - 1)]
        self.setVisible(any(not t.isHidden() for t in tabs))

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        if self._edge == "bottom":
            p.fillRect(self.rect(), self._colors.strip)
            p.setPen(self._colors.border)
            p.drawLine(QPointF(0, 0.5), QPointF(self.width(), 0.5))
            p.end()
            return
        p.fillRect(self.rect().adjusted(0, PaneHeader.HEIGHT, 0, 0), self._colors.strip)
        p.setPen(self._colors.border)
        base = PaneHeader.HEIGHT - 0.5
        p.drawLine(QPointF(0, base), QPointF(self.width(), base))     # the baseline carries on
        x = self.width() - 0.5 if self._edge == "left" else 0.5
        p.drawLine(QPointF(x, base), QPointF(x, self.height()))       # the edge starts below it
        p.end()


class CollapsiblePane(QWidget):
    """A pane: its title tab (with the collapse arrow) above the content. Placed in
    a :class:`PaneArea`, which handles collapsing; ``tab`` is its collapsed tab."""
    collapsedChanged = Signal(bool)

    def __init__(self, content: QWidget, title: str, edge: str = "left", parent=None) -> None:
        """``edge``: the side the pane collapses to ("left", "right" or "bottom")."""
        super().__init__(parent)
        self.content = content
        self.edge = edge
        self._title = title
        self._collapsed = False
        self._enabled = True
        self.area: PaneArea | None = None
        self.colors = _Colors()
        self.header = PaneHeader(title, edge, self.colors)
        self.header.collapseRequested.connect(lambda: self.set_collapsed(True))
        self.tab = (HorizontalTab if edge == "bottom" else VerticalTab)(title, edge, self.colors)
        self.tab.setToolTip(f"Expand {title}")
        self.tab.clicked.connect(lambda: self.set_collapsed(False))
        self.tab.hide()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.header)
        lay.addWidget(content, 1)

    def title(self) -> str:
        return self._title

    def set_title(self, title: str) -> None:
        self._title = title
        self.header.set_title(title)
        self.tab.setText(title)
        self.tab.setToolTip(f"Expand {title}")

    def set_theme(self, t) -> None:
        """Tab colours (and italic titles for the SGI themes) from a gallery Theme."""
        self.colors.set(t.c("tile"), t.c("tile_hover"), t.c("border"), t.c("text"), t.c("muted"), t.c("panel"))
        self.colors.italic = bool(getattr(t, "ITALIC_TABS", False))
        self.colors.bold = bool(getattr(t, "BOLD_TABS", False))
        self.colors.sgi = ((t.c("hilite"), t.c("shadow"), t.c("bg"), t.c("ring"))
                           if getattr(t, "TAB_SHAPE", "rounded") == "sgi" else None)
        self.header.update()
        self.tab.setText(self.tab.text())          # italics change the tab's width

    def is_collapsed(self) -> bool:
        return self._collapsed

    def is_enabled(self) -> bool:
        """Part of the layout (expanded or collapsed), as opposed to removed."""
        return self._enabled

    def set_collapsed(self, collapsed: bool) -> None:
        if self.area is not None:
            self.area._set_collapsed(self, collapsed)
        else:
            self._collapsed = collapsed
            self.content.setVisible(not collapsed)
            self.collapsedChanged.emit(collapsed)


class PaneHandle(QSplitterHandle):
    """A splitter handle that belongs to the panes: between side-by-side panes it
    carries the title tabs' baseline across and draws the separator only below
    it, so no line cuts through the row of tabs. Between stacked panes it draws
    nothing: the lower pane's title tab is the separation."""

    def __init__(self, orientation, splitter: "PaneSplitter") -> None:
        super().__init__(orientation, splitter)
        self._colors = splitter.colors

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setPen(QPen(self._colors.border, 1))
        if self.orientation() == Qt.Horizontal:            # panes side by side
            base = PaneHeader.HEIGHT - 0.5
            p.drawLine(QPointF(0, base), QPointF(self.width(), base))
            x = self.width() / 2
            p.drawLine(QPointF(x, base), QPointF(x, self.height()))
        # stacked panes: nothing, the lower pane's title tab already separates them
        p.end()


class PaneSplitter(QSplitter):
    """A QSplitter whose handles are :class:`PaneHandle` (independent of the host's
    stylesheet, which in Houdini draws a groove the full height)."""

    def __init__(self, orientation, colors: _Colors, parent=None) -> None:
        self.colors = colors
        super().__init__(orientation, parent)
        self.setChildrenCollapsible(False)
        self.setHandleWidth(5)

    def createHandle(self) -> QSplitterHandle:
        return PaneHandle(self.orientation(), self)


def _main_index(splitter: QSplitter, skip: int = -1) -> int | None:
    """The visible child that grows and shrinks first: highest stretch, then widest."""
    sizes = splitter.sizes()
    cands = [i for i in range(splitter.count()) if i != skip and not splitter.widget(i).isHidden()]
    if not cands:
        return None
    return max(cands, key=lambda i: (_stretch(splitter, i), sizes[i]))


def _stretch(splitter: QSplitter, i: int) -> int:
    pol = splitter.widget(i).sizePolicy()
    return pol.horizontalStretch() if splitter.orientation() == Qt.Horizontal else pol.verticalStretch()


class PaneArea(QWidget):
    """Left panes | centre pane | right column, between two strips of collapsed tabs,
    above an optional bottom pane (full width) and its strip."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.colors = _Colors()
        self.panes: dict[str, CollapsiblePane] = {}
        self._where: dict[str, str] = {}
        self._saved: dict[int, int] = {}            # id(widget) -> size to restore
        self.splitter = PaneSplitter(Qt.Horizontal, self.colors)
        self.column = PaneSplitter(Qt.Vertical, self.colors)   # the right-hand panes share the height
        self._column_added = False
        self.left_strip = DockStrip("left", self.colors)
        self.right_strip = DockStrip("right", self.colors)
        self.bottom_strip = DockStrip("bottom", self.colors)
        self.row = QWidget()
        row = QHBoxLayout(self.row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.left_strip)
        row.addWidget(self.splitter, 1)
        row.addWidget(self.right_strip)
        self.vsplit = PaneSplitter(Qt.Vertical, self.colors)    # the row above bottom panes
        self.vsplit.addWidget(self.row)
        self.vsplit.setStretchFactor(0, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.vsplit, 1)
        lay.addWidget(self.bottom_strip)

    def add_pane(self, name: str, pane: CollapsiblePane, where: str = "left", size: int = 250,
                 stretch: int = 0) -> CollapsiblePane:
        """``where``: "left" (side by side, in order), "center", "right" (stacked in the
        right column, in order) or "bottom" (full width, under everything).
        ``size``: width, or height for right and bottom panes."""
        pane.area = self
        self.panes[name] = pane
        self._where[name] = where
        if where == "bottom":
            self.vsplit.addWidget(pane)
            self.vsplit.setStretchFactor(self.vsplit.indexOf(pane), 0)
            self.bottom_strip.add_tab(pane.tab)
        elif where == "right":
            if not self._column_added:
                self.splitter.addWidget(self.column)
                self._column_added = True
            self.column.addWidget(pane)
            self.column.setStretchFactor(self.column.indexOf(pane), stretch or 1)
            self.right_strip.add_tab(pane.tab)
        else:
            self.splitter.addWidget(pane)
            self.splitter.setStretchFactor(self.splitter.indexOf(pane), stretch)
            self.left_strip.add_tab(pane.tab)
        self._saved[id(pane)] = size
        return pane

    def set_sizes(self, widths: dict[str, int], column_width: int | None = None) -> None:
        """Initial widths of the left / centre panes and the right column."""
        sizes = []
        for i in range(self.splitter.count()):
            w = self.splitter.widget(i)
            if w is self.column:
                sizes.append(column_width or 300)
                self._saved[id(self.column)] = column_width or 300
            else:
                name = next(n for n, p in self.panes.items() if p is w)
                sizes.append(widths.get(name, 250))
        self.splitter.setSizes(sizes)
        self.column.setSizes([0 if self.column.widget(i).isHidden() else self._saved[id(self.column.widget(i))]
                              for i in range(self.column.count())])
        self.vsplit.setSizes([10000] + [0 if self.vsplit.widget(i).isHidden() else self._saved[id(self.vsplit.widget(i))]
                                        for i in range(1, self.vsplit.count())])

    def set_theme(self, t) -> None:
        self.colors.set(t.c("tile"), t.c("tile_hover"), t.c("border"), t.c("text"), t.c("muted"), t.c("panel"))
        for pane in self.panes.values():
            pane.set_theme(t)
        self.left_strip.update()
        self.right_strip.update()
        self.bottom_strip.update()
        for s in (self.splitter, self.column, self.vsplit):
            for i in range(s.count()):
                s.handle(i).update()

    # -- state -------------------------------------------------------------------------
    def set_collapsed(self, name: str, collapsed: bool = True) -> None:
        self._set_collapsed(self.panes[name], collapsed)

    def is_collapsed(self, name: str) -> bool:
        return self.panes[name].is_collapsed()

    def set_enabled(self, name: str, on: bool) -> None:
        """Add a pane to the layout or remove it (no tab). Removing doesn't collapse."""
        pane = self.panes[name]
        if pane._enabled == on:
            return
        pane._enabled = on
        if on and not pane._collapsed:
            self._show(pane)
        elif not on and not pane._collapsed:
            self._hide(pane)
        pane.tab.setVisible(on and pane._collapsed)
        self._refresh_strips()

    def _set_collapsed(self, pane: CollapsiblePane, collapsed: bool) -> None:
        if pane._collapsed == collapsed:
            return
        pane._collapsed = collapsed
        if pane._enabled:
            if collapsed:
                self._hide(pane)
            else:
                self._show(pane)
            pane.tab.setVisible(collapsed)
            self._refresh_strips()
        pane.collapsedChanged.emit(collapsed)

    # -- layout ------------------------------------------------------------------------
    def _refresh_strips(self) -> None:
        self.left_strip.refresh()
        self.right_strip.refresh()
        self.bottom_strip.refresh()

    def _hide(self, w: QWidget) -> None:
        """Take ``w`` out of its splitter, remembering its size; its space goes to the
        main neighbour. The right column goes too once it has nothing to show."""
        s = w.parentWidget()
        if not isinstance(s, QSplitter) or w.isHidden():
            return
        sizes = s.sizes()
        i = s.indexOf(w)
        if sizes[i] > 0:
            self._saved[id(w)] = sizes[i]
        w.hide()
        j = _main_index(s, skip=i)
        if j is not None:
            sizes[j] += sizes[i]
            sizes[i] = 0
            s.setSizes(sizes)
        if s is self.column and all(self.column.widget(k).isHidden() for k in range(self.column.count())):
            self._hide(self.column)

    def _show(self, w: QWidget) -> None:
        """Put ``w`` back at its remembered size, taken from the main neighbour."""
        s = w.parentWidget()
        if not isinstance(s, QSplitter):
            return
        if s is self.column and self.column.isHidden():
            self._show(self.column)
        if not w.isHidden():
            return
        w.show()
        sizes = s.sizes()
        i = s.indexOf(w)
        want = self._saved.get(id(w), 250)
        j = _main_index(s, skip=i)
        if j is None:
            return
        floor = max(s.widget(j).minimumSizeHint().width() if s.orientation() == Qt.Horizontal
                    else s.widget(j).minimumSizeHint().height(), 60)
        total = sizes[i] + sizes[j]
        give = max(0, min(want, total - floor))
        sizes[i], sizes[j] = give, total - give
        s.setSizes(sizes)
