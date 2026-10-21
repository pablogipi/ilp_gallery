"""Offscreen UI tests. Need PySide6 + ffmpeg; skipped otherwise.

    QT_QPA_PLATFORM=offscreen pytest tests/test_ui.py
"""

import os
import subprocess
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QMouseEvent  # noqa: E402

from ilp_gallery.media import ffmpeg  # noqa: E402

pytestmark = pytest.mark.skipif(not ffmpeg.have_ffmpeg(), reason="ffmpeg not installed")
HERE = os.path.dirname(__file__)


@pytest.fixture(scope="session")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="session")
def demo(tmp_path_factory):
    out = tmp_path_factory.mktemp("demo")
    subprocess.run([sys.executable, os.path.join(HERE, "..", "examples", "make_demo_library.py"), str(out)],
                   check=True, stdout=subprocess.DEVNULL)
    return out


@pytest.fixture(scope="session")
def megascans_lib(tmp_path_factory, app):
    """Two fake Megascans assets with real JPEG previews, run through the importer."""
    import json
    import shutil
    from PySide6.QtGui import QColor, QImage
    from ilp_gallery.sources.megascans import build_library_json

    root = tmp_path_factory.mktemp("ms")
    src = json.load(open(os.path.join(HERE, "data", "megascans_surface_rmlkx2p0.json")))
    for cat, aid, name, ac in (("surface", "rmlkx2p0", "Chickpeas", {"surface": {"other": {"edible": {}}}}),
                               ("surface", "xbknedb", "Rock Cliff", {"surface": {"rock": {"cliff": {}, "rough": {}}}})):
        d = root / cat / f"{name.replace(' ', '_')}_{aid}"
        d.mkdir(parents=True)
        data = dict(src, id=aid, name=name, assetCategories=ac)
        (d / f"{aid}.json").write_text(json.dumps(data))
        for f, size in (("rmlkx2p_Grid_360_thumb.jpg", 360), ("rmlkx2p_Popup_1920_preview.jpg", 960)):
            img = QImage(size, size, QImage.Format_RGB32)
            img.fill(QColor("#6b4e29"))
            img.save(str(d / f))
    (root / "3d").mkdir()
    out = root / "megascans.json"
    build_library_json(str(out), root=str(root), threads=2)
    return out


def wait(app, cond, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def cache(tmp_path):
    from ilp_gallery.media.thumbs import ThumbnailCache
    c = ThumbnailCache(cache_dir=str(tmp_path / "cache"))
    yield c
    c.shutdown()


@pytest.fixture
def gallery(app, demo, cache):
    from ilp_gallery.ui.widget import GalleryWidget
    w = GalleryWidget(cache=cache)
    w.load(str(demo / "library.json"))
    w.resize(1400, 800)
    w.show()
    wait(app, lambda: False, 0.2)
    yield w
    w.details.player.stop()
    w.close()


@pytest.fixture
def browser(app, megascans_lib, cache, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))  # isolated QSettings
    from ilp_gallery.ui.browser import GalleryBrowser
    b = GalleryBrowser(cache=cache)
    b.gallery.load(str(megascans_lib))
    b.resize(1500, 900)
    b.show()
    wait(app, lambda: False, 0.2)
    yield b
    b.close()


# -- generic media ------------------------------------------------------------
def test_loads_and_thumbnails(app, gallery):
    assert gallery.model.rowCount() == 9
    items = gallery.visible_items()
    assert wait(app, lambda: all(gallery.cache.poster(i) is not None or gallery.cache.failed(i.id) for i in items))
    assert not [i.id for i in items if gallery.cache.failed(i.id)]
    assert len(gallery.cache.frames(gallery.library.get("walk_cycle_male"))) == 12
    assert len(gallery.cache.frames(gallery.library.get("fire_loop"))) >= 6


