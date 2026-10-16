"""Keep a gallery's UI state in the open scene, so it comes back with the file.

    from ilp_gallery.ui.persist import bind_state
    bind_state(gallery, "motion_clips_panel")      # Houdini / Maya / Nuke / standalone

The state is :meth:`GalleryWidget.ui_state` (search, category, collapsed panes,
sizes) plus, by default, the drawer's entries. It's written to the scene each
time the user changes something, and read back when the gallery is built and
whenever a scene is opened (the gallery follows it); File > New resets it to the
defaults the gallery had when it was bound.

Where it's kept (one entry per key):

* Houdini: user data on ``/obj``, saved in the .hip
* Maya: ``fileInfo``, saved in the .ma / .mb
* Nuke: a hidden text knob on the Root node, saved in the .nk
* standalone: QSettings (per user, not per scene)

A node's parameter: ``HoudiniParmStore(node_or_callable, "gallery_ui_state")``.
A host with another place (a database, a file) passes its own store:
any object with ``read(key) -> str``, ``write(key, text)`` and optionally
``watch(callback) -> unwatch``, where ``callback("load" | "clear")`` is called
after a scene is opened / cleared.
"""

from __future__ import annotations

import base64
import json
import logging
import weakref
from typing import Callable

log = logging.getLogger(__name__)

PREFIX = "ilp_gallery_"


def _key(key: str) -> str:
    return PREFIX + "".join(c if c.isalnum() else "_" for c in key)


def _encode(text: str) -> str:          # Maya / Nuke escape quotes and newlines their own way
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _decode(text: str) -> str:
    try:
        return base64.b64decode(text.encode("ascii"), validate=True).decode("utf-8")
    except Exception:
        return text                     # plain JSON (written by hand)


class HoudiniStore:
    def _node(self):
        import hou
        return hou.node("/obj") or hou.node("/")

    def read(self, key: str) -> str:
        return self._node().userData(_key(key)) or ""

    def write(self, key: str, text: str) -> None:
        self._node().setUserData(_key(key), text)

    def watch(self, callback: Callable[[str], None]) -> Callable[[], None]:
        import hou
        events = {hou.hipFileEventType.AfterLoad: "load", hou.hipFileEventType.AfterClear: "clear"}

        def on_event(event_type):
            if event_type in events:
                callback(events[event_type])
        hou.hipFile.addEventCallback(on_event)
        return lambda: hou.hipFile.removeEventCallback(on_event)


class MayaStore:
    def read(self, key: str) -> str:
        import maya.cmds as cmds
        found = cmds.fileInfo(_key(key), query=True)
        return _decode(found[0]) if found else ""

    def write(self, key: str, text: str) -> None:
        import maya.cmds as cmds
        cmds.fileInfo(_key(key), _encode(text))

    def watch(self, callback: Callable[[str], None]) -> Callable[[], None]:
        import maya.cmds as cmds
        jobs = [cmds.scriptJob(event=["SceneOpened", lambda: callback("load")]),
                cmds.scriptJob(event=["NewSceneOpened", lambda: callback("clear")])]

        def unwatch():
            for job in jobs:
                if cmds.scriptJob(exists=job):
                    cmds.scriptJob(kill=job, force=True)
        return unwatch


class NukeStore:
    def _knob(self, key: str, create: bool):
        import nuke
        root = nuke.root()
        knob = root.knob(_key(key))
        if knob is None and create:
            knob = nuke.String_Knob(_key(key), _key(key))
            knob.setFlag(nuke.INVISIBLE)
            root.addKnob(knob)
        return knob

    def read(self, key: str) -> str:
        knob = self._knob(key, create=False)
        return _decode(knob.value()) if knob is not None and knob.value() else ""

    def write(self, key: str, text: str) -> None:
        self._knob(key, create=True).setValue(_encode(text))

    def watch(self, callback: Callable[[str], None]) -> Callable[[], None]:
        import nuke

        def on_load():
            callback("load")

        def on_close():
            callback("clear")
        nuke.addOnScriptLoad(on_load, nodeClass="Root")
        nuke.addOnScriptClose(on_close, nodeClass="Root")

        def unwatch():
            nuke.removeOnScriptLoad(on_load, nodeClass="Root")
            nuke.removeOnScriptClose(on_close, nodeClass="Root")
        return unwatch


