"""UI state: dump, restore (also before the library has loaded), change signal, files."""

import json
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from ilp_gallery.core import RecordsSource, make_record  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def spin(app, t=0.2):
    end = time.time() + t
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


RECORDS = [make_record(f"/c/{n}.fbx", name=n, category_paths=[[cat, n]])
           for n, cat in (("walk", "Locomotion"), ("run", "Locomotion"), ("idle", "Idle"))]


def make(app, load=True, **kw):
    from ilp_gallery.ui.widget import GalleryWidget
    w = GalleryWidget(drawer=True, **kw)
    if load:
        w.add_source(RecordsSource(list(RECORDS)), background=False)
    w.resize(1400, 700)
    w.show()
    spin(app)
    return w


def test_round_trip(app):
    w = make(app)
    w.search.setText("walk")
    w.tree.select_path(("locomotion",))
    w.set_collapsed("tree", True)
    w.size_slider.setValue(200)
    spin(app, 0.3)
    state = w.ui_state()
    assert state["search"] == "walk" and state["category"] == ["locomotion"]
    assert state["collapsed"] == ["tree"] and state["tile_size"] == 200
    assert state["sizes"]["tree"] > 100 and state["sizes"]["column"] > 100
    json.dumps(state)                                         # plain data
    w.close()

    w2 = make(app, ui_state=json.dumps(state))                # restored at build, from JSON
    spin(app, 0.3)
    assert w2.search.text() == "walk" and w2.tree.current_path() == ("locomotion",)
    assert w2.is_collapsed("tree") and not w2.is_collapsed("drawer")
    assert w2.size_slider.value() == 200
    assert w2.model.ids() == ["/c/walk.fbx"]                          # search + category applied
    w2.set_collapsed("tree", False)
    spin(app)
    assert abs(w2.panes["tree"].width() - state["sizes"]["tree"]) <= 2
    w2.close()


def test_category_restored_once_the_library_loads(app):
    w = make(app, load=False)
    w.set_ui_state({"category": ["idle"]})
    assert w.ui_state()["category"] == ["idle"]               # kept while pending
    w.add_source(RecordsSource(list(RECORDS)), background=False)
    spin(app, 0.3)
    assert w.tree.current_path() == ("idle",) and w.model.ids() == ["/c/idle.fbx"]
    w.close()


def test_change_signal_debounced_and_not_for_restores(app):
    w = make(app)
    got = []
    w.uiStateChanged.connect(got.append)
    w.set_ui_state({"search": "run", "collapsed": ["details"]})
    spin(app, 0.6)
    assert got == []                                           # restoring isn't a change
    w.search.setText("idle")
    w.set_collapsed("drawer", True)
    spin(app, 0.8)
    assert len(got) == 1 and got[0]["search"] == "idle" and "drawer" in got[0]["collapsed"]
    w.set_collapsed("drawer", True)                            # no change, no signal
    spin(app, 0.6)
    assert len(got) == 1
    w.close()


def test_save_and_load_file(app, tmp_path):
    w = make(app)
    w.search.setText("walk")
    spin(app, 0.3)
    path = tmp_path / "state.json"
    w.save_ui_state(str(path))
    w.search.setText("")
    w.load_ui_state(str(path))
    assert w.search.text() == "walk"
    with pytest.raises(ValueError):
        w.set_ui_state("[1, 2]")
    w.set_ui_state("")                                          # empty string: nothing to do
    w.close()


def test_drawer_current_entry(app):
    w = make(app)
    w.add_to_drawer([w._drawer_index["walk"], w._drawer_index["run"]])
    w.set_drawer_current("run")
    lst = w.drawer.list
    assert lst.item(1).font().bold() and not lst.item(0).font().bold()
    assert "Current" in lst.item(1).toolTip()
    w.drawer.set_enabled_keys(["walk"], False)                  # rebuilds keep the mark
    assert w.drawer.list.item(1).font().bold()
    w.set_drawer_current(None)
    assert not w.drawer.list.item(1).font().bold()
    w.close()


def test_dragging_a_splitter_is_reported(app):
    w = make(app)
    got = []
    w.uiStateChanged.connect(got.append)
    s = w.area.splitter
    sizes = s.sizes()
    s.setSizes([sizes[0] + 60, sizes[1] - 60] + sizes[2:])
    spin(app, 0.6)
    assert got == []                                           # a layout change alone: no
    s.splitterMoved.emit(sizes[0] + 60, 1)                     # what a drag sends
    spin(app, 0.6)
    assert len(got) == 1 and got[0]["sizes"]["tree"] == sizes[0] + 60
    w.close()
