"""SGI themes for the gallery: a modern take on IRIX's Indigo Magic look.

Use them like any other theme::

    make_browser(theme="sgi")          # light IRIX look
    make_browser(theme="sgi-dark")     # same widgets in Autodesk Flame's palette
    make_browser(theme="sgi-slate")    # blue-slate dark variant

The same three palettes are also available in Qt's Fusion style::

    make_browser(theme="sgi-dark", style="fusion")   # or theme="fusion-dark"

Tweak a preset with ``SGI.replace(accent="#2a7")``. The fonts (IBM Plex, SIL Open
Font License, see ``fonts/OFL.txt``) are bundled and registered for this
application only.
"""

from .fusion import FUSION, FUSION_DARK, FUSION_SLATE, FusionTheme, with_style
from .style import apply_style, install_menu_marks, remove_style
from .theme import PRESETS, SGI, SGI_DARK, SGI_SLATE, SgiTheme, register_fonts
from .thumbwheel import ThumbWheel

PRESETS.update({t.name: t for t in (FUSION, FUSION_DARK, FUSION_SLATE)})
PRESETS["fusion-light"] = FUSION

__all__ = ["SgiTheme", "SGI", "SGI_DARK", "SGI_SLATE", "PRESETS", "ThumbWheel",
           "FusionTheme", "FUSION", "FUSION_DARK", "FUSION_SLATE", "with_style",
           "apply_style", "remove_style", "install_menu_marks", "register_fonts"]
