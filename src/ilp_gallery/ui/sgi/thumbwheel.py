"""IRIX thumbwheel: a ribbed metal cylinder you drag, like ivview's Rotx / Roty / Dolly.

A ``QAbstractSlider``, so it has the usual ``value`` / ``valueChanged`` / ranges.
The light is fixed while the ribs move, so dragging rolls the ribs through the
highlight: a flat face that drops off steeply at the ends, a specular strip along
both long edges, and ribs drawn as a highlight with its dark edge right beside it.

Works with any :class:`~ilp_gallery.ui.theme.Theme`; SGI themes supply the exact
colours, other themes get colours derived from their palette.
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractSlider, QSizePolicy

from .. import theme as base


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(round(a.red() + (b.red() - a.red()) * t),
                  round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t))


def wheel_colours(t: base.Theme) -> dict[str, QColor]:
    """Colours the wheel needs, from an SGI theme or derived from any other theme."""
    get = lambda name: getattr(t, name, "") or ""            # noqa: E731
    bg = QColor(t.bg)
    dark = bg.lightness() < 128
    return {
        "hilite": QColor(get("hilite") or base._hex(base._shade(bg, 40, bg))),
        "hilite2": QColor(get("hilite2") or base._hex(base._shade(bg, 20, bg))),
        "shadow2": QColor(get("shadow2") or base._hex(base._shade(bg, -25, bg))),
        "shadow": QColor(get("shadow") or base._hex(base._shade(bg, -55, bg))),
        "well": QColor(get("well") or t.panel),
        "face": QColor(get("wheel_face") or t.tile),
        "hi": QColor(get("wheel_hi") or ("#b0b0b0" if dark else "#ffffff")),
        "lo": QColor(get("wheel_lo") or "#141414"),
        "bg": bg,
    }


class ThumbWheel(QAbstractSlider):
    RIB = math.radians(8)       # angle between ribs (about 8px apart in the middle)
    STEP = math.radians(3)      # wheel rotation per value step
    PIXELS_PER_STEP = 3         # drag distance per value step

    def __init__(self, orientation=Qt.Horizontal, theme: base.Theme | None = None, parent=None):
        super().__init__(parent)
        self.setOrientation(orientation)
        self._colours = wheel_colours(theme or base.ILP)
        self._drag = None
        if orientation == Qt.Horizontal:
            self.setFixedHeight(20)
            self.setMinimumWidth(90)
            self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        else:
            self.setFixedWidth(20)
            self.setMinimumHeight(90)
            self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)

    def set_theme(self, theme: base.Theme) -> None:
        self._colours = wheel_colours(theme)
        self.update()

    # -- interaction -----------------------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = (e.position(), self.value())
            self.setSliderDown(True)

    def mouseMoveEvent(self, e):
        if self._drag:
            p0, v0 = self._drag
            d = (e.position().x() - p0.x()) if self.orientation() == Qt.Horizontal else (p0.y() - e.position().y())
            self.setValue(round(v0 + d / self.PIXELS_PER_STEP))

    def mouseReleaseEvent(self, e):
        self._drag = None
        self.setSliderDown(False)

    def wheelEvent(self, e):
        self.setValue(self.value() + (self.singleStep() if e.angleDelta().y() > 0 else -self.singleStep()))

    # -- drawing ------------------------------------------------------------------------
    def paintEvent(self, _):
        c = self._colours
        horiz = self.orientation() == Qt.Horizontal
        p = QPainter(self)
        w, h = self.width(), self.height()
        L, A = (w, h) if horiz else (h, w)          # "along" / "across" the wheel

        def line(a0, c0, a1, c1, colour):
            p.setPen(QPen(colour, 1))
            if horiz:
                p.drawLine(a0, c0, a1, c1)
            else:
                p.drawLine(c0, L - 1 - a0, c1, L - 1 - a1)

        hl, hl2, sh2, sh = c["hilite"], c["hilite2"], c["shadow2"], c["shadow"]
        black, white = c["lo"], c["hi"]
        enabled = self.isEnabled()
        face = c["face"] if enabled else c["bg"]

        # raised frame around a well
        p.fillRect(0, 0, w, h, c["well"])
        for i, (tl, br) in enumerate(((hl, sh), (hl2, sh2))):
            p.setPen(tl)
            p.drawLine(i, i, w - 1 - i, i)
            p.drawLine(i, i, i, h - 1 - i)
            p.setPen(br)
            p.drawLine(i, h - 1 - i, w - 1 - i, h - 1 - i)
            p.drawLine(w - 1 - i, i, w - 1 - i, h - 1 - i)

        # the wheel, in a 1px black outline
        a0, a1, c0, c1 = 4, L - 5, 3, A - 4
        length = a1 - a0 - 1
        r = length / 2.0
        for a, b in ((c0, c0), (c1, c1)):
            line(a0, a, a1, b, black)
        line(a0, c0, a0, c1, black)
        line(a1, c0, a1, c1, black)

        for i in range(length):
            u = (i + 0.5 - r) / r
            k = math.sqrt(max(0.0, 1 - u * u))      # 1 = facing us, 0 = turned away
            a = a0 + 1 + i
            body = black if k < 0.3 else _mix(black, face, min(1.0, (k - 0.3) / 0.3))
            line(a, c0 + 1, a, c1 - 1, body)
            if enabled:                              # specular strips, stepped like the original
                spec = (white if k > 0.92 else _mix(face, white, 0.6) if k > 0.75 else face if k > 0.5
                        else _mix(black, face, 0.5) if k > 0.3 else black)
            else:
                spec = _mix(sh2, hl2, k)
            line(a, c0 + 1, a, c0 + 1, spec)
            line(a, c1 - 1, a, c1 - 1, _mix(black, spec, 0.85))

        # ribs: a highlight with its dark edge right beside it (light from the top-left)
        lit_side = -1 if horiz else 1
        ang = -math.pi / 2 + (self.value() * self.STEP) % self.RIB
        while ang < math.pi / 2:
            pos = round(a0 + 1 + r + r * math.sin(ang))
            k = math.cos(ang)
            if enabled:
                dark = _mix(black, face, 0.5)
                lite = (white if k > 0.88 else _mix(face, white, 0.5) if k > 0.5
                        else face if k > 0.3 else None)
                width = 2 if k > 0.88 else 1
            else:
                dark, lite, width = sh2, hl2, 1
            line(pos, c0 + 2, pos, c1 - 2, dark)
            if lite is not None:
                for j in range(1, width + 1):
                    hp = pos + lit_side * j
                    if a0 < hp < a1:
                        line(hp, c0 + 2, hp, c1 - 2, lite)
            ang += self.RIB
        p.end()