def test_search_and_tree(app, gallery):
    gallery.set_query("cat:crowd -tag:loop")
    assert gallery.model.ids() == ["run_cycle_female"]
    gallery.set_query("")
    gallery.select_category(["fx"])
    assert set(gallery.model.ids()) == {"fractal_zoom", "fire_loop", "magic_sparkle"}
    gallery.set_query("type:gif")                      # search AND tree filter
    assert set(gallery.model.ids()) == {"fire_loop", "magic_sparkle"}
    gallery.select_category([])                        # All
    assert set(gallery.model.ids()) == {"fire_loop", "magic_sparkle"}


def test_hover_scrub(app, gallery):
    wait(app, lambda: gallery.cache.frames(gallery.library.get("walk_cycle_male")))
    idx = gallery.model.index_of("walk_cycle_male")
    img = gallery.delegate.image_rect(gallery.view.visualRect(idx))
    pos = QPointF(img.left() + img.width() * 0.8, img.top() + 20)
    app.sendEvent(gallery.view.viewport(),
                  QMouseEvent(QMouseEvent.MouseMove, pos, pos, Qt.NoButton, Qt.NoButton, Qt.NoModifier))
    assert gallery.delegate.hover_id == "walk_cycle_male"
    assert 0.7 < gallery.delegate.hover_fraction < 0.9


@pytest.mark.parametrize("item_id", ["walk_cycle_male", "fire_loop", "colour_bars"])
def test_player(app, gallery, item_id):
    gallery._on_activated(gallery.model.index_of(item_id))
    p = gallery.details.player
    if item_id == "colour_bars":
        assert wait(app, lambda: p.canvas._pix is not None and not p.canvas._pix.isNull(), 5)
        return
    assert wait(app, lambda: p._frame >= 5, 10), "playback did not advance"
    p.pause()
    p.seek(10)
    assert wait(app, lambda: p._frame == 10, 5)
    p.step(1)
    assert wait(app, lambda: p._frame == 11, 5)


def test_drag_mime_and_custom_action(app, gallery):
    md = gallery.model.mimeData([gallery.model.index_of("fire_loop")])
    assert md.urls()[0].toLocalFile().endswith("fire_loop.gif")
    got = []
    gallery.add_action("Collect", got.extend, default=True)
    gallery._on_activated(gallery.model.index_of("fire_loop"))
    assert got and got[0].id == "fire_loop"


# -- megascans browser --------------------------------------------------------
def test_browser_tree_and_details(app, browser):
    g = browser.gallery
    assert len(g.library) == 2
    assert g.delegate.aspect == 1.0                         # square tiles for Megascans
    g.select_category(["surface", "rock", "cliff"])
    assert g.model.ids() == ["xbknedb"]
    g.select("xbknedb")
    d = g.details
    assert d.item.id == "xbknedb"
    html = d.info.text()
    assert "Rock" in html and "Cliff" in html and "Rough" in html
    assert "Tileable" in html and "Yes" in html and "Texel Density" in html
    assert "Textures" in html and "4K" in html
    assert d.export_btn.isVisible()
    assert wait(app, lambda: d.player.canvas._pix is not None and d.player.canvas._pix.width() == 960, 5)

    d._on_link("cat:surface/rock")                          # breadcrumb click
    assert g.tree.current_path() == ("surface", "rock")
    d._on_link("tag:brown")                                 # tag click
    assert "tag:brown" in g.search.text()
    assert "status" not in html


def test_browser_export_and_copy(app, browser):
    g = browser.gallery
    g.command_template = "echo exporting {id} {asset_dir}"
    g.select("rmlkx2p0")
    text = g.copy_command()
    assert text.startswith("echo exporting rmlkx2p0 /")
    assert QGuiApplication.clipboard().text() == text
    browser.export_selected()
    assert wait(app, lambda: "Run export command: 1 ok" in browser.log_panel.text.toPlainText(), 10)
    log = browser.log_panel.text.toPlainText()
    assert "exporting rmlkx2p0" in log
    assert browser.log_panel.status.text() == "Idle"


