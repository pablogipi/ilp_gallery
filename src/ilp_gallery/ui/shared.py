"""Shared libraries: several galleries showing the same items, loaded once.

Galleries built with the same ``shared`` key use one :class:`GalleryLibrary`
(items + search index), one data source per source name and one
:class:`ThumbnailCache`, kept here for the life of the process (a Houdini or
Maya session). The first gallery loads a source; the others reuse it as it is.
A reload from any of them updates all of them, and only one of them polls.

    GalleryWidget(shared="motion_clips")      # in every panel
    gallery.add_source(MotionClipsSource())   # loads once; later panels reuse it
"""

from __future__ import annotations

import weakref
from dataclasses import dataclass, field

from ..core.library import GalleryLibrary
from ..media.thumbs import ThumbnailCache


@dataclass
class Shared:
    library: GalleryLibrary
    cache: ThumbnailCache
    loaded: set = field(default_factory=set)          # source names loaded (or loading) once
    poller: "weakref.ref | None" = None               # the gallery that polls for all of them


_SHARED: dict[str, Shared] = {}


def shared(key: str) -> Shared:
    """The shared library and cache for ``key``, created on first use."""
    s = _SHARED.get(key)
    if s is None:
        s = _SHARED[key] = Shared(GalleryLibrary(), ThumbnailCache())
    return s


def drop_shared(key: str) -> None:
    """Forget a shared library (tests, or to free memory). Open galleries keep theirs."""
    s = _SHARED.pop(key, None)
    if s is not None:
        s.cache.shutdown()


class WeakListener:
    """A library listener that holds its gallery weakly and unregisters itself once
    the gallery is gone: a shared library outlives the panels that show it, and a
    host can delete a panel without closing it first."""

    def __init__(self, library: GalleryLibrary, widget) -> None:
        self._library = library
        self._widget = weakref.ref(widget)

    def __call__(self) -> None:
        w = self._widget()
        if w is None or not alive(w):                # Python or C++ side deleted
            self._library.remove_listener(self)
            return
        w._libraryChanged.emit()


def alive(obj) -> bool:
    """False once a Qt object's C++ side has been deleted (e.g. a closed pane)."""
    try:
        from shiboken6 import isValid
    except ImportError:                              # PySide2 hosts
        try:
            from shiboken2 import isValid
        except ImportError:
            isValid = None
    if isValid is not None:
        return isValid(obj)
    try:
        obj.objectName()
        return True
    except RuntimeError:
        return False
