"""SGI themes for the gallery: a modern take on IRIX's Indigo Magic look.

Use them like any other theme::

    make_browser(theme="sgi")          # light IRIX look
    make_browser(theme="sgi-dark")     # same widgets in Autodesk Flame's palette
    make_browser(theme="sgi-slate")    # blue-slate dark variant

or tweak a preset: ``SGI.replace(accent="#2a7")``. The fonts (IBM Plex, SIL Open
Font License, see ``fonts/OFL.txt``) are bundled and registered for this
application only.
"""

from .style import apply_style, install_menu_marks, remove_style
from .theme import PRESETS, SGI, SGI_DARK, SGI_SLATE, SgiTheme, register_fonts
from .thumbwheel import ThumbWheel

__all__ = ["SgiTheme", "SGI", "SGI_DARK", "SGI_SLATE", "PRESETS", "ThumbWheel",
           "apply_style", "remove_style", "install_menu_marks", "register_fonts"]
