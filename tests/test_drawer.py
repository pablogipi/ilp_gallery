"""The drawer: entries keyed by name, states against the library, edits and badges."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QModelIndex, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QDropEvent  # noqa: E402

from ilp_gallery.core import RecordsSource, make_record  # noqa: E402
from ilp_gallery.ui.drawer import (  # noqa: E402
    STATE_MISSING, STATE_OK, STATE_OUTDATED, DrawerEntry, as_entry,
)


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def wait(app, cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def clip(name, version):
    return make_record(f"/clips/{name}_v{version:03d}.fbx", id=f"{name}-v{version}", name=name)


@pytest.fixture
def gallery(app, tmp_path):
    from ilp_gallery.media.thumbs import ThumbnailCache
    from ilp_gallery.ui.widget import GalleryWidget
    cache = ThumbnailCache(cache_dir=str(tmp_path / "cache"))
    w = GalleryWidget(cache=cache, drawer=True, drawer_title="Clips")
    w.records = [clip("walk", 1), clip("run", 2), clip("idle", 1)]
    w.src = RecordsSource(lambda: list(w.records), name="clips")
    w.add_source(w.src, background=False)
    w.resize(1400, 700)
    w.show()
    assert wait(app, lambda: len(w._drawer_index) == 3)
    w.changes = []
    w.drawerChanged.connect(w.changes.append)
    yield w
    w.close()
    cache.shutdown()


def items(g, *names):
    return [g._drawer_index[n] for n in names]


def test_as_entry_accepts_tuples_dicts_and_keys():
    assert as_entry(("walk", "/a.fbx", False)) == DrawerEntry("walk", "/a.fbx", False)
    assert as_entry({"key": "run", "path": "/b.fbx"}) == DrawerEntry("run", "/b.fbx", True)
    assert as_entry("idle") == DrawerEntry("idle", "", True)


def test_set_drawer_is_silent_and_resolves_states(app, gallery):
    gallery.set_drawer([("walk", "/clips/walk_v001.fbx"),
                        ("run", "/clips/run_v001.fbx"),             # library has v002
                        ("gone", "/clips/gone_v001.fbx", False)])
    assert gallery.changes == []
    assert gallery.drawer.states() == {"walk": STATE_OK, "run": STATE_OUTDATED, "gone": STATE_MISSING}
    marks = gallery.delegate.marks
    assert marks == {"walk-v1": (True, STATE_OK), "run-v2": (True, STATE_OUTDATED)}
    assert gallery.drawer.status.text() == "1 outdated  ·  1 missing"
    assert gallery.drawer.update_btn.isVisibleTo(gallery)


def test_add_skips_duplicates_and_emits(app, gallery):
    assert gallery.add_to_drawer(items(gallery, "walk", "run")) == 2
    assert gallery.add_to_drawer(items(gallery, "run", "idle")) == 1
    assert gallery.drawer.keys() == ["walk", "run", "idle"]
    assert len(gallery.changes) == 2
    last = gallery.changes[-1]
    assert last[1] == DrawerEntry("run", "/clips/run_v002.fbx", True)


def test_new_version_shows_outdated_until_updated(app, gallery):
    gallery.add_to_drawer(items(gallery, "walk"))
    gallery.records[0] = clip("walk", 2)                       # a new version is published
    gallery.reload_source(gallery.src, background=False)
    assert wait(app, lambda: gallery.drawer.states() == {"walk": STATE_OUTDATED})
    assert gallery.drawer.entries()[0].path == "/clips/walk_v001.fbx"   # the stored path stays
    assert gallery.drawer.update_to_latest() == 1
    assert gallery.drawer.entries()[0].path == "/clips/walk_v002.fbx"
    assert gallery.drawer.states() == {"walk": STATE_OK}
    assert gallery.delegate.marks == {"walk-v2": (True, STATE_OK)}


def test_toggle_remove_and_reorder(app, gallery):
    gallery.add_to_drawer(items(gallery, "walk", "run", "idle"))
    lst = gallery.drawer.list
    lst.item(1).setCheckState(Qt.Unchecked)                   # the checkbox in the row
    assert gallery.changes[-1][1] == DrawerEntry("run", "/clips/run_v002.fbx", False)
    assert gallery.delegate.marks["run-v2"] == (False, STATE_OK)

    lst.model().moveRow(QModelIndex(), 0, QModelIndex(), 3)    # walk to the end
    assert gallery.drawer.keys() == ["run", "idle", "walk"]
    assert [e.key for e in gallery.changes[-1]] == ["run", "idle", "walk"]

    lst.item(1).setSelected(True)
    gallery.drawer.remove_selected()
    assert gallery.drawer.keys() == ["run", "walk"]
    assert gallery.delegate.marks.keys() == {"run-v2", "walk-v1"}
    gallery.drawer.clear()
    assert gallery.drawer.keys() == [] and gallery.delegate.marks == {}


def test_drop_from_gallery(app, gallery):
    rows = [gallery.model.index_of(i) for i in ("walk-v1", "idle-v1")]
    md = gallery.model.mimeData(rows)
    lst = gallery.drawer.list
    ev = QDropEvent(QPointF(10, 10), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
    lst.dropEvent(ev)
    assert ev.isAccepted()
    assert gallery.drawer.keys() == ["walk", "idle"]


def test_drawer_items_and_duplicate_names_warning(app, gallery):
    logs = []
    gallery.logMessage.connect(logs.append)
    gallery.set_drawer([("walk", "/clips/walk_v001.fbx", True), ("run", "/clips/run_v002.fbx", False)])
    assert [i.id for i in gallery.drawer_items()] == ["walk-v1", "run-v2"]
    assert [i.id for i in gallery.drawer_items(enabled_only=True)] == ["walk-v1"]

    gallery.records.append(make_record("/other/walk.fbx", id="other-walk", name="walk"))
    gallery.reload_source(gallery.src, background=False)
    assert wait(app, lambda: any("used by more than one item" in m for m in logs))


def test_drawer_hidden_by_default(app, tmp_path):
    from ilp_gallery.ui.widget import GalleryWidget
    w = GalleryWidget()
    assert not w.has_drawer()
    w.close()


def test_path_key_and_entry_factory(app, tmp_path):
    """A Megascans-style batch: keyed by id, storing the asset JSON as the path."""
    from ilp_gallery.ui.widget import GalleryWidget
    w = GalleryWidget(drawer=True, drawer_key="id", drawer_path="json")
    recs = [make_record(f"/ms/{i}/preview.jpg", id=i, name="Rock", json=f"/ms/{i}/{i}.json")
            for i in ("aa1", "bb2")]
    w.add_source(RecordsSource(recs, name="megascans"), background=False)
    assert wait(app, lambda: len(w._drawer_index) == 2)     # same name twice is fine: keyed by id
    w.add_to_drawer(list(w._drawer_index.values()))
    assert w.drawer_entries() == [DrawerEntry("aa1", "/ms/aa1/aa1.json"), DrawerEntry("bb2", "/ms/bb2/bb2.json")]
    assert set(w.drawer.states().values()) == {STATE_OK}

    w.drawer.clear()
    w.drawer.entry_factory = lambda it: (it.id.upper(), it.path, False) if it.id != "bb2" else None
    w.add_to_drawer(list(w._drawer_index.values()))
    assert w.drawer_entries() == [DrawerEntry("AA1", "/ms/aa1/preview.jpg", False)]
    w.close()


def test_run_button_exports_enabled_entries_in_order(app, gallery):
    assert not gallery.drawer.run_btn.isVisibleTo(gallery)        # no exporter yet
    got = []
    gallery.register_exporter(lambda paths: got.extend(paths), label="Ingest", arg="path",
                              batch=True, threaded=False)
    assert gallery.drawer.run_btn.isVisibleTo(gallery)
    gallery.add_to_drawer(items(gallery, "idle", "walk", "run"))
    gallery.drawer.set_enabled_keys(["walk"], False)
    assert gallery.drawer.run_btn.text() == "Ingest (2)"
    gallery.drawer.run_btn.click()
    assert got == ["/clips/idle_v001.fbx", "/clips/run_v002.fbx"]


def test_clicking_a_row_plays_it_in_details(app, gallery):
    gallery.add_to_drawer(items(gallery, "walk", "run"))
    gallery.drawer.list.setCurrentRow(1)
    assert gallery.details.item.id == "run-v2"
    gallery.set_details_visible(False)
    gallery.drawer.list.setCurrentRow(0)
    assert gallery.details.item.id == "run-v2"                  # closed panel: left alone


def test_rows_dragged_out_onto_the_gallery_are_removed(app, gallery, monkeypatch):
    gallery.add_to_drawer(items(gallery, "walk", "run", "idle"))
    lst = gallery.drawer.list
    md = lst.mimeData([lst.item(0), lst.item(2)])
    assert md.hasFormat("application/x-ilp-drawer-keys")
    monkeypatch.setattr(gallery, "_drawer_drag_out", lambda e: True)   # synthetic events have no source()
    ev = QDropEvent(QPointF(300, 300), Qt.CopyAction | Qt.MoveAction, md, Qt.LeftButton, Qt.NoModifier)
    gallery.dropEvent(ev)
    assert ev.isAccepted() and ev.dropAction() == Qt.CopyAction   # Copy: Qt doesn't delete rows too
    assert wait(app, lambda: gallery.drawer.keys() == ["run"])


def test_drawer_context_menu_actions(app, gallery):
    gallery.add_to_drawer(items(gallery, "walk", "run"))
    got = []
    gallery.add_drawer_action("Set As Current", lambda es: got.append([e.key for e in es]), single=True)
    gallery.add_drawer_action("Print", lambda es: got.append(len(es)))
    gallery.add_drawer_action("Broken", lambda es: 1 / 0)
    logs = []
    gallery.logMessage.connect(logs.append)

    labels = lambda m: [a.text() for a in m.actions() if not a.isSeparator()]
    one = gallery.drawer.context_menu(["run"])
    assert labels(one)[:3] == ["Set As Current", "Print", "Broken"] and "Remove Item" in labels(one)
    two = gallery.drawer.context_menu(["run", "walk"])
    assert "Set As Current" not in labels(two) and "Remove Items (2)" in labels(two)

    next(a for a in one.actions() if a.text() == "Set As Current").trigger()
    next(a for a in two.actions() if a.text() == "Print").trigger()
    next(a for a in two.actions() if a.text() == "Broken").trigger()
    assert got == [["run"], 2] and any("Drawer action failed" in m for m in logs)
    next(a for a in two.actions() if a.text().startswith("Remove Items")).trigger()
    assert gallery.drawer.keys() == []
    assert gallery.drawer.context_menu([]) is None
