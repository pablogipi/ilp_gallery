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


def test_tab_colour_is_lifted_off_a_matching_strip():
    """In Houdini the theme's tile grey can equal the strip's: the tab must still read."""
    from PySide6.QtGui import QColor
    from ilp_gallery.ui.collapsible import _Colors, _distance
    c = _Colors()
    c.set(QColor("#3a3a3a"), QColor("#3a3a3a"), QColor("#515151"), QColor("#cccccc"),
          QColor("#999999"), QColor("#3a3a3a"))
    assert _distance(c.tab, c.strip) >= 18 and _distance(c.hover, c.tab) >= 12
    c.set(QColor("#2b303b"), QColor("#363c4a"), QColor("#363b47"), QColor("#d9dce3"),
          QColor("#8a909c"), QColor("#1e2129"))
    assert c.tab == QColor("#2b303b")                     # enough contrast: left alone


def test_second_toolbar_row(app):
    w = make(app)
    assert w.row2.isHidden()
    a = w.add_toolbar_widget(QtWidgets.QComboBox(), row=1)
    b = w.add_toolbar_widget(QtWidgets.QCheckBox("Retarget Clips"), row=1)
    spin(app)
    assert not w.row2.isHidden() and b.x() > a.x()
    assert abs(a.geometry().center().y() - b.geometry().center().y()) <= 2   # same line
    assert w.row2.y() > w.search.y() + w.search.height() - 2          # under the search bar
    with pytest.raises(ValueError):
        w.add_toolbar_widget(QtWidgets.QLabel(), row=2)
    w.close()


def test_size_label_and_media_badges_can_be_hidden(app):
    w = make(app, size_label=False, media_badges=False)
    assert w.size_label.isHidden() and w.delegate.media_badges is False
    w.close()
    w = make(app)
    assert not w.size_label.isHidden() and w.delegate.media_badges is True
    w.close()


def test_grid_tab_follows_the_source_label(app):
    from ilp_gallery.core import RecordsSource
    w = make(app)                                       # RecordsSource: its default label
    src = w.library.sources[0]
    assert w.panes["grid"].title() == (src.label or src.name)
    w.add_source(RecordsSource([], name="more", label="More"), background=False)
    assert w.panes["grid"].title() == "Assets"         # several sources
    w.set_grid_title("Motion Clips")
    assert w.panes["grid"].title() == "Motion Clips"
    w.close()


def test_toolbar_button_icons(app):
    w = make(app)
    b = w.add_toolbar_button("", lambda: None, "Reload", icon="reload")
    assert not b.icon().isNull() and b.text() == ""
    with pytest.raises(ValueError):
        w.add_toolbar_button("", lambda: None, icon="spinner")
    w.close()


def test_log_pane(app):
    w = make(app, drawer=True, log=True, collapsed=("log",))
    assert w.has_log() and w.panes["log"].parentWidget() is w.area.vsplit
    strip, tab = w.area.bottom_strip, w.panes["log"].tab
    assert not strip.isHidden() and tab.height() == STRIP and tab.width() > tab.height()
    assert strip.y() > w.panes["grid"].y()                          # folded along the bottom
    w.logMessage.emit("Loading clips…")
    w.logMessage.emit("ERROR loading clips: boom\nTraceback line")
    assert w.panes["log"].tab.text() == "Log (2)"                     # unseen while folded
    text = w.log_pane.text()
    assert "Loading clips…" in text and "          Traceback line" in text
    w.set_collapsed("log", False)
    spin(app)
    assert w.panes["log"].title() == "Log" and strip.isHidden()
    assert 100 < w.panes["log"].height() < 220                       # full width, under the rest
    assert w.panes["log"].width() >= w.width() - 2
    w.taskStarted.emit("Exporting…", True)
    assert w.log_pane.stop_btn.isEnabled()
    w.taskFinished.emit(True)
    assert not w.log_pane.stop_btn.isEnabled()
    w.log_pane.clear_btn.click()
    assert w.log_pane.text() == ""
    w.close()


def test_no_log_pane_by_default(app):
    w = make(app)
    assert not w.has_log() and w.panes["log"].isHidden()
    w.logMessage.emit("x")
    assert w.log_pane.text() == ""
    w.close()
