"""Collapsible panes: tabs stack in a strip per side, the right column shares the height,
sizes come back on expand."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402

from ilp_gallery.core import RecordsSource, make_record  # noqa: E402
from ilp_gallery.ui.collapsible import STRIP  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def spin(app, t=0.2):
    end = time.time() + t
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def make(app, **kw):
    from ilp_gallery.ui.widget import GalleryWidget
    w = GalleryWidget(**kw)
    w.add_source(RecordsSource([make_record(f"/c/{n}.fbx", name=n) for n in ("walk", "run")]),
                 background=False)
    w.resize(1500, 800)
    w.show()
    spin(app)
    return w


def shown(widget):
    return not widget.isHidden()


def test_right_column_stacks_drawer_over_details(app):
    w = make(app, drawer=True)
    col = w.area.column
    assert w.panes["drawer"].parentWidget() is col and w.panes["details"].parentWidget() is col
    assert col.indexOf(w.panes["drawer"]) == 0                       # drawer on top
    assert w.panes["drawer"].x() == w.panes["details"].x()            # same column
    assert not shown(w.area.left_strip) and not shown(w.area.right_strip)
    w.close()


def test_built_collapsed_tabs_stack_in_one_strip(app):
    w = make(app, drawer=True, drawer_title="Clips", collapsed=("drawer", "details"))
    assert w.is_collapsed("drawer") and w.is_collapsed("details") and w.has_drawer()
    strip = w.area.right_strip
    assert shown(strip) and strip.width() == STRIP
    d, p = w.panes["drawer"].tab, w.panes["details"].tab
    assert shown(d) and shown(p) and d.y() < p.y()                    # one bar, stacked
    assert not shown(w.area.column)                                   # nothing left to show
    assert w.panes["grid"].width() >= 1500 - w.panes["tree"].width() - STRIP - 10   # tiles get the room
    w.close()


def test_collapse_one_right_pane_gives_the_other_the_height(app):
    w = make(app, drawer=True)
    w.set_collapsed("details", True)
    spin(app)
    assert shown(w.area.column) and w.panes["drawer"].height() > 700
    assert shown(w.area.right_strip) and shown(w.panes["details"].tab)
    w.set_collapsed("details", False)
    spin(app)
    assert not shown(w.area.right_strip)
    assert 200 < w.panes["details"].height() < 500
    w.close()


def test_collapse_and_expand_restores_width(app):
    w = make(app, drawer=True)
    tree, grid = w.panes["tree"], w.panes["grid"]
    before, grid_before = tree.width(), grid.width()
    w.set_collapsed("tree", True)
    spin(app)
    assert not shown(tree) and shown(w.area.left_strip)
    assert grid.width() >= grid_before + before - STRIP - 8          # less the strip and handle
    tree.tab.click()
    spin(app)
    assert shown(tree) and not shown(w.area.left_strip)
    assert abs(tree.width() - before) <= 2 and abs(grid.width() - grid_before) <= 2
    w.close()


def test_column_width_comes_back(app):
    w = make(app, drawer=True)
    col_w = w.area.column.width()
    w.set_collapsed("drawer", True)
    w.set_collapsed("details", True)
    spin(app)
    w.panes["drawer"].tab.click()
    spin(app)
    assert shown(w.area.column) and abs(w.area.column.width() - col_w) <= 2
    assert shown(w.panes["details"].tab)
    w.close()


def test_collapsed_drawer_still_works(app):
    w = make(app, drawer=True, drawer_title="Clips", collapsed=("drawer",))
    assert w.add_to_drawer([w._drawer_index["walk"]]) == 1
    assert w.panes["drawer"].tab.text() == "Clips: 1"
    w.close()


def test_details_collapse_skips_updates_and_refreshes_on_expand(app):
    w = make(app, collapsed=("details",))
    w.select(w.model.ids()[0])
    spin(app)
    assert w.details.item is None
    w.set_collapsed("details", False)
    spin(app)
    assert w.details.item is not None and w.details.item.id == w.model.ids()[0]
    w.close()


def test_removed_panes_have_no_tab(app):
    w = make(app, show_sidebar=False, show_details=False)              # no drawer either
    assert not shown(w.area.column) and not shown(w.area.left_strip)
    w.set_sidebar_visible(True)
    spin(app)
    assert shown(w.panes["tree"])
    with pytest.raises(ValueError):
        w.set_collapsed("player")
    w.close()


def test_header_tab_arrow_and_double_click_collapse(app):
    w = make(app, drawer=True, drawer_title="Clips")
    header = w.panes["tree"].header
    c = header.arrow_rect().center()
    header.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, c, header.mapToGlobal(c),
                                       Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert w.is_collapsed("tree")
    w.set_collapsed("tree", False)
    header.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, QPointF(12, 12), QPointF(12, 12),
                                       Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert not w.is_collapsed("tree")                  # a click on the title alone does nothing
    header.mouseDoubleClickEvent(QMouseEvent(QEvent.MouseButtonDblClick, QPointF(12, 12), QPointF(12, 12),
                                             Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert w.is_collapsed("tree")
    w.close()


def test_tile_size_option_and_wheel_control(app):
    from ilp_gallery.ui.sgi.thumbwheel import ThumbWheel
    w = make(app, tile_size=90, size_control="wheel")
    assert isinstance(w.size_slider, ThumbWheel) and w.size_slider.value() == 90
    assert w.view.tile_width == 90 if hasattr(w.view, "tile_width") else True
    w.size_slider.setValue(200)
    w.close()
    w = make(app, tile_size=5000)
    assert w.size_slider.value() == w.TILE_MAX
    w.close()
    with pytest.raises(ValueError):
        make(app, size_control="knob")


def test_toolbar_widget_sits_beside_the_size_control(app):
    w = make(app)
    label = w.add_toolbar_widget(QtWidgets.QLabel("/obj/geo1/clips1"))
    spin(app)
    assert label.isVisible() and label.x() > w.size_slider.x() + w.size_slider.width()
    w.close()


def test_splitter_handles_are_pane_handles(app):
    from ilp_gallery.ui.collapsible import PaneHandle
    w = make(app, drawer=True)
    for s in (w.area.splitter, w.area.column):
        assert all(isinstance(s.handle(i), PaneHandle) for i in range(1, s.count()))
    w.close()


def test_toolbar_label_right_aligned_and_framed(app):
    w = make(app)
    b = w.add_toolbar_button("Rescan", lambda: None)
    label = w.add_toolbar_label("/obj/geo1/clips1")
    spin(app)
    assert label.x() > w.width() * 0.6 and label.x() < b.x()      # right side, before buttons
    assert "border" in label.styleSheet()
    w.set_theme("sgi")
    assert w.theme.c("border").name() in label.styleSheet()
    with pytest.raises(ValueError):
        w.add_toolbar_widget(QtWidgets.QLabel(), side="middle")
    w.close()


def test_strip_tabs_hang_below_the_tab_baseline(app):
    from ilp_gallery.ui.collapsible import PaneHeader
    w = make(app, drawer=True, collapsed=("tree", "details"))
    spin(app)
    for tab in (w.panes["tree"].tab, w.panes["details"].tab):
        assert tab.y() >= PaneHeader.HEIGHT
    w.close()


def test_tabs_keep_their_width_under_a_host_button_stylesheet(app):
    """Houdini's stylesheet gives buttons a min-width and padding; the tabs must not grow."""
    from ilp_gallery.ui.widget import GalleryWidget
    host = QtWidgets.QWidget()
    host.setStyleSheet("QAbstractButton { min-width: 36px; padding: 4px 10px; }")
    lay = QtWidgets.QVBoxLayout(host)
    w = GalleryWidget(parent=host, drawer=True, collapsed=("tree", "drawer"))
    lay.addWidget(w)
    host.resize(1200, 600)
    host.show()
    spin(app)
    for name in ("tree", "drawer"):
        assert w.panes[name].tab.width() == STRIP
    host.close()
