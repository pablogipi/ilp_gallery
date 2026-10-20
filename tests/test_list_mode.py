"""List view mode: rows like the drawer's, per-mode sizes, the toggle and reset buttons."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QListView  # noqa: E402

from ilp_gallery.core import RecordsSource, make_record  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def make(app, tmp_path, **kw):
    from ilp_gallery.media.thumbs import ThumbnailCache
    from ilp_gallery.ui.widget import GalleryWidget
    cache = ThumbnailCache(cache_dir=str(tmp_path / "cache"))
    w = GalleryWidget(cache=cache, **kw)
    recs = [make_record(f"/x/{i}.jpg", id=f"i{i}", name=f"Item {i}") for i in range(20)]
    w.add_source(RecordsSource(lambda: recs, name="x"), background=False)
    w.resize(1000, 600)
    w.show()
    app.processEvents()
    w._cache_for_test = cache
    return w


def settle(app, seconds=0.3):
    import time
    end = time.time() + seconds
    while time.time() < end:                 # the view lays rows out in batches
        app.processEvents()


def close(w):
    w.close()
    w._cache_for_test.shutdown()


def test_switch_modes(app, tmp_path):
    w = make(app, tmp_path)
    try:
        assert w.view_mode() == "grid" and w.view.viewMode() == QListView.IconMode
        w.set_tile_width(w.TILE_MIN)                     # small tiles: several per row
        settle(app)
        before = [w.view.visualRect(w.model.index(i, 0)) for i in (0, 1)]
        assert before[1].left() > before[0].left()
        seen = []
        w.viewModeChanged.connect(seen.append)
        w.set_view_mode("list")
        assert w.view.viewMode() == QListView.ListMode and w.delegate.mode == "list"
        assert w.view.property("listMode") is True and seen == ["list"]
        settle(app)
        r0, r1 = (w.view.visualRect(w.model.index(i, 0)) for i in (0, 1))
        assert r1.top() == r0.bottom() + 1 and r0.width() >= w.view.viewport().width() - 2   # full-width rows
        w.toggle_view_mode()
        assert w.view_mode() == "grid" and w.view.viewMode() == QListView.IconMode
        settle(app)
        after = [w.view.visualRect(w.model.index(i, 0)) for i in (0, 1)]
        assert after == before                           # tiles side by side again, not one column
        with pytest.raises(ValueError):
            w.set_view_mode("table")
    finally:
        close(w)


def test_each_mode_keeps_its_size_and_rows_stay_small(app, tmp_path):
    w = make(app, tmp_path, tile_size=300)
    try:
        w.set_view_mode("list")
        assert w.size_slider.value() == w.LIST_SIZE
        w.size_slider.setValue(w.TILE_MAX)                 # largest row
        biggest = w.delegate.row_height
        assert biggest <= w.view.ROW_MAX + 6 < w.delegate.image_height()
        w.size_slider.setValue(w.TILE_MIN)
        assert w.delegate.row_height == w.view.ROW_MIN + 6
        w.size_slider.setValue(200)
        w.set_view_mode("grid")
        assert w.size_slider.value() == 300 and w.delegate.tile_width == 300
        w.set_view_mode("list")
        assert w.size_slider.value() == 200
    finally:
        close(w)


def test_reset_size_only_resets_the_current_mode(app, tmp_path):
    w = make(app, tmp_path, tile_size=250, size_control="wheel")
    try:
        assert w.size_reset_btn.isVisibleTo(w)              # shown with the wheel by default
        w.size_slider.setValue(400)
        w.set_view_mode("list")
        w.size_slider.setValue(300)
        w.size_reset_btn.click()
        assert w.size_slider.value() == w.LIST_SIZE
        w.set_view_mode("grid")
        assert w.size_slider.value() == 400
        w.reset_size()
        assert w.size_slider.value() == 250
    finally:
        close(w)


def test_buttons_follow_options_and_mode(app, tmp_path):
    w = make(app, tmp_path, view_toggle=False)
    try:
        assert not w.view_toggle_btn.isVisibleTo(w) and not w.size_reset_btn.isVisibleTo(w)
    finally:
        close(w)
    w = make(app, tmp_path, view_mode="list", size_reset=True)
    try:
        assert w.view_mode() == "list" and w.size_reset_btn.isVisibleTo(w)
        assert w.view_toggle_btn.toolTip() == "Show grid"
        w.toggle_view_mode()
        assert w.view_toggle_btn.toolTip() == "Show list"
    finally:
        close(w)


def test_ui_state_round_trip(app, tmp_path):
    w = make(app, tmp_path)
    try:
        w.size_slider.setValue(260)
        w.set_view_mode("list")
        w.size_slider.setValue(330)
        state = w.ui_state()
        assert (state["view"], state["tile_size"], state["list_size"]) == ("list", 260, 330)
    finally:
        close(w)
    w2 = make(app, tmp_path, ui_state=state)
    try:
        assert w2.view_mode() == "list" and w2.size_slider.value() == 330
        w2.set_view_mode("grid")
        assert w2.size_slider.value() == 260
    finally:
        close(w2)


def test_list_rows_scrub_across_the_row(app, tmp_path):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    w = make(app, tmp_path, view_mode="list")
    try:
        settle(app)
        r = w.view.visualRect(w.model.index(2, 0))
        pos = QPoint(r.left() + int(r.width() * 0.75), r.center().y())
        ev = QMouseEvent(QMouseEvent.MouseMove, QPointF(pos), QPointF(w.view.viewport().mapToGlobal(pos)),
                         Qt.NoButton, Qt.NoButton, Qt.NoModifier)
        w.view.mouseMoveEvent(ev)
        assert w.delegate.hover_id == "i2"
        assert 0.7 < w.delegate.hover_fraction < 0.8      # the whole row, not just the thumbnail
        img = w.delegate.image_rect(r)
        assert img.height() == r.height() - 6 and img.left() < r.left() + 10
    finally:
        close(w)


@pytest.mark.parametrize("name", ["fusion-dark", "sgi", "sgi-dark"])
def test_list_rows_take_the_list_pane_colours(app, tmp_path, name):
    w = make(app, tmp_path, theme=name)
    try:
        sel_bg, sel_text = w.theme.list_selection()
        assert w.delegate._list_sel_bg == sel_bg and w.delegate._list_text == w.theme.list_color("text")
        assert 'QListView[listMode="true"]' in w.theme.stylesheet()
    finally:
        close(w)


def test_view_icons(app):
    from ilp_gallery.ui.widget import view_icon
    for kind in ("grid", "list", "reset"):
        assert not view_icon(kind, "#ffffff").isNull()
    with pytest.raises(ValueError):
        view_icon("pie", "#fff")


@pytest.mark.parametrize("name", ["fusion-dark", "sgi", "sgi-dark", "sgi-slate"])
def test_size_reset_button_is_square(app, tmp_path, name):
    w = make(app, tmp_path, theme=name, size_control="wheel")
    try:
        settle(app, 0.1)
        b = w.size_reset_btn
        assert b.width() == b.height() == w.size_slider.height()   # square, as tall as the wheel (SGI made it 22x28)
    finally:
        close(w)


def test_category_label_and_megascans_extras():
    from ilp_gallery.core.item import GalleryItem
    from ilp_gallery.sources.megascans import list_extras as default_list_extras
    from ilp_gallery.ui.delegate import category_label
    it = GalleryItem(path="/m/a.jpg", id="tdlmdbqr", name="Road",
                     metadata={"source": "megascans", "category_paths": [["surface", "asphalt", "rough"]],
                               "physical_size": "2x2"})
    assert category_label(it) == "Asphalt / Rough"
    assert default_list_extras(it, False) == ["2 × 2 m"]
    assert default_list_extras(it, True) == ["2 × 2 m", "tdlmdbqr"]       # same-named assets: the ID
    other = GalleryItem(path="/c/walk.fbx", id="walk", name="walk", metadata={"category_paths": [["loco", "walk"]]})
    assert default_list_extras(other, True) == []                          # only Megascans gets them


@pytest.mark.parametrize("name", ["fusion-dark", "sgi", "sgi-dark"])
def test_view_toggle_is_square_too(app, tmp_path, name):
    w = make(app, tmp_path, theme=name, size_control="wheel")
    try:
        settle(app, 0.1)
        b = w.view_toggle_btn
        assert b.width() == b.height() == 22
    finally:
        close(w)


def test_list_extras_option(app, tmp_path):
    seen = []
    w = make(app, tmp_path, view_mode="list", list_extras=lambda item, shared: seen.append(item.id) or ["x"])
    try:
        settle(app)
        w.view.viewport().grab()
        assert seen                                       # called while painting rows
    finally:
        close(w)
    w = make(app, tmp_path, list_extras=False, list_details=False)
    try:
        assert w.delegate.list_extras is None and w.delegate.list_details is False
    finally:
        close(w)


def test_reset_button_touches_the_size_control(app, tmp_path):
    w = make(app, tmp_path, theme="sgi", size_control="wheel")
    try:
        settle(app, 0.1)
        wheel, reset = w.size_slider.geometry(), w.size_reset_btn.geometry()
        assert reset.left() == wheel.right() + 1 and reset.top() == wheel.top()   # one widget
    finally:
        close(w)
