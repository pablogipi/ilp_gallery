"""Colours + stylesheet. Applied to our top widget only, so hosts keep their own look.

A :class:`Theme` is a small set of colours; :meth:`Theme.stylesheet` turns it
into the gallery's stylesheet and the tile / tree / details painters read the
same object. Pick one with ``theme=`` on GalleryBrowser / GalleryWidget /
``dcc.make_browser`` / ``dcc.show``:

* ``"auto"`` (default): match the host inside Houdini, Nuke or Maya, the
  ILP look standalone.
* ``"host"``: derive the colours from the host: its colour scheme in Houdini
  (``hou.qt.getColor``), the running application's QPalette elsewhere.
  "auto" and "host" follow the host when its colours change.
* ``"ilp"``: the built-in ILP look.
* ``"sgi"``, ``"sgi-dark"``, ``"sgi-slate"``: the SGI / IRIX look
  (see :mod:`ilp_gallery.ui.sgi`).
* a :class:`Theme`, e.g. ``ILP.replace(accent="#e08a3d")``.

The environment variable ``ILP_GALLERY_THEME=auto|host|ilp|sgi|...`` overrides the
default for "auto".
"""

from __future__ import annotations

import dataclasses
import os
import sys
from dataclasses import dataclass

from PySide6.QtGui import QColor, QGuiApplication, QPalette


@dataclass(frozen=True)
class Theme:
    name: str
    bg: str             # window
    panel: str          # side panels, inputs
    tile: str
    tile_hover: str
    image_bg: str       # behind thumbnails and the player
    border: str
    text: str
    muted: str
    accent: str         # primary buttons, focus, hover scrub
    accent_hover: str
    accent_text: str    # text on accent
    select_bg: str      # selected tree row
    select_text: str    # text on select_bg
    link: str
    font_size: str | None = "10pt"   # None: inherit the host's font size

    def c(self, role: str) -> QColor:
        return QColor(getattr(self, role))

    def replace(self, **changes) -> "Theme":
        return dataclasses.replace(self, **changes)

    def qpalette(self) -> QPalette:
        """The theme as a QPalette, for whatever the stylesheet doesn't cover
        (check box indicators, popups...), so nothing falls back to the host's."""
        p = QPalette()
        for roles, colour in (
            ((QPalette.Window,), self.bg),
            ((QPalette.Base, QPalette.ToolTipBase), self.panel),
            ((QPalette.AlternateBase, QPalette.Button), self.tile),
            ((QPalette.WindowText, QPalette.Text, QPalette.ButtonText, QPalette.ToolTipText), self.text),
            ((QPalette.PlaceholderText,), self.muted),
            ((QPalette.Highlight,), self.accent),
            ((QPalette.HighlightedText,), self.accent_text),
            ((QPalette.Link,), self.link),
        ):
            for role in roles:
                p.setColor(role, QColor(colour))
        for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
            p.setColor(QPalette.Disabled, role, QColor(self.muted))
        return p

    # Hooks for themes that need more than colours and a stylesheet (see ilp_gallery.ui.sgi).
    def polish(self, root) -> None:
        """Called after the stylesheet is applied to ``root`` (a widget tree)."""

    def unpolish(self, root) -> None:
        """Undo :meth:`polish` when ``root`` switches to another theme."""

    def polish_menu(self, menu) -> None:
        """Called for drop-down / context menus the gallery creates."""

    def tile_bevel(self):
        """(light, light2, dark2, dark) QColors to bevel the tiles with, or None."""
        return None

    def stylesheet(self) -> str:
        return _STYLESHEET.format(
            font=f"font-size: {self.font_size};" if self.font_size else "",
            **{f.name: getattr(self, f.name) for f in dataclasses.fields(self) if f.name != "font_size"},
        )


ILP = Theme(
    name="ilp",
    bg="#1d2027", panel="#23262e", tile="#2a2e37", tile_hover="#323743", image_bg="#1f2229",
    border="#363b47", text="#d9dce3", muted="#8a909c", accent="#3d7fe0", accent_hover="#4b8ef0",
    accent_text="#ffffff", select_bg="#2d4a78", select_text="#ffffff", link="#6ea6f5",
)


# -- deriving a theme from a host palette ------------------------------------

def _hex(c: QColor) -> str:
    return c.name()


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    """``t`` = 0 -> a, 1 -> b."""
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    )


def _luminance(c: QColor) -> float:
    def ch(v: float) -> float:
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())


def _contrast(a: QColor, b: QColor) -> float:
    """WCAG contrast ratio, 1 (none) .. 21 (black on white)."""
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _readable(bg: QColor, *candidates: QColor) -> QColor:
    """The candidate with the most luminance contrast against ``bg``."""
    return max(candidates, key=lambda c: _contrast(bg, c))


