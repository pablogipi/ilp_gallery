"""The SGI themes: a modern take on IRIX's Indigo Magic look.

Motif bevels with mitered corners, the embossed IRIX check mark, diamond radio
buttons, flush sliders, detached scrollbars, slanted italic tabs, Toolchest
submenu cabinets and IRIX shortcut marks, set in IBM Plex (bundled).

Presets:

* :data:`SGI`: the light IRIX look (warm greys, slate-blue desktop, salmon fields)
* :data:`SGI_DARK`: the same widgets in Autodesk Flame's palette
* :data:`SGI_SLATE`: a blue-slate dark variant
"""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass

from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication

from .. import theme as base
from . import assets, style

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_FONT_FILES = [
    "IBMPlexSans-Regular.ttf", "IBMPlexSans-Italic.ttf", "IBMPlexSans-SemiBold.ttf",
    "IBMPlexSans-SemiBoldItalic.ttf", "IBMPlexSans-Bold.ttf", "IBMPlexSans-BoldItalic.ttf",
    "IBMPlexMono-Regular.ttf", "IBMPlexMono-Italic.ttf", "IBMPlexMono-Bold.ttf",
]
_fonts_registered: set[str] | None = None


def register_fonts() -> set[str]:
    """Register the bundled IBM Plex fonts with this application (once).
    Application fonts are private to the process: the host's fonts are untouched."""
    global _fonts_registered
    if _fonts_registered is None:
        if QGuiApplication.instance() is None:
            return set()
        _fonts_registered = set()
        for name in _FONT_FILES:
            fid = QFontDatabase.addApplicationFont(os.path.join(FONT_DIR, name))
            if fid >= 0:
                _fonts_registered.update(QFontDatabase.applicationFontFamilies(fid))
    return _fonts_registered


@dataclass(frozen=True)
class SgiTheme(base.Theme):
    desktop: str = ""      # behind the tiles (the IRIX desktop)
    hilite: str = ""       # bevels: outer light edge
    hilite2: str = ""      #         inner light edge
    shadow2: str = ""      #         inner dark edge
    shadow: str = ""       #         outer dark edge
    ring: str = ""         # 1px outline around buttons
    pressed: str = ""      # face of a pressed button
    well: str = ""         # sunken areas (lists, tree, log)
    entry: str = ""        # entry fields (salmon on IRIX)
    tab: str = ""          # unselected tabs
    title_bg: str = ""     # dock titles
    accent_hl: str = ""    # bevels of the primary (accent) button
    accent_hl2: str = ""
    accent_sh2: str = ""
    accent_sh: str = ""
    accent_dis: str = ""   # disabled check mark
    radio: str = ""        # selected-radio arrow
    radio_edge: str = ""   #   its dark inner edges
    radio_glow: str = ""   #   its light rim
    radio_face: str = ""   # radio diamond face
    check_off: str = ""    # unchecked box face
    check_face: str = ""   # checked box face (lit)
    tick: str = ""         # check mark facing the light
    tick_deep: str = ""    #   turning away
    tick_tip: str = ""     #   at the tip
    tick_hl: str = ""      #   highlight
    tick_sh: str = ""      #   shadow
    wheel_face: str = ""   # channels: slider trough, scrollbars, thumbwheels
    wheel_hi: str = ""     # thumbwheel highlight
    wheel_lo: str = ""     # near-black ends and grooves
    sb_arrow: str = ""     # scrollbar arrows (default: shadow)
    current: str = ""      # text of the current tab (default: text)
    # Indigo Magic's file views: list panes (tree, drawer) in a deep colour, the
    # icon area (the tiles' grid) lighter. Empty: well / text / muted / desktop.
    grid_bg: str = ""      # behind the tiles
    list_bg: str = ""      # tree and drawer
    list_text: str = ""
    list_muted: str = ""   # counts, missing entries
    list_select_bg: str = ""
    list_select_text: str = ""
    font_ui: str = '"IBM Plex Sans", "Helvetica Neue", Helvetica, Arial, sans-serif'
    font_mono: str = '"IBM Plex Mono", "Liberation Mono", Courier, monospace'

    # -- base.Theme hooks ----------------------------------------------------------
    def stylesheet(self) -> str:
        register_fonts()
        vals = {f.name: getattr(self, f.name) for f in dataclasses.fields(self)}
        vals.update({f"img_{k}": v for k, v in assets.write(self).items()})
        vals["current"] = self.current or self.text
        vals["disabled_text"] = base._hex(base._mix(QColor(self.text), QColor(self.bg), 0.55))
        vals["font_size"] = self.font_size or "9.5pt"
        vals.update(self._list_vals())
        return _QSS.format(**vals)

    def _list_vals(self) -> dict[str, str]:
        list_bg = self.list_bg or self.well
        return {
            "grid_bg": self.grid_bg or self.desktop, "list_bg": list_bg,
            "list_text": self.list_text or self.text,
            "list_hover": base._hex(base._mix(QColor(list_bg), QColor(self.hilite), 0.18)),
            "list_select_bg": self.list_select_bg or self.select_bg,
            "list_select_text": self.list_select_text or self.select_text,
        }

    def list_color(self, role: str) -> QColor:
        own = {"text": self.list_text, "muted": self.list_muted}.get(role)
        return QColor(own) if own else super().list_color(role)

    def polish(self, root) -> None:
        style.apply_style(root)

    def unpolish(self, root) -> None:
        style.remove_style(root)

    ITALIC_TABS = True
    BOLD_TABS = True
    TAB_SHAPE = "sgi"

    def polish_menu(self, menu) -> None:
        super().polish_menu(menu)
        style.install_menu_marks(menu)

    def tile_bevel(self):
        return (QColor(self.hilite), QColor(self.hilite2), QColor(self.shadow2), QColor(self.shadow))


