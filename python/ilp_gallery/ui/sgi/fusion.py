"""The SGI palettes drawn by Qt's built-in Fusion style.

Same colours as the SGI themes, modern widgets::

    make_browser(theme="sgi-dark", style="fusion")   # or simply theme="fusion-dark"

Fusion is driven by the QPalette, so the theme maps its colours onto Qt's colour
roles and keeps a stylesheet only for what a palette can't express (fonts, the
desktop behind the tiles, muted labels). Like the SGI style, Fusion is applied to
the gallery's own widgets only, never to the host application's.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QProxyStyle, QPushButton, QStyleFactory, QWidget

from .theme import SGI, SGI_DARK, SGI_SLATE, SgiTheme, register_fonts

_MARKED = "ilpFusionStyled"          # dynamic property on widgets we restyled
_style: QProxyStyle | None = None


def fusion_style() -> QProxyStyle:
    """Shared Fusion style, owned by the QApplication so it outlives the widgets."""
    global _style
    if _style is None:
        _style = QProxyStyle(QStyleFactory.create("Fusion"))
        _style.setParent(QApplication.instance())
    return _style


@dataclass(frozen=True)
class FusionTheme(SgiTheme):
    """An SGI palette drawn by Qt's Fusion style."""

    def qpalette(self) -> QPalette:
        p = super().qpalette()
        for role, colour in ((QPalette.Button, self.tile), (QPalette.Light, self.hilite),
                             (QPalette.Midlight, self.hilite2), (QPalette.Mid, self.shadow2),
                             (QPalette.Dark, self.shadow), (QPalette.Shadow, self.wheel_lo),
                             (QPalette.Base, self.well), (QPalette.AlternateBase, self.panel),
                             (QPalette.Highlight, self.accent), (QPalette.HighlightedText, self.accent_text)):
            p.setColor(role, QColor(colour))
        return p

    def stylesheet(self) -> str:
        register_fonts()
        return f"""
QWidget#ILPGalleryRoot, QWidget#ILPGalleryRoot QWidget {{ font-family: {self.font_ui}; font-size: {self.font_size}; }}
QWidget#ILPGalleryRoot QLineEdit, QWidget#ILPGalleryRoot QPlainTextEdit {{ font-family: {self.font_mono}; }}
QWidget#ILPGalleryRoot QLabel#muted {{ color: {self.muted}; }}
QWidget#ILPGalleryRoot QLabel#title {{ font-size: 14pt; font-weight: 600; }}
QListView {{ background: {self.desktop}; }}
"""

    def _primary_palette(self) -> QPalette:
        p = self.qpalette()
        p.setColor(QPalette.Button, QColor(self.accent))
        p.setColor(QPalette.ButtonText, QColor(self.accent_text))
        return p

    def polish(self, root) -> None:
        # Set the palette on every widget: with a stylesheet on the root, Qt doesn't
        # propagate the root's palette to children matched by stylesheet rules.
        style, pal, primary = fusion_style(), self.qpalette(), self._primary_palette()
        for w in [root] + root.findChildren(QWidget):
            if not w.property(_MARKED):
                w.setStyle(style)
                w.setProperty(_MARKED, True)
            is_primary = isinstance(w, QPushButton) and w.objectName() == "primary"
            w.setPalette(primary if is_primary else pal)     # primary: the accent button (Export)

    def unpolish(self, root) -> None:
        for w in [root] + root.findChildren(QWidget):
            if w.property(_MARKED):
                w.setStyle(None)
                w.setProperty(_MARKED, False)
                if w is not root:
                    w.setPalette(QPalette())                  # inherit again

    def polish_menu(self, menu) -> None:
        menu.setStyle(fusion_style())
        menu.setProperty(_MARKED, True)
        menu.setPalette(self.qpalette())
        for action in menu.actions():
            if action.menu() is not None:
                self.polish_menu(action.menu())

    def tile_bevel(self):
        return None                                   # Fusion keeps the rounded tiles


def fusion(t: SgiTheme, name: str | None = None) -> FusionTheme:
    """The same palette as ``t``, drawn by Fusion."""
    values = {f.name: getattr(t, f.name) for f in dataclasses.fields(SgiTheme)}
    values["name"] = name or t.name.replace("sgi", "fusion", 1)
    return FusionTheme(**values)


def sgi(t: SgiTheme, name: str | None = None) -> SgiTheme:
    """The same palette as ``t``, drawn in the SGI style."""
    values = {f.name: getattr(t, f.name) for f in dataclasses.fields(SgiTheme)}
    values["name"] = name or t.name.replace("fusion", "sgi", 1)
    return SgiTheme(**values)


STYLES = {"sgi": sgi, "fusion": fusion}


def with_style(t, style: str):
    """``t``'s palette in another widget style: "sgi" or "fusion"."""
    key = style.lower()
    if key not in STYLES:
        raise ValueError(f"Unknown style {style!r}: use 'sgi' or 'fusion'")
    if not isinstance(t, SgiTheme):
        raise ValueError(f"style={style!r} needs one of the SGI palettes "
                         f"('sgi', 'sgi-dark', 'sgi-slate'), not {t.name!r}")
    if key == "fusion" and isinstance(t, FusionTheme):
        return t
    if key == "sgi" and not isinstance(t, FusionTheme):
        return t
    return STYLES[key](t)


FUSION = fusion(SGI, "fusion")
FUSION_DARK = fusion(SGI_DARK, "fusion-dark")
FUSION_SLATE = fusion(SGI_SLATE, "fusion-slate")