def _is_dark(c: QColor) -> bool:
    return c.lightness() < 128


def _shade(c: QColor, amount: int, base: QColor) -> QColor:
    """Move ``c`` ``amount`` lightness steps away from black on dark themes, toward
    black on light ones (judged by ``base``), so contrast works either way."""
    h, s, lightness, a = c.getHsl()
    step = amount if base.lightness() < 128 else -amount
    return QColor.fromHsl(max(h, 0), s, max(0, min(255, lightness + step)), a)


def from_palette(pal: QPalette, name: str = "host") -> Theme:
    """Build a Theme from a QPalette (normally the host application's)."""
    window = pal.color(QPalette.Window)
    base = pal.color(QPalette.Base)
    text = pal.color(QPalette.WindowText)
    hi = pal.color(QPalette.Highlight)
    hi_text = pal.color(QPalette.HighlightedText)
    # Hosts don't always keep every role in step with their colour scheme (Houdini
    # Light still reports a black Base). Panels must stay on the window's side of
    # light/dark, and distinguishable from it.
    if _is_dark(base) != _is_dark(window) or abs(base.lightness() - window.lightness()) < 4:
        base = _shade(window, -5, window)
    if _contrast(text, window) < 4.5:
        text = _readable(window, text, QColor("#e6e6e6"), QColor("#1e1e1e"))
    if _contrast(hi_text, hi) < 3:
        hi_text = _readable(hi, hi_text, QColor("#ffffff"), QColor("#000000"))
    select_bg = _mix(hi, window, 0.45)
    link = _mix(hi, text, 0.35)
    for t in (0.55, 0.75, 1.0):  # links stay readable on the panels
        if _contrast(link, base) >= 4.5:
            break
        link = _mix(hi, text, t)
    return Theme(
        name=name,
        bg=_hex(window),
        panel=_hex(base),
        tile=_hex(_shade(window, 10, window)),
        tile_hover=_hex(_shade(window, 20, window)),
        image_bg=_hex(_shade(window, -8, window)),
        border=_hex(_shade(window, 24, window)),
        text=_hex(text),
        muted=_hex(_mix(text, window, 0.45)),
        accent=_hex(hi),
        accent_hover=_hex(hi.lighter(112)),
        accent_text=_hex(hi_text),
        select_bg=_hex(select_bg),
        select_text=_hex(_readable(select_bg, text, hi_text, QColor("#ffffff"), QColor("#000000"))),
        link=_hex(link),
        font_size=None,  # keep the host's font size
    )


def _houdini_colors() -> dict[str, QColor]:
    """Houdini's current colour scheme via hou.qt.getColor (empty if unavailable).

    Houdini styles its UI from its own scheme, which the Qt palette doesn't
    always follow (e.g. after switching to Houdini Light).
    """
    try:
        import hou
    except ImportError:
        return {}
    out = {}
    for name in ("BackColor", "TextColor", "ButtonColor"):
        try:
            out[name] = QColor(hou.qt.getColor(name))
        except Exception:  # unknown name / older Houdini
            pass
    return out


def host_palette() -> QPalette:
    """The host's colours as a QPalette: the app palette, corrected from the
    host's own colour scheme where we know how to read it."""
    app = QGuiApplication.instance()
    pal = QPalette(app.palette()) if app is not None else QPalette()
    if detect_host() == "houdini":
        hc = _houdini_colors()
        if "BackColor" in hc:
            pal.setColor(QPalette.Window, hc["BackColor"])
            pal.setColor(QPalette.Base, hc["BackColor"])  # from_palette derives the panel shade
        if "TextColor" in hc:
            pal.setColor(QPalette.WindowText, hc["TextColor"])
            pal.setColor(QPalette.Text, hc["TextColor"])
        if "ButtonColor" in hc:
            pal.setColor(QPalette.Button, hc["ButtonColor"])
    return pal


def host_theme() -> Theme:
    """Theme derived from the host application's colours."""
    return from_palette(host_palette(), name=f"host:{detect_host()}")


def detect_host() -> str:
    """houdini | maya | nuke | standalone, without importing anything new."""
    if "hou" in sys.modules:
        return "houdini"
    if "maya.cmds" in sys.modules or "maya.OpenMayaUI" in sys.modules:
        return "maya"
    if "nuke" in sys.modules:
        return "nuke"
    return "standalone"