SGI = SgiTheme(
    name="sgi", bg="#c6c5c0", panel="#c6c5c0", tile="#cbcac5", tile_hover="#d5d4cf",
    image_bg="#2c3a52", border="#8e8d88", text="#141414", muted="#55554f",
    accent="#cc1a1f", accent_hover="#dd2a2f", accent_text="#ffffff",
    select_bg="#8fa3c4", select_text="#0c0c0c", link="#1e3f8a", font_size="9.5pt",
    grid_bg="#9fbfc0", list_bg="#4782b4", list_text="#0f2440", list_muted="#203f63",
    list_select_bg="#5d94c4", list_select_text="#ffffff",
    desktop="#4b6b99", hilite="#fbfaf6", hilite2="#e2e1dc", shadow2="#9c9b96", shadow="#5e5d59",
    ring="#3c3c3a", pressed="#b8b7b2", well="#b4b3ae", entry="#d9aaa5", tab="#b4b3ae",
    title_bg="#b0afaa", accent_hl="#f08a85", accent_hl2="#e04a4c", accent_sh2="#a01015", accent_sh="#6e080b",
    accent_dis="#c98a87", radio="#0a05ec", radio_edge="#0b0f48", radio_glow="#cdceff", radio_face="#d6d5d0",
    check_off="#a9a8a3", check_face="#e4e3de",
    tick="#f20d0d", tick_deep="#b00808", tick_tip="#5e0303", tick_hl="#ffb3b0", tick_sh="#2e0202",
    wheel_face="#a8a7a2", wheel_hi="#ffffff", wheel_lo="#141414",
)

SGI_SLATE = SgiTheme(
    name="sgi-slate", bg="#2b3546", panel="#2b3546", tile="#3a4760", tile_hover="#44536f",
    image_bg="#10151f", border="#4d5b75", text="#e4e8ef", muted="#9aa5b8",
    accent="#e8392f", accent_hover="#f24c42", accent_text="#ffffff",
    select_bg="#4a6a99", select_text="#ffffff", link="#9cc0f5", font_size="9.5pt",
    grid_bg="#3f5876", list_bg="#1f3a62", list_text="#dfe7f3", list_muted="#8ea3c4",
    list_select_bg="#33568a", list_select_text="#ffffff",
    desktop="#2a4166", hilite="#7488aa", hilite2="#526380", shadow2="#222b3a", shadow="#121821",
    ring="#0a0d13", pressed="#303b50", well="#222b3a", entry="#4f3a40", tab="#26303f",
    title_bg="#344058", accent_hl="#ff9b92", accent_hl2="#f5645a", accent_sh2="#a8221a", accent_sh="#6a120c",
    accent_dis="#7a4a50", radio="#6c78ff", radio_edge="#1c2266", radio_glow="#c3c9ff", radio_face="#3a4760",
    check_off="#26303f", check_face="#5c6c88",
    tick="#ff3a2e", tick_deep="#c41a12", tick_tip="#6a0c06", tick_hl="#ffb8b2", tick_sh="#140000",
    wheel_face="#4d5b75", wheel_hi="#aebbd2", wheel_lo="#141a24",
)

