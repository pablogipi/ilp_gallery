"""Host integration: Houdini, Maya, Nuke and standalone.

Every function imports its host module lazily, so this file is safe to
import anywhere.

    # Houdini (Python Shell / shelf tool)
    from ilp_gallery import dcc; dcc.show("/jobs/lib/clips")

    # Maya (dockable workspace control)
    from ilp_gallery import dcc; dcc.show_maya("/jobs/lib/clips")

    # Nuke (menu.py): register a dockable panel
    from ilp_gallery import dcc; dcc.register_nuke_panel()

Default library when no source is passed: ``$ILP_GALLERY_LIBRARY``
(a JSON file or folder; use ``:`` to separate several).
"""

from __future__ import annotations

import os
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget

from .ui.browser import GalleryBrowser
from .ui.widget import GalleryWidget

_windows: dict[str, QWidget] = {}  # keep references so windows aren't garbage collected


def default_sources() -> list[str]:
    raw = os.environ.get("ILP_GALLERY_LIBRARY", "")
    return [s for s in raw.split(os.pathsep) if s]


def host() -> str:
    try:
        import hou  # noqa: F401
        return "houdini"
    except ImportError:
        pass
    try:
        import maya.cmds  # noqa: F401
        return "maya"
    except ImportError:
        pass
    try:
        import nuke  # noqa: F401
        return "nuke"
    except ImportError:
        pass
    return "standalone"


def main_window() -> QWidget | None:
    h = host()
    if h == "houdini":
        import hou
        return hou.qt.mainWindow()
    if h == "maya":
        from maya import OpenMayaUI
        from shiboken6 import wrapInstance
        ptr = OpenMayaUI.MQtUtil.mainWindow()
        return wrapInstance(int(ptr), QWidget) if ptr else None
    app = QApplication.instance()
    if app:
        for w in app.topLevelWidgets():
            if isinstance(w, QMainWindow) and w.isVisible():
                return w
    return None


def make_browser(sources: list[str] | None = None, parent: QWidget | None = None,
                 actions: list[tuple[str, Callable]] | None = None, host_defaults: bool = True,
                 **kwargs) -> GalleryBrowser:
    """Full browser (tree, grid, details, export log). Embeddable: it's a QWidget.

    ``host_defaults`` registers the example exporters / drag handlers for the
    current host (see :func:`install_host_defaults`); pass False to set your own.
    """
    b = GalleryBrowser(parent=parent, **kwargs)
    for s in sources if sources is not None else default_sources():
        b.load(s, background=True)
    for label, cb in actions or []:
        b.gallery.add_action(label, cb)
    if host_defaults:
        install_host_defaults(b.gallery)
    return b


def make_widget(sources: list[str] | None = None, parent: QWidget | None = None,
                actions: list[tuple[str, Callable]] | None = None, host_defaults: bool = True,
                **kwargs) -> GalleryWidget:
    """Just the gallery component, for tools that bring their own window."""
    w = GalleryWidget(parent=parent, **kwargs)
    for s in sources if sources is not None else default_sources():
        w.load(s, background=True)
    for label, cb in actions or []:
        w.add_action(label, cb)
    if host_defaults:
        install_host_defaults(w)
    return w


def show(sources: list[str] | str | None = None, name: str = "ILP Gallery",
         actions: list[tuple[str, Callable]] | None = None, **kwargs) -> GalleryBrowser:
    """Open (or raise) the gallery window, parented to the host main window."""
    if isinstance(sources, str):
        sources = [sources]
    win = _windows.get(name)
    if win is not None:
        try:
            win.show()
            win.raise_()
            win.activateWindow()
            return win
        except RuntimeError:  # C++ object deleted
            _windows.pop(name, None)
    win = make_browser(sources, parent=main_window(), actions=actions, **kwargs)
    win.setWindowFlags(win.windowFlags() | Qt.Window)
    win.setWindowTitle(name)
    win.show()
    _windows[name] = win
    return win


# -- host specific examples ---------------------------------------------------
# These are starting points: each studio wires its own exporters / drag handlers
# with GalleryWidget.register_exporter() and register_drag_handler().

