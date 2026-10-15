"""The public API: data sources, exporters, drag handlers, compressed caches."""

import json
import os
import threading
import time

import pytest

from ilp_gallery.core import (
    Changes, DataSource, GalleryLibrary, JsonFileSource, RecordsSource, make_record, read_json,
    source_for_path, write_json,
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class Publishes(DataSource):
    """Stand-in for a pipeline source (e.g. ShotGrid published files)."""
    name = "publishes"
    label = "Published assets"

    def __init__(self, records):
        super().__init__()
        self.records = records
        self.pending = None
        self.tag_writes = []
        self.load_thread = None

    def load(self):
        self.load_thread = threading.current_thread()
        return list(self.records)

    def poll(self):
        p, self.pending = self.pending, None
        return p

    def thumbnail(self, item):
        return item.metadata.get("thumb_bytes")

    def set_tags(self, item, tags):
        self.tag_writes.append((item.id, tags))


def pub(i, **kw):
    return make_record(f"/show/assets/chair_{i}.usd", id=f"pub-{i}", name=f"Chair {i}",
                       categories=["props"], tags=["wood"], type_label="Model",
                       category_paths=[["assets", "props", "chairs"]],
                       display={"Version": i, "Artist": "pablo"}, version=i, **kw)


# -- core --------------------------------------------------------------------
def test_make_record_is_the_internal_format():
    rec = pub(3, tile_aspect=1.0)
    lib = GalleryLibrary()
    lib.add_records([rec])
    it = lib.get("pub-3")
    assert it.type_label == "Model"
    assert it.category_paths == [["assets", "props", "chairs"]]
    assert it.display == [["Version", 3], ["Artist", "pablo"]]
    assert it.value("version") == 3 and it.value("path").endswith("chair_3.usd")
    assert lib.search("meta:pablo") == ["pub-3"]


def test_custom_source_load_poll_and_writes():
    src = Publishes([pub(1), pub(2)])
    lib = GalleryLibrary()
    assert lib.add_source(src) == 2
    assert lib.get("pub-1").source == "publishes" and lib.source_of("pub-1") is src

    src.pending = Changes(updated=[pub(3)], removed=["pub-1"])
    assert lib.apply_changes(src, src.poll())
    assert set(lib.ids()) == {"pub-2", "pub-3"}
    assert lib.search("chair") and "pub-1" not in lib.search("chair")

    assert not src.is_read_only()
    lib.set_tags(lib.get("pub-2"), ["wood", "approved"])
    assert src.tag_writes == [("pub-2", ["wood", "approved"])]
    assert lib.search("tag:approved") == ["pub-2"]

    ro = RecordsSource([pub(9)], name="ro")
    lib.add_source(ro)
    with pytest.raises(PermissionError):
        lib.set_tags(lib.get("pub-9"), ["x"])


def test_sources_do_not_clobber_each_other():
    lib = GalleryLibrary()
    a = RecordsSource([pub(1), pub(2)], name="a")
    b = RecordsSource([make_record("/x/b.png", id="b-1")], name="b")
    lib.add_source(a)
    lib.add_source(b)
    a._records = [pub(2)]                   # pub-1 disappeared from source a
    lib.reload_source(a)
    assert set(lib.ids()) == {"pub-2", "b-1"}
    lib.remove_source("b")
    assert lib.ids() == ["pub-2"]


def test_gzip_cache_roundtrip_and_detection(tmp_path):
    out = tmp_path / "lib.json.gz"
    write_json(str(out), {"source": "megascans", "root": "/r", "items": [pub(1)]})
    assert out.read_bytes()[:2] == b"\x1f\x8b"
    assert read_json(str(out))["items"][0]["id"] == "pub-1"
    src = source_for_path(str(out))
    assert type(src).__name__ == "MegascansSource" and src.root == "/r"
    plain = tmp_path / "plain.json"
    write_json(str(plain), {"items": [pub(2)]})
    assert isinstance(source_for_path(str(plain)), JsonFileSource)
    lib = GalleryLibrary()
    lib.load(str(plain))
    lib.save(str(tmp_path / "saved.json.gz"))
    assert read_json(str(tmp_path / "saved.json.gz"))["items"][0]["id"] == "pub-2"


def test_json_source_poll_detects_rebuild(tmp_path):
    f = tmp_path / "lib.json"
    write_json(str(f), {"items": [pub(1)]})
    src = JsonFileSource(str(f))
    lib = GalleryLibrary()
    lib.add_source(src)
    assert src.poll() is None
    time.sleep(0.01)
    write_json(str(f), {"items": [pub(1), pub(2)]})
    ch = src.poll()
    assert ch and ch.reset
    lib.apply_changes(src, ch)
    assert len(lib) == 2


# -- UI ------------------------------------------------------------------------
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


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


@pytest.fixture
def gallery(app, tmp_path):
    from ilp_gallery.media.thumbs import ThumbnailCache
    from ilp_gallery.ui.widget import GalleryWidget
    cache = ThumbnailCache(cache_dir=str(tmp_path / "cache"))
    w = GalleryWidget(cache=cache)
    w.resize(1200, 700)
    w.show()
    yield w
    w.close()
    cache.shutdown()


def test_background_load_and_poll(app, gallery):
    src = Publishes([pub(i) for i in range(50)])
    src.poll_interval = 0.2
    loaded = []
    gallery.sourceLoaded.connect(lambda s, n: loaded.append(n))
    gallery.add_source(src)                               # background by default
    assert wait(app, lambda: loaded == [50])
    assert src.load_thread is not threading.main_thread()
    assert wait(app, lambda: gallery.model.rowCount() == 50)
    tree = gallery.tree
    assert ("assets", "props", "chairs") in tree.ids

    src.pending = Changes(updated=[pub(100)], removed=["pub-0"])
    assert wait(app, lambda: gallery.model.rowCount() == 50 and gallery.model.index_of("pub-100").isValid(), 5)


def test_source_thumbnail_bytes(app, gallery, tmp_path):
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QColor, QImage
    img = QImage(64, 64, QImage.Format_RGB32)
    img.fill(QColor("#336699"))
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    rec = pub(1, thumb_bytes=None)
    rec["metadata"]["thumb_bytes"] = bytes(ba)            # e.g. downloaded from ShotGrid
    gallery.add_source(Publishes([rec]), background=False)
    it = gallery.library.get("pub-1")                     # path doesn't exist: remote asset
    assert wait(app, lambda: gallery.cache.poster(it) is not None or gallery.cache.failed(it.id))
    c = gallery.cache.poster(it).toImage().pixelColor(10, 10)
    assert abs(c.red() - 0x33) <= 3 and abs(c.green() - 0x66) <= 3 and abs(c.blue() - 0x99) <= 3  # JPEG


def test_exporters(app, gallery):
    gallery.add_source(Publishes([pub(1), pub(2)]), background=False)
    wait(app, lambda: gallery.model.rowCount() == 2, 2)
    got, main_thread_calls, logs = [], [], []
    gallery.logMessage.connect(logs.append)

    def ingest(json_or_path, log):                      # receives the chosen field + log
        log(f"ingest {json_or_path}")
        got.append((json_or_path, threading.current_thread() is threading.main_thread()))

    gallery.register_exporter(ingest, label="Ingest", arg="path", default=True)
    gallery.register_exporter(lambda items: main_thread_calls.append(
        (len(items), threading.current_thread() is threading.main_thread())),
        label="Build in scene", batch=True, threaded=False)
    gallery.register_exporter(lambda v: None, label="Only megascans", arg="json", sources=["megascans"])

    items = [gallery.library.get("pub-1"), gallery.library.get("pub-2")]
    assert [e.label for e in gallery.exporters(items)] == ["Ingest", "Build in scene"]  # 3rd doesn't apply

    done = []
    gallery.taskFinished.connect(done.append)
    assert gallery.export(items)
    assert wait(app, lambda: done == [True])
    assert [g[0] for g in got] == ["/show/assets/chair_1.usd", "/show/assets/chair_2.usd"]
    assert not any(g[1] for g in got)                    # ran on a worker thread
    assert any("ingest /show/assets/chair_1.usd" in l for l in logs)

    gallery.export(items, gallery.exporters(items)[1])
    assert main_thread_calls == [(2, True)]              # batch, on the GUI thread

    def boom(v):
        raise RuntimeError("disk full")
    ex = gallery.register_exporter(boom, label="Broken")
    done.clear()
    gallery.export(items[:1], ex)
    assert wait(app, lambda: done == [False])
    assert any("disk full" in l for l in logs)


def test_export_stop_between_items(app, gallery):
    gallery.add_source(Publishes([pub(i) for i in range(20)]), background=False)
    calls = []
    gallery.register_exporter(lambda it: (calls.append(it.id), time.sleep(0.1)), label="Slow")
    done = []
    gallery.taskFinished.connect(done.append)
    gallery.export(list(gallery.library))
    wait(app, lambda: len(calls) >= 2, 5)
    gallery.stop_task()
    assert wait(app, lambda: done == [False], 5)
    assert len(calls) < 20


def test_drag_handlers(app, gallery):
    from PySide6.QtCore import QMimeData
    gallery.add_source(Publishes([pub(1)]), background=False)
    wait(app, lambda: gallery.model.rowCount() == 1, 2)
    idx = [gallery.model.index_of("pub-1")]

    md = gallery.model.mimeData(idx)                      # default: file URL + JSON
    assert md.urls()[0].toLocalFile() == "/show/assets/chair_1.usd"

    gallery.register_drag_handler(lambda items: [f"/tex/{i.id}_albedo.exr" for i in items])
    gallery.register_drag_handler(lambda items: {"application/x-houdini-node": "cop:file"})
    md = gallery.model.mimeData(idx)
    assert md.urls()[0].toLocalFile() == "/tex/pub-1_albedo.exr"
    assert bytes(md.data("application/x-houdini-node")) == b"cop:file"
    assert md.hasFormat("application/x-ilp-gallery-items")  # default kept

    def only(items):
        m = QMimeData()
        m.setText("replaced")
        return m
    gallery.register_drag_handler(only, replace_default=True)
    md = gallery.model.mimeData(idx)
    assert md.text() == "replaced"
    assert md.hasFormat("application/x-ilp-gallery-items")  # records always kept (drawer)

    gallery.register_drag_handler(lambda items: 1 / 0)    # broken handler: drag still works
    assert gallery.model.mimeData(idx) is not None


# -- examples -------------------------------------------------------------------
class FakeShotgun:
    """Just enough of shotgun_api3.Shotgun for the example source."""

    def __init__(self):
        self.pubs = [self._pub(1, "chairA_model"), self._pub(2, "chairA_rig", "Rig")]
        self.calls = []

    @staticmethod
    def _pub(i, code, ptype="Model"):
        return {"type": "PublishedFile", "id": i, "code": code, "version_number": i,
                "path": {"local_path": f"/show/pub/{code}.usd"},
                "entity": {"type": "Asset", "id": 7, "name": "chairA"},
                "published_file_type": {"name": ptype}, "tags": [{"name": "hero"}],
                "created_by": {"name": "pablo"}, "sg_status_list": "apr", "image": None}

    def find(self, entity, filters, fields=None, order=None):
        self.calls.append((entity, filters))
        if any(f[0] == "updated_at" for f in filters):
            return [self._pub(3, "chairA_lookdev", "Shader")]
        return list(self.pubs)


def test_shotgrid_example_source():
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "examples"))
    from shotgrid_source import ShotGridPublishes
    sg = FakeShotgun()
    src = ShotGridPublishes(sg, project_id=123, types=["Model", "Rig"])
    lib = GalleryLibrary()
    assert lib.add_source(src) == 2
    it = lib.get("sg-pub-2")
    assert it.type_label == "Rig" and it.category_paths == [["Asset", "chairA", "Rig"]]
    assert ["Artist", "pablo"] in it.display
    assert lib.search("tag:hero cat:rig") == ["sg-pub-2"]
    assert ["published_file_type.PublishedFileType.code", "in", ["Model", "Rig"]] in sg.calls[0][1]
    lib.apply_changes(src, src.poll())                  # incremental query by updated_at
    assert "sg-pub-3" in lib and len(lib) == 3