# Autodesk Flame's palette (sampled from a Flame UI): neutral charcoals, blue-grey
# value fields, steel-blue toggles, amber for the current item.
SGI_DARK = SGI_SLATE.replace(
    name="sgi-dark",
    bg="#212121", panel="#212121", tile="#373737", tile_hover="#404040", image_bg="#0e0e0e",
    border="#2d2d2d", text="#c4c4c4", muted="#888888",
    accent="#667cb6", accent_hover="#7890cc", accent_text="#ffffff",
    select_bg="#4f4f4f", select_text="#e6e6e6", link="#d2ad62",
    grid_bg="#2a2a2a", list_bg="#171717", list_text="#c4c4c4", list_muted="#7a7a7a",
    list_select_bg="#4f4f4f", list_select_text="#e6e6e6",
    desktop="#161616", hilite="#565656", hilite2="#444444", shadow2="#191919", shadow="#0e0e0e",
    ring="#0a0a0a", pressed="#2c2c2c", well="#1b1b1b", entry="#393c48", tab="#262626",
    title_bg="#2f2f2f", accent_hl="#9fb2e2", accent_hl2="#7f95cf", accent_sh2="#4a5c8e", accent_sh="#2a3456",
    accent_dis="#5a5a5a", radio="#6f88cc", radio_edge="#243052", radio_glow="#b4c2ea", radio_face="#3a3a3a",
    check_off="#2b2b2b", check_face="#4f4f4f",
    tick="#e3453a", tick_deep="#a3231b", tick_tip="#4c0d09", tick_hl="#ffb1aa", tick_sh="#120000",
    wheel_face="#3c3c3c", wheel_hi="#b0b0b0", wheel_lo="#0e0e0e",
    current="#dfbe68", sb_arrow="#8c8c8c",
)

PRESETS = {t.name: t for t in (SGI, SGI_DARK, SGI_SLATE)}
PRESETS["sgi-light"] = SGI


