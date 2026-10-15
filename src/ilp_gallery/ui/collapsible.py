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
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QHBoxLayout, QSizePolicy, QSplitter, QSplitterHandle, QVBoxLayout, QWidget,
)

STRIP = 22              # width of a collapsed pane
QWIDGETSIZE_MAX = (1 << 24) - 1


class _Colors:
    """Tab colours, shared by a pane's header tab and its collapsed tab."""

    def __init__(self) -> None:
        self.set(QColor("#2b303b"), QColor("#363c4a"), QColor("#363b47"), QColor("#d9dce3"),
                 QColor("#8a909c"), QColor("#1e2129"))

    def set(self, tab, hover, border, text, muted, strip) -> None:
        self.tab, self.hover, self.border, self.text, self.muted, self.strip = (
            QColor(tab), QColor(hover), QColor(border), QColor(text), QColor(muted), QColor(strip))


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
        fm = self.fontMetrics()
        w = min(self.width() - 8, fm.horizontalAdvance(self._title) + 22 + self.ARROW_BOX)
        return QRectF(4, 3, max(w, self.ARROW_BOX + 10), self.HEIGHT - 3)

    def arrow_rect(self) -> QRectF:
        t = self.tab_rect()
        return QRectF(t.right() - self.ARROW_BOX - 2, t.top() + (t.height() - self.ARROW_BOX) / 2,
                      self.ARROW_BOX, self.ARROW_BOX)

    def paintEvent(self, _event) -> None:
        c = self._colors
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        tab = self.tab_rect()
        p.setPen(QPen(c.border, 1))
        p.drawLine(QPointF(0, self.height() - 0.5), QPointF(self.width(), self.height() - 0.5))
        path = _tab_path(tab.adjusted(0.5, 0.5, -0.5, 0.5), 4, "bottom")
        p.fillPath(path, c.tab)
        p.drawPath(path)
        p.setPen(c.text)
        text_r = QRectF(tab.left() + 10, tab.top(), tab.width() - self.ARROW_BOX - 14, tab.height())
        p.drawText(text_r, Qt.AlignLeft | Qt.AlignVCenter,
                   self.fontMetrics().elidedText(self._title, Qt.ElideRight, int(text_r.width())))
        a = self.arrow_rect()
        if self._hover_arrow:
            p.setPen(Qt.NoPen)
            p.setBrush(c.hover)
            p.drawRoundedRect(a, 3, 3)
        _arrow(p, a.center(), 8, "left" if self._edge == "left" else "right",
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
        self.setText(text)
        self._edge = edge
        self._colors = colors or _Colors()
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
        return QSize(STRIP, self.fontMetrics().horizontalAdvance(self.text()) + 36)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _event) -> None:
        c = self._colors
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        hover = self.underMouse() or self.isDown()
        # rounded towards the window edge, open towards the gallery
        tab = QRectF(2.5, 0.5, self.width() - 3, self.height() - 1) if self._edge == "left" \
            else QRectF(0.5, 0.5, self.width() - 3, self.height() - 1)
        path = _tab_path(tab, 4, "right" if self._edge == "left" else "left")
        p.setPen(QPen(c.border, 1))
        p.fillPath(path, c.hover if hover else c.tab)
        p.drawPath(path)
        _arrow(p, QPointF(tab.center().x(), tab.top() + 11), 8,
               "right" if self._edge == "left" else "left", c.text if hover else c.muted)
        p.setPen(c.text)
        p.translate(tab.right(), 0)
        p.rotate(90)                                   # reads top to bottom
        avail = int(tab.height() - 28)
        text = self.fontMetrics().elidedText(self.text(), Qt.ElideRight, avail)
        p.drawText(QRectF(tab.top() + 22, 0, avail, tab.width()), Qt.AlignLeft | Qt.AlignVCenter, text)
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


class DockStrip(QWidget):
    """The narrow bar on one side of a :class:`PaneArea` holding the tabs of the
    collapsed panes on that side, stacked top to bottom. Hidden when empty."""

    def __init__(self, edge: str, colors: _Colors, parent=None) -> None:
        super().__init__(parent)
        self._edge = edge
        self._colors = colors
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
        """``edge``: the side the pane collapses to ("left" or "right")."""
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
        self.tab = VerticalTab(title, edge, self.colors)
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
        """Tab colours from a gallery Theme."""
        self.colors.set(t.c("tile"), t.c("tile_hover"), t.c("border"), t.c("text"), t.c("muted"), t.c("panel"))
        self.header.update()
        self.tab.update()

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
    """Left panes | centre pane | right column, between two strips of collapsed tabs."""

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
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.left_strip)
        lay.addWidget(self.splitter, 1)
        lay.addWidget(self.right_strip)

    def add_pane(self, name: str, pane: CollapsiblePane, where: str = "left", size: int = 250,
                 stretch: int = 0) -> CollapsiblePane:
        """``where``: "left" (side by side, in order), "center" or "right" (stacked in the
        right column, in order). ``size``: width, or height for right panes."""
        pane.area = self
        self.panes[name] = pane
        self._where[name] = where
        if where == "right":
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
        self.column.setSizes([self._saved[id(self.column.widget(i))] for i in range(self.column.count())])

    def set_theme(self, t) -> None:
        self.colors.set(t.c("tile"), t.c("tile_hover"), t.c("border"), t.c("text"), t.c("muted"), t.c("panel"))
        for pane in self.panes.values():
            pane.set_theme(t)
        self.left_strip.update()
        self.right_strip.update()
        for s in (self.splitter, self.column):
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
