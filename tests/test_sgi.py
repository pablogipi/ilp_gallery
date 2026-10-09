"""SGI themes: presets, generated images, fonts, proxy style, menu marks, thumbwheel."""

import os
import re
import xml.etree.ElementTree as ET

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPalette  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from ilp_gallery.ui import theme  # noqa: E402
from ilp_gallery.ui.sgi import (  # noqa: E402
    PRESETS, SGI, SGI_DARK, SGI_SLATE, SgiTheme, ThumbWheel, assets, register_fonts,
)
from ilp_gallery.ui.sgi.style import _MARKED, SgiProxyStyle, mnemonic_index  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def sgi_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("ILP_GALLERY_SGI_CACHE", str(tmp_path / "sgi"))
    monkeypatch.delenv("ILP_GALLERY_THEME", raising=False)


def marked(root):
    return [w for w in [root] + root.findChildren(QtWidgets.QWidget) if w.property(_MARKED)]


# -- presets and resolution ---------------------------------------------------------

def test_presets_resolve():
    assert theme.resolve("sgi") is SGI
    assert theme.resolve("sgi-light") is SGI
    assert theme.resolve("SGI-Dark") is SGI_DARK
    assert theme.resolve("sgi-slate") is SGI_SLATE
    assert set(PRESETS) >= {"sgi", "sgi-dark", "sgi-slate"}
    with pytest.raises(ValueError):
        theme.resolve("sgi-purple")


def test_env_selects_sgi(monkeypatch):
    monkeypatch.setenv("ILP_GALLERY_THEME", "sgi-dark")
    assert theme.resolve() is SGI_DARK


def test_flame_palette_is_neutral():
    # the dark preset uses Flame's charcoals: no blue tint in the panels
    c = QColor(SGI_DARK.bg)
    assert c.red() == c.green() == c.blue()
    assert SGI_DARK.current == "#dfbe68"          # amber current tab


def test_replace_keeps_sgi_type():
    t = SGI.replace(accent="#22aa77")
    assert isinstance(t, SgiTheme) and t.accent == "#22aa77"
    assert "#22aa77" in t.stylesheet()


# -- generated images and stylesheet ---------------------------------------------------

def test_stylesheet_references_existing_svgs(app):
    for t in (SGI, SGI_DARK, SGI_SLATE):
        qss = t.stylesheet()
        assert not re.search(r"\{[a-z_]+\}", qss)        # every placeholder was filled in
        urls = re.findall(r"url\(([^)]+)\)", qss)
        assert len(urls) > 30
        for u in set(urls):
            assert os.path.isfile(u), u
            ET.parse(u)                                # every piece is well-formed SVG


def test_pieces_cover_every_url():
    names = set(assets.pieces(SGI))
    used = set(re.findall(r"\{img_(\w+)\}", __import__("ilp_gallery.ui.sgi.theme", fromlist=["_QSS"])._QSS))
    assert used <= names, used - names


def test_assets_written_once_and_keyed_by_colours():
    a = assets.write(SGI)
    mtime = os.path.getmtime(a["raised"])
    assert assets.write(SGI)["raised"] == a["raised"]
    assert os.path.getmtime(a["raised"]) == mtime          # reused, not rewritten
    b = assets.write(SGI.replace(tile="#123456"))
    assert os.path.dirname(b["raised"]) != os.path.dirname(a["raised"])


