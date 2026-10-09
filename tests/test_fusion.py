"""The SGI palettes in Qt's Fusion style: names, style=, polish / unpolish."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtGui import QColor, QPalette  # noqa: E402

from ilp_gallery.ui import theme  # noqa: E402
from ilp_gallery.ui.sgi import (  # noqa: E402
    FUSION, FUSION_DARK, FUSION_SLATE, SGI, SGI_DARK, SGI_SLATE, FusionTheme, SgiTheme, with_style,
)
from ilp_gallery.ui.sgi import fusion as fusion_mod  # noqa: E402
from ilp_gallery.ui.sgi.style import _MARKED as SGI_MARK  # noqa: E402

LOOKS = [("sgi", None), ("sgi-dark", None), ("sgi-slate", None),
         ("sgi", "fusion"), ("sgi-dark", "fusion"), ("fusion-slate", None)]


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ILP_GALLERY_SGI_CACHE", str(tmp_path / "sgi"))
    monkeypatch.delenv("ILP_GALLERY_THEME", raising=False)


def restyled(root, mark):
    return [w for w in [root] + root.findChildren(QtWidgets.QWidget) if w.property(mark)]


def test_names_and_style_argument():
    assert theme.resolve("fusion") is FUSION
    assert theme.resolve("fusion-dark") is FUSION_DARK
    assert theme.resolve("fusion-slate") is FUSION_SLATE
    assert theme.resolve("sgi-dark", style="fusion") == FUSION_DARK
    assert theme.resolve("fusion-dark", style="sgi") == SGI_DARK
    assert theme.resolve("sgi", style="sgi") is SGI
    assert isinstance(FUSION, FusionTheme) and isinstance(FUSION, SgiTheme)


def test_same_palette_both_styles():
    for s, f in ((SGI, FUSION), (SGI_DARK, FUSION_DARK), (SGI_SLATE, FUSION_SLATE)):
        assert (s.bg, s.accent, s.text, s.desktop) == (f.bg, f.accent, f.text, f.desktop)
    assert with_style(SGI.replace(accent="#22aa77"), "fusion").accent == "#22aa77"


def test_style_errors():
    with pytest.raises(ValueError):
        theme.resolve("sgi", style="plastique")
    with pytest.raises(ValueError):
        theme.resolve("ilp", style="fusion")        # Fusion looks use the SGI palettes


def test_env_selects_fusion(monkeypatch):
    monkeypatch.setenv("ILP_GALLERY_THEME", "fusion-dark")
    assert theme.resolve() is FUSION_DARK


def test_palette_drives_fusion(app):
    p = FUSION_DARK.qpalette()
    assert p.color(QPalette.Button) == QColor(FUSION_DARK.tile)
    assert p.color(QPalette.Highlight) == QColor(FUSION_DARK.accent)
    assert p.color(QPalette.Dark) == QColor(FUSION_DARK.shadow)
    assert FUSION.tile_bevel() is None              # Fusion keeps rounded tiles


def test_browser_in_fusion(app):
    from ilp_gallery.ui import GalleryBrowser
    b = GalleryBrowser(theme="sgi-dark", style="fusion")
    try:
        b.show()
        app.processEvents()
        assert b.theme == FUSION_DARK
        assert b.gallery.tree in restyled(b, fusion_mod._MARKED)
        assert restyled(b, SGI_MARK) == []                              # no SGI proxy style
        export = b.gallery.details.export_btn
        assert export.palette().color(QPalette.Button) == QColor(FUSION_DARK.accent)   # accent kept
        menus = b.menuBar().findChildren(QtWidgets.QMenu)
        assert menus and all(m.palette().color(QPalette.Window) == QColor(FUSION_DARK.bg) for m in menus)
    finally:
        b.close()
        b.gallery.cache.shutdown()


def test_cycle_all_six_looks_and_back(app):
    from ilp_gallery.ui import GalleryBrowser
    b = GalleryBrowser(theme="ilp")
    try:
        b.show()
        export = b.gallery.details.export_btn
        for name, style in LOOKS * 2:
            b.set_theme(name, style)
            app.processEvents()
            is_fusion = isinstance(b.theme, FusionTheme)
            assert bool(restyled(b, fusion_mod._MARKED)) == is_fusion, (name, style)
            assert bool(restyled(b, SGI_MARK)) == (not is_fusion), (name, style)
            assert (b.gallery.delegate._bevel is None) == is_fusion
            if is_fusion:   # the palette reaches widgets under the stylesheet after a live switch
                assert b.gallery.tree.palette().color(QPalette.Base) == QColor(b.theme.well), (name, style)
                assert b.gallery.log_view.palette().color(QPalette.Base) == QColor(b.theme.well) \
                    if hasattr(b.gallery, "log_view") else True
        b.set_theme("ilp")
        app.processEvents()
        assert restyled(b, fusion_mod._MARKED) == [] and restyled(b, SGI_MARK) == []
        assert not isinstance(b.gallery.tree.style(), QtWidgets.QProxyStyle)
        assert export.palette().color(QPalette.Button) != QColor(FUSION_DARK.accent)   # accent palette removed
    finally:
        b.close()
        b.gallery.cache.shutdown()


def test_context_menu_polish(app):
    menu = QtWidgets.QMenu()
    sub = menu.addMenu("Filter by tag")
    FUSION_SLATE.polish_menu(menu)
    assert menu.property(fusion_mod._MARKED) and sub.property(fusion_mod._MARKED)
    assert sub.palette().color(QPalette.Window) == QColor(FUSION_SLATE.bg)
