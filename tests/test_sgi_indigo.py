"""Indigo Magic details: the default-button ring, item-view check marks, list panes
darker than the tiles' grid, bold italic tabs (SGI only; Fusion keeps its look)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtGui import QColor, QFont  # noqa: E402

from ilp_gallery.ui import theme  # noqa: E402
from ilp_gallery.ui.sgi import FUSION_DARK, SGI, SGI_DARK, SGI_SLATE, assets  # noqa: E402

SGIS = [SGI, SGI_DARK, SGI_SLATE]


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.mark.parametrize("t", SGIS, ids=lambda t: t.name)
def test_primary_button_is_a_ring_not_an_accent_fill(app, t):
    css = t.stylesheet()
    rule = css.split("QPushButton:default, QPushButton#primary {")[1].split("}")[0]
    assert "img_default" not in rule and "default" in rule and "border-width: 8px" in rule
    assert "primary.svg" not in css and t.accent_text not in rule   # no accent face or text
    svg = assets.pieces(t)["default"]
    assert t.bg in svg and t.tile in svg                     # sunken frame, gap, normal face
    assert 'QPushButton#primary[menuButton="true"]' in css   # Export's ▾ stays a plain button


@pytest.mark.parametrize("t", SGIS, ids=lambda t: t.name)
def test_item_view_checks_use_the_sgi_marks(app, t):
    css = t.stylesheet()
    assert "QAbstractItemView::indicator {" in css and "QAbstractItemView::indicator:checked" in css
    sel = css.split("QListWidget::item:selected {")[1].split("}")[0]
    assert t.accent not in sel                                # no red flash on press


@pytest.mark.parametrize("t", SGIS, ids=lambda t: t.name)
def test_grid_lighter_than_list_panes(app, t):
    assert QColor(t.grid_bg).lightness() > QColor(t.list_bg).lightness()
    css = t.stylesheet()
    assert f"QListView {{ background: {t.grid_bg};" in css
    assert t.list_color("text") == QColor(t.list_text)


def test_fusion_keeps_its_list_colours(app):
    assert FUSION_DARK.list_color("text") == QColor(FUSION_DARK.text)
    assert FUSION_DARK.list_color("muted") == QColor(FUSION_DARK.muted)
    assert not FUSION_DARK.BOLD_TABS and not FUSION_DARK.ITALIC_TABS


def test_tabs_bold_italic_with_sgi(app, tmp_path):
    from ilp_gallery.media.thumbs import ThumbnailCache
    from ilp_gallery.ui.collapsible import _title_font
    from ilp_gallery.ui.widget import GalleryWidget
    cache = ThumbnailCache(cache_dir=str(tmp_path / "c"))
    g = GalleryWidget(cache=cache, theme="sgi")
    try:
        pane = g.panes["tree"]
        f = _title_font(pane.header, pane.colors)
        assert f.italic() and f.weight() == QFont.DemiBold
        g.set_theme("fusion-dark")
        f = _title_font(pane.header, pane.colors)
        assert not f.italic() and f.weight() != QFont.DemiBold
    finally:
        g.close()
        cache.shutdown()


def test_default_theme_standalone_is_fusion_dark():
    assert theme.resolve() is FUSION_DARK


def test_combo_popups_get_a_styled_delegate_and_hover(app):
    from PySide6.QtWidgets import QComboBox, QStyledItemDelegate, QWidget
    from ilp_gallery.ui.sgi import style
    root = QWidget()
    combo = QComboBox(root)
    combo.addItems(["a", "b"])
    original = combo.itemDelegate()
    style.apply_style(root)
    assert isinstance(combo.itemDelegate(), QStyledItemDelegate) and combo.itemDelegate() is not original
    assert combo.itemDelegate().sizeHint(combo.view().viewOptions() if hasattr(combo.view(), "viewOptions")
                                         else __import__("PySide6.QtWidgets", fromlist=["x"]).QStyleOptionViewItem(),
                                         combo.model().index(0, 0)).height() >= style.COMBO_ROW
    css = SGI.stylesheet()
    assert "QComboBox QAbstractItemView::item:hover" in css
    style.remove_style(root)
    assert combo.itemDelegate() is original                 # the host / Fusion look comes back
    root.deleteLater()


@pytest.mark.parametrize("name,shape", [("sgi", True), ("sgi-dark", True), ("sgi-slate", True),
                                        ("fusion-dark", False), ("fusion", False)])
def test_irix_tab_shape_only_with_sgi(app, tmp_path, name, shape):
    from ilp_gallery.media.thumbs import ThumbnailCache
    from ilp_gallery.ui.widget import GalleryWidget
    cache = ThumbnailCache(cache_dir=str(tmp_path / "c"))
    g = GalleryWidget(cache=cache, theme=name)
    try:
        pane = g.panes["tree"]
        assert (pane.colors.sgi is not None) is shape
        t = pane.header.tab_rect()
        from ilp_gallery.ui.collapsible import SGI_HATCH
        assert (t.left() > SGI_HATCH) is shape                # room for the hatch
        assert t.contains(pane.header.arrow_rect())          # the collapse arrow stays in the tab
    finally:
        g.close()
        cache.shutdown()


def test_sgi_tab_path_is_wide_on_the_pane_side():
    from PySide6.QtCore import QPointF, QRectF
    from ilp_gallery.ui.collapsible import sgi_tab_path
    r = QRectF(0, 0, 100, 20)
    for wide, near_base, near_free, middle in (
            ("bottom", QPointF(4, 18), QPointF(4, 2), QPointF(50, 2)),
            ("top", QPointF(4, 2), QPointF(4, 18), QPointF(50, 18))):
        path = sgi_tab_path(r, wide)
        assert path.contains(near_base) and not path.contains(near_free) and path.contains(middle), wide
    side = QRectF(0, 0, 20, 100)
    path = sgi_tab_path(side, "right")                      # a collapsed tab on the left strip
    assert path.contains(QPointF(18, 4)) and not path.contains(QPointF(2, 4))
    assert not path.contains(QPointF(0.6, 1.5))              # rounded free-end corner