class HoudiniParmStore:
    """A string parameter of a node the host picks (e.g. the node a Parameters-pane
    panel shows). ``node`` is a hou.Node or a callable returning one (or None); the
    write isn't an undo step. With a callable, call ``binding.restore()`` whenever
    the node changes."""

    def __init__(self, node, parm: str = "gallery_ui_state") -> None:
        self._node = node if callable(node) else (lambda: node)
        self.parm = parm

    def _parm(self):
        node = self._node()
        return node.parm(self.parm) if node is not None else None

    def read(self, key: str) -> str:
        p = self._parm()
        return p.evalAsString() if p is not None else ""

    def write(self, key: str, text: str) -> None:
        import hou
        p = self._parm()
        if p is not None:
            with hou.undos.disabler():          # UI state isn't an undoable edit
                p.set(text)


class SettingsStore:
    """Per user, not per scene: for standalone galleries."""

    def __init__(self, org: str = "ILP", app: str = "ILPGallery") -> None:
        from PySide6.QtCore import QSettings
        self._settings = QSettings(org, app)

    def read(self, key: str) -> str:
        return str(self._settings.value(f"ui_state/{key}", "") or "")

    def write(self, key: str, text: str) -> None:
        self._settings.setValue(f"ui_state/{key}", text)


class MemoryStore:
    """In memory: tests, or a host that persists ``data`` itself."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self._callbacks: list[Callable[[str], None]] = []

    def read(self, key: str) -> str:
        return self.data.get(key, "")

    def write(self, key: str, text: str) -> None:
        self.data[key] = text

    def watch(self, callback):
        self._callbacks.append(callback)
        return lambda: self._callbacks.remove(callback)

    def emit(self, event: str) -> None:            # simulate a scene load / clear
        for cb in list(self._callbacks):
            cb(event)


def scene_store():
    """The store for the host this runs in (see the module docstring)."""
    from ..dcc import host
    return {"houdini": HoudiniStore, "maya": MayaStore, "nuke": NukeStore}.get(host(), SettingsStore)()


class StateBinding:
    """Keeps one gallery's state in a store under one key; see :func:`bind_state`."""

    def __init__(self, gallery, key: str, store, include_drawer: bool) -> None:
        self.key = key
        self.store = store
        self.include_drawer = include_drawer
        self._gallery = weakref.ref(gallery)
        self.defaults = gallery.ui_state(include_drawer)      # what File > New goes back to
        self._last = ""
        self._unwatch = store.watch(self._on_scene) if hasattr(store, "watch") else None
        gallery.uiStateChanged.connect(self.save)
        if include_drawer:
            gallery.drawerChanged.connect(self.save)
        gallery.destroyed.connect(self.close)
        self.restore()

    def restore(self) -> None:
        """Apply the stored state (the defaults when there is none or it's broken)."""
        g = self._gallery()
        if g is None:
            return
        try:
            text = self.store.read(self.key)
        except Exception:
            log.exception("reading the gallery state failed")
            text = ""
        try:
            g.set_ui_state(text or self.defaults)
        except ValueError:
            g.set_ui_state(self.defaults)
        self._last = text

    def save(self, *_args) -> None:
        g = self._gallery()
        if g is None:
            return
        text = json.dumps(g.ui_state(self.include_drawer), sort_keys=True)
        if text == self._last:
            return
        try:
            self.store.write(self.key, text)
            self._last = text
        except Exception:
            log.exception("saving the gallery state failed")

    def _on_scene(self, event: str) -> None:
        g = self._gallery()
        if g is None:
            self.close()
        elif event == "clear":
            g.set_ui_state(self.defaults)
            self._last = ""
        else:
            self.restore()

    def close(self, *_args) -> None:
        if self._unwatch is not None:
            try:
                self._unwatch()
            except Exception:
                pass
            self._unwatch = None


def bind_state(gallery, key: str, store=None, include_drawer: bool = True) -> StateBinding:
    """Restore ``gallery``'s state from ``store`` (default: the open scene, see
    :func:`scene_store`) and keep it there from now on. Call it once the gallery's
    sources and options are set up: the state it has then is the default for
    scenes without a stored one."""
    binding = StateBinding(gallery, key, store or scene_store(), include_drawer)
    gallery._state_binding = binding                    # lives as long as the gallery
    return binding