_QSS = """
QWidget#ILPGalleryRoot, QWidget#ILPGalleryRoot QWidget {{
    color: {text};
    font-family: {font_ui};
    font-size: {font_size};
}}
QMainWindow#ILPGalleryRoot, QWidget#ILPGallery {{ background: {bg}; }}
QMenuBar {{ background: {bg}; color: {text}; border-bottom: 1px solid {shadow2}; font-style: italic; font-weight: 600; }}
QMenuBar::item {{ padding: 3px 10px; }}
QMenuBar::item:selected {{ background: {select_bg}; }}
QMenu {{ background: {bg}; color: {text}; border-image: url({img_panel}) 2 2 2 2 stretch stretch; border-width: 2px; }}
QMenu::item {{ padding: 4px 38px 4px 18px; font-style: italic; font-weight: 600; }}
QMenu::item:selected {{ background: {select_bg}; color: {select_text}; }}
QMenu::separator {{ height: 2px; margin: 3px 4px; border-top: 1px solid {shadow2}; border-bottom: 1px solid {hilite}; }}
QMenu::right-arrow {{ image: url({img_submenu}); width: 12px; height: 16px; subcontrol-position: right center; right: 10px; }}
QMenu::right-arrow:selected {{ image: url({img_submenu_sel}); }}
QStatusBar {{ background: {bg}; color: {muted}; border-top: 1px solid {hilite2}; }}
QWidget#ILPGalleryRoot QLabel:disabled, QWidget#ILPGalleryRoot QCheckBox:disabled,
QWidget#ILPGalleryRoot QRadioButton:disabled {{ color: {disabled_text}; }}

QLineEdit {{
    background: transparent; color: {text};
    border-image: url({img_entry}) 2 2 2 2 stretch stretch; border-width: 2px;
    padding: 3px 6px; selection-background-color: {accent}; selection-color: {accent_text};
}}

QPushButton, QToolButton, QComboBox {{
    background: transparent; border-image: url({img_raised}) 3 3 3 3 stretch stretch;
    border-width: 3px; padding: 2px 12px; min-height: 18px;
}}
QPushButton:hover, QToolButton:hover, QComboBox:hover {{ border-image: url({img_raised_hover}) 3 3 3 3 stretch stretch; }}
QPushButton:pressed, QToolButton:pressed {{ border-image: url({img_pressed}) 3 3 3 3 stretch stretch;
    padding: 3px 11px 1px 13px; }}
QWidget#ILPGalleryRoot QPushButton:disabled {{ color: {shadow2}; }}
QPushButton:default, QPushButton#primary {{ border-image: url({img_default}) 8 8 8 8 stretch stretch;
    border-width: 8px; padding: 0 7px; }}
QPushButton:default:hover, QPushButton#primary:hover {{ border-image: url({img_default_hover}) 8 8 8 8 stretch stretch; }}
QPushButton:default:pressed, QPushButton#primary:pressed {{ border-image: url({img_default_pressed}) 8 8 8 8 stretch stretch;
    padding: 1px 6px 0 8px; }}
QWidget#ILPGalleryRoot QPushButton#primary:disabled {{ color: {shadow2}; }}
QPushButton#primary[menuButton="true"] {{ border-image: url({img_raised}) 3 3 3 3 stretch stretch; border-width: 3px; }}
QPushButton#primary[menuButton="true"]:hover {{ border-image: url({img_raised_hover}) 3 3 3 3 stretch stretch; }}
QPushButton#primary[menuButton="true"]:pressed {{ border-image: url({img_pressed}) 3 3 3 3 stretch stretch; }}
QToolButton {{ padding: 1px 4px; }}

QComboBox {{ padding-right: 26px; font-style: italic; font-weight: 600; }}
QComboBox::drop-down {{ border: none; width: 22px; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url({img_option}); width: 14px; height: 8px; }}
QComboBox QAbstractItemView {{ background: {bg}; color: {text}; border: 2px solid {shadow}; border-image: none;
    outline: 0; padding: 2px; }}
QComboBox QAbstractItemView::item {{ padding: 0 10px; font-style: italic; font-weight: 600; }}
QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected {{
    background: {select_bg}; color: {select_text}; }}

QCheckBox, QRadioButton {{ spacing: 6px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; image: url({img_check_off}); }}
QCheckBox::indicator:checked {{ image: url({img_check_on}); }}
QCheckBox::indicator:disabled {{ image: url({img_check_dis}); }}
QCheckBox::indicator:checked:disabled {{ image: url({img_check_dis_on}); }}
QRadioButton::indicator {{ width: 18px; height: 18px; image: url({img_radio_off}); }}
QRadioButton::indicator:checked {{ image: url({img_radio_on}); }}
QRadioButton::indicator:disabled {{ image: url({img_radio_dis}); }}
QRadioButton::indicator:checked:disabled {{ image: url({img_radio_dis_on}); }}

QTreeView {{
    background: {list_bg}; color: {list_text}; outline: 0; padding-top: 2px;
    border-image: url({img_sunken_list}) 2 2 2 2 stretch stretch; border-width: 2px;
    show-decoration-selected: 1;
}}
QTreeView::item {{ padding: 3px 2px; }}
QTreeView::item:hover {{ background: {list_hover}; }}
QTreeView::item:selected {{ background: {list_select_bg}; color: {list_select_text}; }}

QListView {{ background: {grid_bg}; outline: 0; border-image: url({img_sunken_desktop}) 2 2 2 2 stretch stretch; border-width: 2px; }}
QListWidget {{ background: {list_bg}; color: {list_text}; outline: 0;
    border-image: url({img_sunken_list}) 2 2 2 2 stretch stretch; border-width: 2px; }}
QListWidget::item {{ padding: 1px 4px; }}
QAbstractItemView::indicator {{ width: 18px; height: 18px; image: url({img_check_off}); }}
QAbstractItemView::indicator:checked {{ image: url({img_check_on}); }}
QAbstractItemView::indicator:disabled {{ image: url({img_check_dis}); }}
QAbstractItemView::indicator:checked:disabled {{ image: url({img_check_dis_on}); }}
QListWidget::item:hover {{ background: {list_hover}; }}
QListWidget::item:selected {{ background: {list_select_bg}; color: {list_select_text}; }}
QScrollArea {{ background: {panel}; border: none; }}
QScrollArea > QWidget > QWidget {{ background: {panel}; }}
QWidget#details {{ background: {panel}; }}

QScrollBar:vertical {{ width: 22px; margin: 18px 0 18px 0;
    border-image: url({img_sb_trough_v}) 1 1 0 4 stretch stretch; border-width: 1px 1px 0 4px; }}
QScrollBar:horizontal {{ height: 22px; margin: 0 18px 0 18px;
    border-image: url({img_sb_trough_h}) 4 0 1 1 stretch stretch; border-width: 4px 0 1px 1px; }}
QScrollBar::handle {{ border-image: url({img_sb_raised}) 3 3 3 3 stretch stretch; border-width: 3px; }}
QScrollBar::handle:vertical {{ min-height: 30px; image: url({img_sb_grip_v}); margin: 0 -1px 0 -1px; }}
QScrollBar::handle:horizontal {{ min-width: 30px; image: url({img_sb_grip_h}); margin: -1px 0 -1px 0; }}
QScrollBar::add-line, QScrollBar::sub-line {{
    border-image: url({img_sb_raised}) 3 3 3 3 stretch stretch; border-width: 3px; subcontrol-origin: margin; }}
QScrollBar::add-line:pressed, QScrollBar::sub-line:pressed {{ border-image: url({img_sb_pressed}) 3 3 3 3 stretch stretch; }}
QScrollBar::sub-line:vertical {{ height: 12px; width: 13px; subcontrol-position: top right; }}
QScrollBar::add-line:vertical {{ height: 12px; width: 13px; subcontrol-position: bottom right; }}
QScrollBar::sub-line:horizontal {{ width: 12px; height: 13px; subcontrol-position: bottom left; }}
QScrollBar::add-line:horizontal {{ width: 12px; height: 13px; subcontrol-position: bottom right; }}
QScrollBar::up-arrow {{ image: url({img_sb_arrow_up}); width: 11px; height: 11px; }}
QScrollBar::down-arrow {{ image: url({img_sb_arrow_down}); width: 11px; height: 11px; }}
QScrollBar::left-arrow {{ image: url({img_sb_arrow_left}); width: 11px; height: 11px; }}
QScrollBar::right-arrow {{ image: url({img_sb_arrow_right}); width: 11px; height: 11px; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QSlider {{ min-height: 17px; }}
QSlider::groove:horizontal {{ height: 7px; border-image: url({img_trough}) 4 3 4 3 stretch stretch;
    border-width: 4px 3px 4px 3px; }}
QSlider::sub-page:horizontal, QSlider::add-page:horizontal {{ background: none; }}
QSlider::handle:horizontal {{ width: 30px; height: 9px; margin: -1px 0; image: url({img_slider_thumb}); }}
QSlider::handle:horizontal:disabled {{ image: url({img_slider_thumb_dis}); }}

QTabWidget::pane {{ border-image: url({img_panel}) 2 2 2 2 stretch stretch; border-width: 2px; top: -2px; }}
QTabBar::tab {{ border-image: url({img_tab}) 1 8 0 8 stretch stretch; border-width: 1px 8px 0 8px;
    padding: 3px 5px 2px 4px; margin-right: -4px; margin-top: 3px; color: {muted}; font-style: italic; }}
QTabBar::tab:last, QTabBar::tab:only-one {{ margin-right: 0; }}
QTabBar::tab:selected {{ border-image: url({img_tab_on}) 1 8 0 8 stretch stretch; margin-top: 0;
    padding-bottom: 4px; color: {current}; font-weight: 600; }}

QFrame#groove {{ border: none; border-top: 1px solid {shadow2}; border-bottom: 1px solid {hilite}; max-height: 0; min-height: 0; }}
QSplitter::handle {{ background: {bg}; }}
QSplitter::handle:horizontal {{ width: 5px; }}
QDockWidget {{ color: {text}; titlebar-close-icon: none; }}
QDockWidget::title {{ background: {title_bg}; padding: 3px 8px; font-style: italic; }}
QPlainTextEdit {{ background: {well}; font-size: 9pt;
    border-image: url({img_sunken}) 2 2 2 2 stretch stretch; border-width: 2px; }}
QWidget#ILPGalleryRoot QLineEdit, QWidget#ILPGalleryRoot QPlainTextEdit {{ font-family: {font_mono}; }}
QWidget#ILPGalleryRoot QLabel#muted {{ color: {muted}; }}
QWidget#ILPGalleryRoot QLabel#title {{ font-size: 14pt; font-weight: 600; font-style: italic; }}
QToolTip {{ background: {entry}; color: {text}; border: 1px solid {shadow}; }}
"""
