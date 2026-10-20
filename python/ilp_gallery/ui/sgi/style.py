"""IRIX details that stylesheets can't express, applied to the gallery's own widgets.

* Scrollbars outside the scroll area's frame (lists, trees, text views).
* The IRIX shortcut mark: a small three-step staircase under the mnemonic letter,
  instead of Qt's underline.

A proxy style does both for widgets whose text goes through the style. Drop-down
menus are drawn by Qt's stylesheet engine without consulting the style, so they get
a paint hook that adds the marks after Qt has drawn the menu.

Only widgets passed to :func:`apply_style` (and menus passed to
:func:`install_menu_marks`) are affected; the host application's widgets never are.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QFontMetricsF, QPainter, QPalette
from PySide6.QtWidgets import (
    QApplication, QComboBox, QMenu, QProxyStyle, QStyle, QStyledItemDelegate, QWidget,
)

_MARKED = "ilpSgiStyled"           # dynamic property on widgets we restyled


# -- the shortcut mark -----------------------------------------------------------

def mnemonic_index(text: str) -> tuple[int, str]:
    """(index of the mnemonic letter, text without '&' markers); index -1 if none.
    '&&' is a literal ampersand."""
    out, idx, i = [], -1, 0
    while i < len(text):
        ch = text[i]
        if ch == "&" and i + 1 < len(text):
            if text[i + 1] == "&":
                out.append("&")
                i += 2
                continue
            if idx < 0:
                idx = len(out)
            i += 1
            continue
        out.append(ch)
        i += 1
    return idx, "".join(out)


def draw_mnemonic_mark(painter: QPainter, font, rect, flags, text: str, colour) -> None:
    """Draw the IRIX mark under the mnemonic letter of ``text`` as laid out in ``rect``:
    three crisp 1px rows (full, ~half, ~quarter length), starting one pixel left of
    the letter, one empty row below the baseline."""
    idx, plain = mnemonic_index(text)
    if idx < 0 or idx >= len(plain):
        return
    fm = QFontMetricsF(font)
    br = fm.boundingRect(rect, int(flags) & ~int(Qt.TextShowMnemonic), plain)
    x = br.left() + fm.horizontalAdvance(plain[:idx])
    if font.italic():
        x -= fm.ascent() * 0.12                # start under the slanted letter's foot
    w = fm.horizontalAdvance(plain[idx])
    x0 = round(x) - 1
    y0 = round(br.top() + fm.ascent()) + 1
    length = max(5, round(w) + 3)
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, False)
    for i, frac in enumerate((1.0, 0.55, 0.28)):
        painter.fillRect(x0, y0 + i, max(2, round(length * frac)), 1, colour)
    painter.restore()


# -- proxy style -----------------------------------------------------------------

class SgiProxyStyle(QProxyStyle):
    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.SH_ScrollView_FrameOnlyAroundContents:
            return 1                           # scrollbars outside the frame
        if hint == QStyle.SH_UnderlineShortcut and isinstance(widget, QMenu):
            return 0                           # menus get their marks from the paint hook
        return super().styleHint(hint, option, widget, returnData)

    def pixelMetric(self, metric, option=None, widget=None):
        if metric == QStyle.PM_ScrollView_ScrollBarSpacing:
            return 3
        return super().pixelMetric(metric, option, widget)

    def drawItemText(self, painter, rect, flags, pal, enabled, text, role=QPalette.NoRole):
        if "&" in text and flags & Qt.TextShowMnemonic:
            flags = (flags & ~Qt.TextShowMnemonic) | Qt.TextHideMnemonic
            super().drawItemText(painter, rect, flags, pal, enabled, text, role)
            colour = painter.pen().color() if role == QPalette.NoRole else pal.color(role)
            draw_mnemonic_mark(painter, painter.font(), rect, flags, text, colour)
            return
        super().drawItemText(painter, rect, flags, pal, enabled, text, role)


_style: SgiProxyStyle | None = None


def proxy_style() -> SgiProxyStyle:
    """The shared proxy style, owned by the QApplication so it outlives the widgets."""
    global _style
    if _style is None:
        _style = SgiProxyStyle()
        _style.setParent(QApplication.instance())
    return _style


# -- drop-down menus ---------------------------------------------------------------

class _MenuMarks(QObject):
    """Paints the shortcut marks after Qt has drawn a menu."""

    #: QMenu::item padding in the SGI stylesheet (left, top, right, bottom)
    ITEM_PADDING = (18, 4, 38, 4)

    def eventFilter(self, menu, event) -> bool:
        if event.type() != QEvent.Paint or menu.property("_ilpSgiPainting"):
            return False
        menu.setProperty("_ilpSgiPainting", True)
        try:
            QMenu.paintEvent(menu, event)
            self._paint_marks(menu)
        finally:
            menu.setProperty("_ilpSgiPainting", False)
        return True

    def _paint_marks(self, menu: QMenu) -> None:
        p = QPainter(menu)
        font = menu.font()
        font.setItalic(True)
        font.setWeight(font.Weight.DemiBold)    # QMenu::item is bold italic
        l, t, r, b = self.ITEM_PADDING
        colour = menu.palette().color(menu.foregroundRole())
        for action in menu.actions():
            text = action.text().split("\t")[0]
            if action.isSeparator() or "&" not in text:
                continue
            rect = menu.actionGeometry(action).adjusted(l, t, -r, -b)
            draw_mnemonic_mark(p, font, rect, Qt.AlignLeft | Qt.AlignVCenter, text, colour)
        p.end()


_menu_marks: _MenuMarks | None = None


def install_menu_marks(menu: QMenu) -> None:
    """Give a drop-down menu (and its submenus) the SGI style and shortcut marks."""
    global _menu_marks
    if _menu_marks is None:
        _menu_marks = _MenuMarks()
        _menu_marks.setParent(QApplication.instance())
    if not menu.property(_MARKED):
        menu.setStyle(proxy_style())
        menu.installEventFilter(_menu_marks)
        menu.setProperty(_MARKED, True)
    for action in menu.actions():
        if action.menu() is not None:
            install_menu_marks(action.menu())


# -- apply / remove ------------------------------------------------------------------

# Combo popups: Qt paints them with an internal menu delegate the stylesheet can't
# reach (no theme colours, no hover). A QStyledItemDelegate makes the
# "QComboBox QAbstractItemView" rules apply; the original comes back on removal.
_combo_delegates: dict[int, object] = {}
COMBO_ROW = 22                                   # popup row height (stylesheet heights don't reach the layout)


class _ComboDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        s.setHeight(max(s.height(), COMBO_ROW))
        return s


def _style_combo(combo: QComboBox) -> None:
    if id(combo) in _combo_delegates:
        return
    _combo_delegates[id(combo)] = combo.itemDelegate()      # kept alive: Qt doesn't delete it
    combo.setItemDelegate(_ComboDelegate(combo))
    combo.destroyed.connect(lambda *_a, k=id(combo): _combo_delegates.pop(k, None))


def _unstyle_combo(combo: QComboBox) -> None:
    old = _combo_delegates.pop(id(combo), None)
    if old is not None:
        combo.setItemDelegate(old)


def apply_style(root: QWidget) -> None:
    """Apply the SGI proxy style to ``root`` and everything under it (menus included)."""
    style = proxy_style()
    for w in [root] + root.findChildren(QWidget):
        if isinstance(w, QMenu):
            install_menu_marks(w)
        elif not w.property(_MARKED):
            w.setStyle(style)
            w.setProperty(_MARKED, True)
        if isinstance(w, QComboBox):
            _style_combo(w)


def remove_style(root: QWidget) -> None:
    """Undo :func:`apply_style`: widgets go back to the application's style."""
    for w in [root] + root.findChildren(QWidget):
        if not w.property(_MARKED):
            continue
        if isinstance(w, QMenu) and _menu_marks is not None:
            w.removeEventFilter(_menu_marks)
        if isinstance(w, QComboBox):
            _unstyle_combo(w)
        w.setStyle(None)
        w.setProperty(_MARKED, False)