def resolve(theme: "Theme | str | None" = None) -> Theme:
    """Turn a ``theme=`` argument into a Theme (see the module docstring)."""
    if isinstance(theme, Theme):
        return theme
    choice = (theme or "auto").lower()
    if choice == "auto":
        choice = os.environ.get("ILP_GALLERY_THEME", "auto").strip().lower() or "auto"
    if choice == "auto":
        choice = "ilp" if detect_host() == "standalone" else "host"
    if choice == "host":
        return host_theme()
    if choice == "ilp":
        return ILP
    if choice.startswith("sgi"):
        from .sgi import PRESETS
        if choice in PRESETS:
            return PRESETS[choice]
    raise ValueError(f"Unknown theme {theme!r}: use 'auto', 'host', 'ilp', 'sgi', 'sgi-dark', "
                     "'sgi-slate' or a Theme")


_STYLESHEET = """
QWidget#ILPGalleryRoot, QWidget#ILPGalleryRoot QWidget {{
    color: {text};
    {font}
}}
QMainWindow#ILPGalleryRoot, QWidget#ILPGallery {{ background: {bg}; }}
QMenuBar {{ background: {bg}; color: {text}; }}
QMenuBar::item:selected {{ background: {tile_hover}; }}
QMenu {{ background: {panel}; color: {text}; border: 1px solid {border}; }}
QMenu::item:selected {{ background: {select_bg}; }}
QStatusBar {{ background: {bg}; color: {muted}; }}

QLineEdit {{
    background: {panel}; border: 1px solid {border}; border-radius: 4px;
    padding: 5px 8px; selection-background-color: {accent}; selection-color: {accent_text};
}}
QLineEdit:focus {{ border-color: {accent}; }}

QPushButton, QToolButton {{
    background: {tile}; border: 1px solid {border}; border-radius: 4px; padding: 5px 12px;
}}
QPushButton:hover, QToolButton:hover {{ background: {tile_hover}; }}
QWidget#ILPGalleryRoot QPushButton:disabled {{ color: {muted}; background: {panel}; }}
QPushButton#flat, QToolButton#flat {{ background: transparent; border: none; padding: 5px 8px; }}
QPushButton#flat:hover {{ background: {tile_hover}; }}
QPushButton#primary {{ background: {accent}; border-color: {accent}; color: {accent_text}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {accent_hover}; }}
QWidget#ILPGalleryRoot QPushButton#primary:disabled {{ background: {border}; border-color: {border}; color: {muted}; }}

QTreeView {{
    background: {panel}; border: none; outline: 0; padding-top: 4px;
    show-decoration-selected: 1;
}}
QTreeView::item {{ padding: 4px 2px; }}
QTreeView::item:hover {{ background: {tile}; }}
QTreeView::item:selected {{ background: {select_bg}; color: {select_text}; }}

QListView {{ background: {bg}; border: none; outline: 0; }}
QScrollArea {{ background: {panel}; border: none; }}
QScrollArea > QWidget > QWidget {{ background: {panel}; }}
QWidget#details {{ background: {panel}; }}

QScrollBar:vertical {{ background: {bg}; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {border}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {muted}; }}
QScrollBar:horizontal {{ background: {bg}; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {border}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}

QSlider::groove:horizontal {{ height: 4px; background: {border}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {text}; width: 12px; height: 12px; margin: -5px 0; border-radius: 6px;
}}
QSplitter::handle {{ background: {bg}; }}
QSplitter::handle:horizontal {{ width: 3px; }}

QDockWidget {{ color: {text}; titlebar-close-icon: none; }}
QDockWidget::title {{ background: {bg}; padding: 4px 6px; }}
QPlainTextEdit {{
    background: {panel}; border: 1px solid {border}; border-radius: 4px;
    font-family: monospace; font-size: 9pt;
}}
QWidget#ILPGalleryRoot QLabel#muted {{ color: {muted}; }}
QWidget#ILPGalleryRoot QLabel#title {{ font-size: 14pt; font-weight: 600; }}
QToolTip {{ background: {panel}; color: {text}; border: 1px solid {border}; }}
QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{
    width: 12px; height: 12px; border-radius: 3px;
    background: {panel}; border: 1px solid {muted};
}}
QCheckBox::indicator:hover {{ border-color: {accent}; }}
QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent}; }}
QCheckBox::indicator:disabled {{ background: {bg}; border-color: {border}; }}
"""


# -- backwards compatibility: the ILP colours as module constants -------------
BG, PANEL, TILE, TILE_HOVER, IMAGE_BG = ILP.bg, ILP.panel, ILP.tile, ILP.tile_hover, ILP.image_bg
BORDER, TEXT, MUTED, ACCENT, ACCENT_HOVER = ILP.border, ILP.text, ILP.muted, ILP.accent, ILP.accent_hover
SELECT_BG, LINK = ILP.select_bg, ILP.link
STYLESHEET = ILP.stylesheet()


def c(hex_: str) -> QColor:
    return QColor(hex_)