def test_browser_export_stop(app, browser):
    g = browser.gallery
    g.command_template = "sleep 5"
    browser.export([g.library.get("rmlkx2p0"), g.library.get("xbknedb")])
    assert wait(app, lambda: browser.log_panel.stop_btn.isEnabled(), 5)
    t = time.time()
    browser.stop_task()
    assert wait(app, lambda: "Export stopped" in browser.log_panel.text.toPlainText(), 5)
    assert time.time() - t < 4


def test_browser_rescan(app, browser):
    browser.rescan()
    assert wait(app, lambda: "Loaded 2 assets" in browser.log_panel.text.toPlainText(), 15)


def test_tree_hierarchy_and_click_to_open(app, browser):
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    t = browser.gallery.tree
    tops = [t._model.item(r, 0).text() for r in range(t._model.rowCount())]
    assert tops == ["All assets", "Favourites", "Surfaces"]      # nested, not flat
    surfaces = t._model.indexFromItem(t._rows[("surface",)][0])
    assert not t.isExpanded(surfaces)                             # starts collapsed
    rect = t.visualRect(surfaces)
    QTest.mouseClick(t.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    assert t.current_path() == ("surface",) and t.isExpanded(surfaces)
    assert browser.gallery.model.rowCount() == 2
    child_labels = {t._model.itemFromIndex(surfaces).child(r, 0).text()
                    for r in range(t._model.itemFromIndex(surfaces).rowCount())}
    assert child_labels == {"Edible", "Rock"}
    QTest.mouseClick(t.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    assert not t.isExpanded(surfaces)                             # second click closes


def test_outdated_library_is_upgraded(app, megascans_lib, cache, tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    old = tmp_path / "old_megascans.json"
    data = json.load(open(megascans_lib))
    for r in data["items"]:                                      # what the previous importer wrote
        for k in ("importer_version", "category_paths", "type_label", "display"):
            r["metadata"].pop(k, None)
    old.write_text(json.dumps(data, indent=1))
    from ilp_gallery.ui.browser import GalleryBrowser
    b = GalleryBrowser(cache=cache)
    b.load(str(old))
    wait(app, lambda: b.gallery.tree._model.rowCount() > 2, 2)  # All assets, Favourites, then categories
    tops = [b.gallery.tree._model.item(r, 0).text() for r in range(b.gallery.tree._model.rowCount())]
    assert tops == ["All assets", "Favourites", "Surfaces"]      # no flat category flood meanwhile
    assert wait(app, lambda: b.gallery.library.get("xbknedb").metadata.get("category_paths"), 15)
    assert "older version of the importer" in b.log_panel.text.toPlainText()
    b.close()


def test_megascans_ingest_callback_and_textures(app, browser):
    import glob
    from ilp_gallery.sources.megascans import texture_files
    g = browser.gallery
    received = []
    g.register_exporter(lambda json_file: received.append(json_file), label="Ingest", arg="json",
                        default=True, sources=["megascans"])
    g.select("xbknedb")
    assert g.details.export_btn.text() == "Ingest"
    assert g.details.more_btn.isVisible() is False            # only one exporter applies
    browser.export_selected()
    assert wait(app, lambda: received, 5)
    assert received[0].endswith("xbknedb.json") and os.path.exists(received[0])

    it = g.library.get("xbknedb")
    d = it.metadata["asset_dir"]
    for f in ("rmlkx2p_2K_Albedo.jpg", "rmlkx2p_2K_Albedo.exr", "rmlkx2p_1K_Normal.jpg"):
        open(os.path.join(d, f), "wb").close()
    tex = texture_files(it)
    assert tex["albedo"].endswith("rmlkx2p_2K_Albedo.exr")    # highest res on disk, EXR preferred
    assert tex["normal"].endswith("rmlkx2p_1K_Normal.jpg")
    assert texture_files(it, ["albedo"], prefer=["jpg"])["albedo"].endswith("2K_Albedo.jpg")
    for f in glob.glob(os.path.join(d, "rmlkx2p_*K_*.*")):
        os.remove(f)
