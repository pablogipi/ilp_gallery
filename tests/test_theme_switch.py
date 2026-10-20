"""Italic menus in every theme, italic pane tabs with SGI, and switching themes live."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtGui import QColor, QPalette  # noqa: E402
from PySide6.QtWidgets import QComboBox, QMenu  # noqa: E402

from ilp_gallery.core import RecordsSource, make_record  # noqa: E402
from ilp_gallery.ui.sgi.fusion import _MARKED  # noqa: E402  (fusion-dark below)

THEMES = ["fusion", "fusion-dark", "fusion-slate", "sgi", "sgi-dark", "sgi-slate"]


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def gallery(app, tmp_path):
    from ilp_gallery.media.thumbs import ThumbnailCache
    from ilp_gallery.ui.widget import GalleryWidget
    cache = ThumbnailCache(cache_dir=str(tmp_path / "cache"))
    w = GalleryWidget(cache=cache, theme="fusion-dark", drawer=True)
    recs = [make_record(f"/x/{n}.jpg", id=n, name=n) for n in ("a", "b")]
    w.add_source(RecordsSource(lambda: recs, name="x"), background=False)
    w.show()
    app.processEvents()
    yield w
    w.close()
    cache.shutdown()


@pytest.mark.parametrize("name", THEMES)
def test_menus_are_italic_in_every_theme(gallery, name):
    gallery.set_theme(name)
    menu = QMenu(gallery)
    gallery.theme.polish_menu(menu)
    sub = menu.addMenu("Filter by tag")                  # added after polishing, like the grid's
    menu.aboutToShow.emit()
    assert menu.font().italic() and sub.font().italic()
    gallery.add_to_drawer(list(gallery.library))
    assert gallery.drawer.context_menu(gallery.drawer.keys()).font().italic()


@pytest.mark.parametrize("name,italic", [("fusion", False), ("fusion-dark", False),
                                         ("sgi", True), ("sgi-dark", True), ("sgi-slate", True)])
def test_pane_tabs_italic_only_with_sgi(gallery, name, italic):
    gallery.set_theme(name)
    pane = gallery.panes["tree"]
    assert pane.colors.italic is italic
    gallery.area.set_collapsed("tree", True)
    w = pane.tab.width() if pane.edge == "bottom" else pane.tab.height()
    assert w == (pane.tab.sizeHint().width() if pane.edge == "bottom" else pane.tab.sizeHint().height())


@pytest.mark.parametrize("target", THEMES)
def test_switching_live_takes_the_new_background(gallery, target):
    for name in THEMES:                                  # through every other theme first
        gallery.set_theme(name)
    gallery.set_theme(target)
    assert gallery.palette().color(QPalette.Window) == QColor(gallery.theme.bg)


def test_toolbar_widgets_added_later_get_the_theme(gallery):
    gallery.set_theme("fusion-dark")
    combo = gallery.add_toolbar_widget(QComboBox(), side="right")
    btn = gallery.add_toolbar_button("", lambda: None, icon="reload")
    for w in (combo, btn):
        assert w.property(_MARKED)                       # the theme installed its style on it
        assert w.palette().color(QPalette.Window) == QColor(gallery.theme.bg)