def test_assets_fall_back_when_cache_is_unwritable(monkeypatch, tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    monkeypatch.setenv("ILP_GALLERY_SGI_CACHE", str(blocker / "sgi"))
    paths = assets.write(SGI)
    assert os.path.isfile(paths["raised"])


def test_bundled_fonts_register(app):
    families = register_fonts()
    assert "IBM Plex Sans" in families and "IBM Plex Mono" in families
    assert '"IBM Plex Sans"' in SGI.stylesheet()


# -- shortcut marks ----------------------------------------------------------------------

@pytest.mark.parametrize("text, idx, plain", [
    ("&File", 0, "File"), ("Fi&lter", 2, "Filter"), ("Save && Close", -1, "Save & Close"),
    ("A && &B", 4, "A & B"), ("plain", -1, "plain"),
])
def test_mnemonic_index(text, idx, plain):
    assert mnemonic_index(text) == (idx, plain)


def _dark_rows_below(img: QImage, x0: int, x1: int, y0: int, y1: int) -> list[int]:
    rows = []
    for y in range(y0, y1):
        if any(QColor(img.pixel(x, y)).lightness() < 80 for x in range(x0, x1)):
            rows.append(y)
    return rows


def test_label_gets_staircase_mark_not_underline(app):
    w = QtWidgets.QWidget(objectName="ILPGalleryRoot")
    w.setStyleSheet(SGI.stylesheet())
    lay = QtWidgets.QVBoxLayout(w)
    cb = QtWidgets.QCheckBox("&Use clipping")
    lay.addWidget(cb)
    SGI.polish(w)
    w.resize(200, 40)
    w.show()
    app.processEvents()
    img = cb.grab().toImage()
    # the mark: three stacked rows whose dark run gets shorter (full, ~half, ~quarter)
    lengths = []
    for y in range(img.height()):
        run = sum(1 for x in range(img.width()) if QColor(img.pixel(x, y)).lightness() < 60
                  and x > 20 and x < 40)
        lengths.append(run)
    tail = [n for n in lengths[img.height() // 2:] if n]
    assert len(tail) >= 3 and tail[-3] > tail[-2] > tail[-1] > 0, lengths
    w.close()


def test_menus_hide_qt_underline():
    style = SgiProxyStyle()
    assert style.styleHint(QtWidgets.QStyle.SH_UnderlineShortcut, None, QtWidgets.QMenu()) == 0
    assert style.styleHint(QtWidgets.QStyle.SH_ScrollView_FrameOnlyAroundContents) == 1


# -- the gallery with the SGI themes -------------------------------------------------------

def test_browser_polish_and_unpolish(app):
    from ilp_gallery.ui import GalleryBrowser
    b = GalleryBrowser(theme="sgi")
    try:
        b.show()
        app.processEvents()
        styled = marked(b)
        assert b.gallery.tree in styled and b.gallery.view in styled
        menus = b.menuBar().findChildren(QtWidgets.QMenu)
        assert menus and all(m.property(_MARKED) for m in menus)        # menu marks installed
        assert b.gallery.delegate._bevel is not None                    # bevelled tiles
        b.set_theme("sgi-dark")
        app.processEvents()
        assert marked(b) and b.theme is SGI_DARK                        # SGI -> SGI keeps the style
        b.set_theme("ilp")
        app.processEvents()
        assert marked(b) == []                                          # ... and leaving SGI removes it
        assert b.gallery.delegate._bevel is None
        assert b.gallery.tree.style() is app.style() or not isinstance(b.gallery.tree.style(), SgiProxyStyle)
    finally:
        b.close()
        b.gallery.cache.shutdown()


def test_context_menus_are_polished(app):
    menu = QtWidgets.QMenu()
    sub = menu.addMenu("&Filter by tag")
    SGI.polish_menu(menu)
    assert menu.property(_MARKED) and sub.property(_MARKED)
    theme.ILP.polish_menu(QtWidgets.QMenu())                            # base hook: a no-op


def test_tiles_render_with_bevels(app):
    from ilp_gallery.core import make_record
    from ilp_gallery.ui import GalleryWidget
    g = GalleryWidget(theme="sgi")
    try:
        g.library.add_records([make_record(f"/tmp/x{i}.jpg", name=f"x{i}") for i in range(3)])
        g._refresh_from_library()
        g.resize(600, 400)
        g.show()
        app.processEvents()
        img = g.view.viewport().grab().toImage()
        assert not img.isNull()
    finally:
        g.close()
        g.cache.shutdown()


# -- thumbwheel ----------------------------------------------------------------------------------

def test_thumbwheel_drag_changes_value(app):
    w = ThumbWheel(Qt.Horizontal, SGI)
    w.setRange(-100, 100)
    w.resize(120, 20)
    w.show()
    app.processEvents()
    QTest.mousePress(w, Qt.LeftButton, Qt.NoModifier, QPoint(60, 10))
    QTest.mouseMove(w, QPoint(90, 10))
    QTest.mouseRelease(w, Qt.LeftButton, Qt.NoModifier, QPoint(90, 10))
    assert w.value() == 10                       # 30px at 3px per step
    w.close()


@pytest.mark.parametrize("t", [SGI, SGI_DARK, theme.ILP])
def test_thumbwheel_paints_with_any_theme(app, t):
    w = ThumbWheel(Qt.Vertical, t)
    w.resize(20, 120)
    img = QImage(w.size(), QImage.Format_ARGB32)
    img.fill(0)
    w.render(img)
    # the specular strip in the middle is the brightest thing on the wheel
    middle = max(QColor(img.pixel(x, 60)).lightness() for x in range(4, 16))
    end = max(QColor(img.pixel(x, 8)).lightness() for x in range(4, 16))
    assert middle > end
