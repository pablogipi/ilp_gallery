"""Favourites (a star, a pinned tree row, saved per user per key) and collections."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from ilp_gallery.core import RecordsSource, make_record  # noqa: E402
from ilp_gallery.ui.persist import MemoryStore  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


RECS = [make_record(f"/x/{i}.jpg", id=f"i{i}", name=f"Item {i}",
                    collections=["Environment/Industrial/Mine"] if i < 2 else
                    (["Environment/Urban"] if i == 2 else None)) for i in range(5)]


def make(app, tmp_path, store, **kw):
    from ilp_gallery.media.thumbs import ThumbnailCache
    from ilp_gallery.ui.widget import GalleryWidget
    cache = ThumbnailCache(cache_dir=str(tmp_path / f"cache{id(store)}{len(kw)}"))
    w = GalleryWidget(cache=cache, favourites_store=store, **kw)
    w.add_source(RecordsSource(lambda: list(RECS), name="x"), background=False)
    w.show()
    settle(app)
    w._cache_for_test = cache
    return w


def settle(app, seconds=0.6):
    import time
    end = time.time() + seconds
    while time.time() < end:               # library refreshes and filters run on short timers
        app.processEvents()


def close(*ws):
    for w in ws:
        w.close()
        w._cache_for_test.shutdown()


def item(w, iid):
    return w.library.get(iid)


def test_star_tree_row_and_storage(app, tmp_path):
    store = MemoryStore()
    w = make(app, tmp_path, store)
    try:
        path = w.FAVOURITES_PATH
        assert w.tree._rows[path][0].text() == "Favourites" and w.tree.ids[path] == set()
        seen = []
        w.favouritesChanged.connect(seen.append)
        w.set_favourite([item(w, "i1"), item(w, "i3")])
        assert w.delegate.favourites == {"i1", "i3"} and w.tree.ids[path] == {"i1", "i3"}
        assert [it.id for it in seen[-1]] == ["i1", "i3"]
        assert store.read("favourites/default") == '["x:i1", "x:i3"]'     # source + id
        w.tree.select_path(path)
        settle(app, 0.2)
        assert sorted(w.model.ids()) == ["i1", "i3"]
        w.toggle_favourite(item(w, "i1"))
        settle(app, 0.2)
        assert sorted(w.model.ids()) == ["i3"]                  # the filtered view follows
        w.details.set_item(item(w, "i3"))
        assert w.details.fav_btn.isVisibleTo(w.details) and w.details.fav_btn.isChecked()
        w.details.fav_btn.click()                               # the details star
        assert not w.is_favourite(item(w, "i3"))
    finally:
        close(w)


def test_favourites_persist_per_key_and_sync(app, tmp_path):
    store = MemoryStore()
    a = make(app, tmp_path, store, favourites_key="megascans")
    b = make(app, tmp_path, store, favourites_key="megascans", view_mode="list")
    c = make(app, tmp_path, store, favourites_key="clips")
    try:
        a.set_favourite(item(a, "i2"))
        assert b.is_favourite(item(b, "i2"))                    # open galleries with the key follow
        assert not c.is_favourite(item(c, "i2"))                # other keys don't
    finally:
        close(a, b, c)
    d = make(app, tmp_path, store, favourites_key="megascans")
    try:
        assert d.delegate.favourites == {"i2"}                  # saved: back on the next start
    finally:
        close(d)


def test_favourites_off(app, tmp_path):
    w = make(app, tmp_path, MemoryStore(), favourites=False)
    try:
        assert w.FAVOURITES_PATH not in w.tree._rows
        w.set_favourite(item(w, "i1"))
        assert not w.is_favourite(item(w, "i1")) and not w.delegate.favourites
        w.details.set_item(item(w, "i1"))
        assert not w.details.fav_btn.isVisibleTo(w.details)
    finally:
        close(w)


def test_collections_branch(app, tmp_path):
    w = make(app, tmp_path, MemoryStore())
    try:
        t = w.tree
        root = ("@collections",)
        assert t._rows[root][0].text() == "Collections"
        assert t.ids[root] == {"i0", "i1", "i2"}
        assert t.ids[root + ("Environment", "Industrial", "Mine")] == {"i0", "i1"}
        assert t._rows[root + ("Environment", "Urban")][0].text() == "Urban"     # names as given
        tops = [t._model.item(r, 0).text() for r in range(t._model.rowCount())]
        assert tops.index("Favourites") < tops.index("Collections")
        t.select_path(root + ("Environment", "Industrial"))
        settle(app, 0.2)
        assert sorted(w.model.ids()) == ["i0", "i1"]
        w.details.set_item(item(w, "i0"))
        assert "Collections" in w.details.info.text() and "Mine" in w.details.info.text()
    finally:
        close(w)


def test_make_record_collections():
    from ilp_gallery.core.item import GalleryItem
    rec = make_record("/a.jpg", collections=[["Environment", "Industrial"], "Vegetation/Forest"])
    assert rec["metadata"]["collections"] == [["Environment", "Industrial"], ["Vegetation", "Forest"]]
    it = GalleryItem(path="/a.jpg", id="a", name="a", metadata={"collections": "Essential"})
    assert it.collections == [["Essential"]]


def test_collection_groups_after_single_collections(app, tmp_path):
    from ilp_gallery.media.thumbs import ThumbnailCache
    from ilp_gallery.ui.widget import GalleryWidget
    recs = [make_record("/a.jpg", id="a", name="a", collections=[["Wasteland"], ["Packs", "Iceland"]]),
            make_record("/b.jpg", id="b", name="b", collections=[["Arctic"]])]
    cache = ThumbnailCache(cache_dir=str(tmp_path / "c"))
    w = GalleryWidget(cache=cache, favourites_store=MemoryStore())
    w.add_source(RecordsSource(lambda: recs, name="x"), background=False)
    try:
        w.show()
        settle(app)
        root = w.tree._rows[("@collections",)][0]
        kids = [root.child(r, 0).text() for r in range(root.rowCount())]
        assert kids == ["Arctic", "Wasteland", "Packs"]
    finally:
        w.close()
        cache.shutdown()