def test_megascans_ingest_example_callback():
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "examples"))
    import megascans_ingest
    lines = []
    assert megascans_ingest.ingest("/x/abc.json", lines.append) is True
    assert lines == ["Ingesting /x/abc.json"]


def test_source_failure_reports_full_traceback(app, gallery, caplog):
    """A failing load() shows the whole traceback (log panel + logging), down to the
    line in the source's own code, even though it ran on a worker thread."""
    import logging
    from ilp_gallery.core import DataSource

    def parse_publish(p):
        return p["path"]["local_path"]

    class Broken(DataSource):
        name, label = "broken", "Broken source"

        def load(self):
            return [parse_publish({"code": "x"})]

    lines, failed = [], []
    gallery.logMessage.connect(lines.append)
    gallery.sourceFailed.connect(lambda src, msg: failed.append(msg))
    with caplog.at_level(logging.ERROR, logger="ilp_gallery"):
        gallery.add_source(Broken())
        assert wait(app, lambda: failed)
    assert failed == ["KeyError: 'path'"]                       # the signal keeps the short form
    err = next(m for m in lines if m.startswith("ERROR loading Broken source"))
    assert "Traceback (most recent call last)" in err
    assert "in parse_publish" in err and "in load" in err       # down to the user's code
    assert any("in parse_publish" in r.getMessage() for r in caplog.records)


def test_make_record_category():
    from ilp_gallery.core import GalleryItem, make_record
    r = make_record("/p/run.fbx", name="locomotion__run__fwd", category="locomotion",
                    category_paths=[["locomotion", "run"]])
    it = GalleryItem.from_dict(r)
    assert it.category == "locomotion"
    assert it.category_paths == [["locomotion", "run"]]          # the tree only
    assert it.categories == ["locomotion", "run"]                 # cat: finds both levels
    only = GalleryItem.from_dict(make_record("/p/a.fbx", category="crowd"))
    assert only.category_paths == [["crowd"]] and only.categories == ["crowd"]
    explicit = GalleryItem.from_dict(make_record("/p/b.fbx", category="crowd", categories=["x"]))
    assert explicit.categories == ["x"] and explicit.category == "crowd"
    legacy = GalleryItem.from_dict(make_record("/p/c.fbx", categories=["fx", "fire"]))
    assert legacy.category == "fx"                                # no category: first one
    tree = GalleryItem.from_dict(make_record("/p/d.fbx", category_paths=[["props", "chairs"]]))
    assert tree.category == "props"
