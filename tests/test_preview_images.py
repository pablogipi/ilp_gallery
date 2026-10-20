"""Hover-only tile labels, and the preview pane's image groups (renders / maps)."""

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QModelIndex  # noqa: E402

from ilp_gallery.core import RecordsSource, make_record  # noqa: E402
from ilp_gallery.core.item import GalleryItem  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "data", "megascans_surface_rmlkx2p0.json")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_hover_labels_make_image_only_tiles(app):
    from ilp_gallery.media.thumbs import ThumbnailCache
    from ilp_gallery.ui.delegate import TileDelegate
    d = TileDelegate(None)
    full = d.sizeHint(None, QModelIndex()).height()
    d.labels = "hover"
    assert full - d.sizeHint(None, QModelIndex()).height() == d.LABEL_H


def test_gallery_tile_labels_option(app, tmp_path):
    from ilp_gallery.media.thumbs import ThumbnailCache
    from ilp_gallery.ui.widget import GalleryWidget
    cache = ThumbnailCache(cache_dir=str(tmp_path / "c"))
    g = GalleryWidget(cache=cache, tile_labels="hover")
    recs = [make_record(f"/x/{i}.jpg", id=f"i{i}", name="Road") for i in range(4)]
    g.add_source(RecordsSource(lambda: recs, name="x"), background=False)
    try:
        g.resize(900, 500)
        g.show()
        app.processEvents()
        g.view.setCurrentIndex(g.model.index(0, 0))
        g.delegate.hover_id = "i1"
        g.view.viewport().grab()                        # paints the overlays without errors
        h = g.view.gridSize().height()
        g.set_tile_labels("always")
        assert g.view.gridSize().height() == h + g.delegate.LABEL_H
        with pytest.raises(ValueError):
            g.set_tile_labels("sometimes")
    finally:
        g.close()
        cache.shutdown()


def item_with_images():
    return GalleryItem(path="/a/render.jpg", id="a", name="Road", metadata={
        "image_label": "Render", "image_group": "Renders",
        "images": [{"path": "/a/clay.jpg", "label": "Clay", "group": "Renders"},
                   {"path": "/a/tiled.jpg", "label": "Tiled", "group": "Renders"},
                   {"path": "/a/albedo.jpg", "label": "Albedo", "group": "Maps"},
                   {"path": "/a/normal.jpg", "label": "Normal", "group": "Maps"}]})


def test_preview_steps_through_a_group_and_switches_groups(app):
    from ilp_gallery.media.thumbs import ThumbnailCache
    from ilp_gallery.ui.details import DetailPanel
    import tempfile
    cache = ThumbnailCache(cache_dir=tempfile.mkdtemp())
    panel = DetailPanel(cache)
    try:
        panel.set_item(item_with_images())
        assert not panel.images_bar.isHidden()
        assert panel.image_groups() == ["Renders", "Maps"] and set(panel._group_btns) == {"Renders", "Maps"}
        assert panel.image_label.text() == "Render  1 / 3"
        panel.step_image(1)
        assert panel.image_label.text() == "Clay  2 / 3" and panel.player.item.path == "/a/clay.jpg"
        panel.step_image(2)                              # wraps round to the item's own render
        assert panel.image_label.text() == "Render  1 / 3" and panel.player.item.path == "/a/render.jpg"
        panel._group_btns["Maps"].click()
        assert panel.image_label.text() == "Albedo  1 / 2" and panel._group_btns["Maps"].isChecked()
        panel.step_image(-1)
        assert panel.player.item.path == "/a/normal.jpg"
        panel.set_item(GalleryItem(path="/b.jpg", id="b", name="b"))
        assert panel.images_bar.isHidden() and not panel._group_btns   # one image: no bar
        panel.set_item(GalleryItem(path="/c.jpg", id="c", name="c", metadata={"images": ["/c2.jpg"]}))
        assert not panel.images_bar.isHidden() and not panel._group_btns   # one group: arrows only
        assert panel.image_label.text() == "Image  1 / 2"
    finally:
        panel.player.stop()
        cache.shutdown()


def test_megascans_records_list_renders_and_maps(tmp_path):
    from ilp_gallery.sources import megascans
    d = tmp_path / "surface" / "Chickpeas_rmlkx2p0"
    d.mkdir(parents=True)
    shutil.copy(FIXTURE, d / "rmlkx2p0.json")
    for f in ["rmlkx2p_Grid_360_thumb.jpg", "rmlkx2p_Popup_1920_preview.jpg",
              "rmlkx2p_PopupClay_1920_preview.jpg", "rmlkx2p_PopupTiled_1440_preview.jpg",
              "rmlkx2p_4K_Albedo.exr", "rmlkx2p_1K_Albedo.jpg", "rmlkx2p_4K_Albedo.jpg", "rmlkx2p_1K_Normal.jpg"]:
        (d / f).write_bytes(b"x")
    m = megascans.asset_record(str(d), "surface")["metadata"]
    got = [(im["group"], im["label"], os.path.basename(im["path"])) for im in m["images"]]
    assert got[:2] == [("Renders", "Clay", "rmlkx2p_PopupClay_1920_preview.jpg"),
                       ("Renders", "Tiled", "rmlkx2p_PopupTiled_1440_preview.jpg")]
    assert ("Maps", "Albedo", "rmlkx2p_1K_Albedo.jpg") in got      # the 4K is over the limit, EXR skipped
    assert ("Maps", "Normal", "rmlkx2p_1K_Normal.jpg") in got
    assert (m["image_label"], m["image_group"]) == ("Render", "Renders")


def test_no_paths_in_the_ui(app, tmp_path):
    from ilp_gallery.media.thumbs import ThumbnailCache
    from ilp_gallery.ui.widget import GalleryWidget
    cache = ThumbnailCache(cache_dir=str(tmp_path / "c"))
    g = GalleryWidget(cache=cache, show_paths=False)
    recs = [make_record("/secret/dir/a.jpg", id="a", name="A")]
    g.add_source(RecordsSource(lambda: recs, name="x"), background=False)
    try:
        g.set_export_enabled(True)
        g.show()
        g.details.set_item(next(iter(g.library)))
        app.processEvents()
        d = g.details
        assert "Location" not in d.info.text() and "/secret" not in d.info.text()
        assert not d.folder_btn.isVisibleTo(d) and not d.copy_btn.isVisibleTo(d)   # Copy path is a path
        g.command_template = "echo {id}"                 # a command, not a path: offered again
        assert d.copy_btn.isVisibleTo(d) and d.copy_btn.text() == "Copy command"
    finally:
        g.close()
        cache.shutdown()


def test_outdated_library_is_reported_once(app, tmp_path):
    from ilp_gallery.media.thumbs import ThumbnailCache
    from ilp_gallery.ui.widget import GalleryWidget

    class Old(RecordsSource):
        rebuild_hint = "rebuild-it --now"

        def outdated(self, items):
            return len(items)

    cache = ThumbnailCache(cache_dir=str(tmp_path / "c"))
    g = GalleryWidget(cache=cache)
    seen = []
    g.logMessage.connect(seen.append)
    recs = [make_record(f"/x/{i}.jpg", id=f"i{i}", name="n") for i in range(3)]
    src = Old(lambda: recs, name="old")
    try:
        g.add_source(src, background=False)
        g.reload_source(src, background=False)
        msgs = [m for m in seen if "older version" in m]
        assert len(msgs) == 1 and "3 of 3" in msgs[0] and "rebuild-it --now" in msgs[0]
    finally:
        g.close()
        cache.shutdown()