def media_paths(items) -> list[str]:
    """What most hosts want on a drop: the best file per item.

    Megascans assets give their albedo texture (EXR if downloaded, else JPEG);
    everything else gives ``item.path``.
    """
    out = []
    for it in items:
        path = it.path
        if it.metadata.get("source") == "megascans":
            try:
                from .sources.megascans import texture_files
                path = texture_files(it, ["albedo"]).get("albedo", path)
            except (OSError, KeyError, ValueError):
                pass
        out.append(path)
    return out


def nuke_create_reads(items) -> None:
    """Exporter (GUI thread): one Read node per item."""
    import nuke
    for path in media_paths(items):
        n = nuke.createNode("Read", inpanel=False)
        n["file"].fromUserText(path)


def houdini_create_file_cops(items) -> None:
    """Exporter (GUI thread): a File COP per item in the current COP / Copernicus network."""
    import hou
    net = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
    if net is None:
        raise RuntimeError("No network editor open")
    parent = net.pwd()
    if parent.childTypeCategory().name() not in ("Cop2", "Cop"):
        raise RuntimeError("Open a COP / Copernicus network to create File nodes")
    for it, path in zip(items, media_paths(items)):
        node = parent.createNode("file", hou.text.variableName(it.name) if hasattr(hou, "text") else None)
        parm = node.parm("filename") or node.parm("file") or node.parm("filename1")
        if parm is not None:
            parm.set(path)
        node.moveToGoodPosition()


def maya_image_planes(items) -> None:
    """Exporter (GUI thread): image plane on the selected camera (or persp)."""
    from maya import cmds
    cam = (cmds.ls(selection=True, type="transform") or ["persp"])[0]
    for path in media_paths(items):
        cmds.imagePlane(camera=cam, fileName=path)


def install_host_defaults(gallery: GalleryWidget) -> None:
    """Register example exporters + drag data for the host we're running in.

    Drags always carry file URLs + text (Nuke and Houdini accept file drops; text
    drops fill path parameters). Megascans drags carry the albedo texture.
    """
    gallery.register_drag_handler(media_paths)
    h = host()
    if h == "nuke":
        gallery.register_exporter(nuke_create_reads, label="Create Read nodes", batch=True, threaded=False)
    elif h == "houdini":
        gallery.register_exporter(houdini_create_file_cops, label="Create File COPs", batch=True,
                                  threaded=False)
    elif h == "maya":
        gallery.register_exporter(maya_image_planes, label="Image plane on camera", batch=True,
                                  threaded=False)


def host_actions() -> list[tuple[str, Callable]]:
    """Deprecated: kept for older scripts. Use install_host_defaults()."""
    return []


def show_maya(sources: list[str] | str | None = None, name: str = "ILPGallery", **kwargs):
    """Dockable Maya workspace control."""
    from maya.app.general.mayaMixin import MayaQWidgetDockableMixin
    from maya import cmds

    if isinstance(sources, str):
        sources = [sources]

    class _Dock(MayaQWidgetDockableMixin, QWidget):
        def __init__(self, parent=None):
            super().__init__(parent=parent)
            self.setObjectName(name)
            self.setWindowTitle("ILP Gallery")
            lay = QVBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            self.browser = make_browser(sources, parent=self, **kwargs)
            self.gallery = self.browser.gallery
            lay.addWidget(self.browser)

    ctrl = name + "WorkspaceControl"
    if cmds.workspaceControl(ctrl, exists=True):
        cmds.deleteUI(ctrl)
    dock = _Dock()
    dock.show(dockable=True, area="right", floating=True, width=1400, height=850)
    _windows[name] = dock
    return dock.browser


class NukeGalleryPanel(QWidget):
    """Argument-less widget for nukescripts.panels.registerWidgetAsPanel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.browser = make_browser(parent=self)
        self.gallery = self.browser.gallery
        lay.addWidget(self.browser)


def register_nuke_panel() -> None:
    import nukescripts
    nukescripts.panels.registerWidgetAsPanel(
        "ilp_gallery.dcc.NukeGalleryPanel", "ILP Gallery", "com.ilpvfx.ILPGallery"
    )
