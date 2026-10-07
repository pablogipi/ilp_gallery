"""ILP Gallery: image / GIF / movie gallery component for PySide6.

Core (Qt-free):  GalleryItem, GalleryLibrary, SearchIndex
UI:              GalleryWidget (embeddable), PlayerWidget
Hosts:           ilp_gallery.dcc
"""

from .core import GalleryItem, GalleryLibrary, SearchIndex, scan_folder

__version__ = "0.1.0"
__all__ = ["GalleryItem", "GalleryLibrary", "SearchIndex", "scan_folder", "GalleryWidget", "GalleryBrowser", "PlayerWidget"]


def __getattr__(name):  # lazy: keep `import ilp_gallery` Qt-free for farm / CLI use
    if name == "GalleryWidget":
        from .ui.widget import GalleryWidget
        return GalleryWidget
    if name == "GalleryBrowser":
        from .ui.browser import GalleryBrowser
        return GalleryBrowser
    if name == "PlayerWidget":
        from .ui.player import PlayerWidget
        return PlayerWidget
    raise AttributeError(name)
