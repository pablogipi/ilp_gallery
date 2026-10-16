"""Scene-stored UI state: restore on build, save on edits, follow scene loads."""

import json
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from ilp_gallery.core import RecordsSource, make_record  # noqa: E402
from ilp_gallery.ui.persist import MemoryStore, _decode, _encode, bind_state  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def spin(app, t=0.6):
    end = time.time() + t
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def make(app):
    from ilp_gallery.ui.widget import GalleryWidget
    w = GalleryWidget(drawer=True)
    w.add_source(RecordsSource([make_record(f"/c/{n}.fbx", id=n, name=n) for n in ("walk", "run")]),
                 background=False)
    w.show()
    spin(app, 0.2)
    return w


def test_saves_edits_and_restores_with_drawer(app):
    store = MemoryStore()
    w = make(app)
    bind_state(w, "panel", store)
    assert store.data == {}                                      # nothing stored: defaults, no write
    w.search.setText("walk")
    w.add_to_drawer([w.library.get("run")])
    w.set_collapsed("tree", True)
    spin(app)
    saved = json.loads(store.data["panel"])
    assert saved["search"] == "walk" and saved["drawer"] == [["run", "/c/run.fbx", True]]
    w.close()

    w2 = make(app)
    bind_state(w2, "panel", store)
    assert w2.search.text() == "walk" and w2.drawer.keys() == ["run"] and w2.is_collapsed("tree")
    w2.close()


def test_follows_scene_load_and_clear(app):
    store = MemoryStore()
    w = make(app)
    bind_state(w, "panel", store)
    store.data["panel"] = json.dumps({"search": "run", "drawer": [["walk", "/c/walk.fbx", False]]})
    store.emit("load")                                           # a scene with a state is opened
    assert w.search.text() == "run" and w.drawer.entries()[0].enabled is False
    store.emit("clear")                                          # File > New
    assert w.search.text() == "" and w.drawer.keys() == []
    store.data["panel"] = "{broken"
    store.emit("load")
    assert w.search.text() == ""
    w.close()


def test_ui_state_drawer_is_opt_in(app):
    w = make(app)
    assert "drawer" not in w.ui_state() and "drawer" in w.ui_state(include_drawer=True)
    w.close()


def test_encoding_round_trip():
    text = json.dumps({"search": 'a "quoted"\nline'})
    assert _decode(_encode(text)) == text and _decode(text) == text


def test_houdini_store_with_a_stub_hou(app, monkeypatch):
    import enum
    import sys
    import types

    class Ev(enum.Enum):
        AfterLoad = 1
        AfterClear = 2
        BeforeSave = 3

    data, callbacks = {}, []
    obj = types.SimpleNamespace(userData=data.get, setUserData=data.__setitem__)
    hou = types.SimpleNamespace(
        node=lambda path: obj, hipFileEventType=Ev,
        hipFile=types.SimpleNamespace(addEventCallback=callbacks.append,
                                      removeEventCallback=callbacks.remove))
    monkeypatch.setitem(sys.modules, "hou", hou)
    from ilp_gallery.ui.persist import HoudiniStore, scene_store
    assert isinstance(scene_store(), HoudiniStore)
    w = make(app)
    b = bind_state(w, "motion clips")
    w.search.setText("run")
    spin(app)
    assert json.loads(data["ilp_gallery_motion_clips"])["search"] == "run"
    data["ilp_gallery_motion_clips"] = json.dumps({"search": "walk"})
    callbacks[0](Ev.BeforeSave)                                  # ignored
    callbacks[0](Ev.AfterLoad)
    assert w.search.text() == "walk"
    b.close()
    assert callbacks == []
    w.close()


def test_houdini_parm_store_follows_the_node(app, monkeypatch):
    import contextlib
    import sys
    import types
    monkeypatch.setitem(sys.modules, "hou", types.SimpleNamespace(
        undos=types.SimpleNamespace(disabler=contextlib.nullcontext)))
    from ilp_gallery.ui.persist import HoudiniParmStore

    class Parm:
        def __init__(self, v=""): self.v = v
        def evalAsString(self): return self.v
        def set(self, v): self.v = v

    a = types.SimpleNamespace(parm=lambda n, p=Parm(): p)
    b = types.SimpleNamespace(parm=lambda n, p=Parm(json.dumps({"search": "run"})): p)
    current = [a]
    w = make(app)
    binding = bind_state(w, "node", HoudiniParmStore(lambda: current[0]), include_drawer=False)
    w.search.setText("walk")
    spin(app)
    assert json.loads(a.parm("x").v)["search"] == "walk" and "drawer" not in json.loads(a.parm("x").v)
    current[0] = b
    binding.restore()                                            # the panel switched nodes
    assert w.search.text() == "run"
    current[0] = None
    binding.restore()                                            # no node: defaults, no write
    assert w.search.text() == ""
    current[0] = a
    binding.restore()
    assert w.search.text() == "walk"
    w.close()


def test_browser_passes_the_widget_api_through(app):
    from ilp_gallery.ui.browser import GalleryBrowser
    b = GalleryBrowser(drawer=True)
    src = b.add_source(RecordsSource([make_record("/c/a.fbx", id="a", name="a")]), background=False)
    assert src is not None and callable(b.reload_source) and b.has_drawer()
    btn = b.add_toolbar_button("", lambda: None, "Reload", icon="reload")
    assert not btn.icon().isNull()
    with pytest.raises(AttributeError):
        b.no_such_thing
    b.close()
