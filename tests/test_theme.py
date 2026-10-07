"""Theme: the ILP look standalone, the host's palette inside Houdini / Nuke / Maya."""

import os
import sys
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtGui import QColor, QPalette  # noqa: E402

from ilp_gallery.ui import theme  # noqa: E402

# Roughly Nuke's default look: mid greys with an orange highlight.
NUKE_LIKE = {
    QPalette.Window: "#323232", QPalette.Base: "#262626", QPalette.WindowText: "#c8c8c8",
    QPalette.Text: "#c8c8c8", QPalette.Button: "#3c3c3c", QPalette.ButtonText: "#c8c8c8",
    QPalette.Highlight: "#f7931e", QPalette.HighlightedText: "#000000",
}


def palette(colors: dict) -> QPalette:
    p = QPalette()
    for role, c in colors.items():
        p.setColor(role, QColor(c))
    return p


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def host_palette(app):
    """Pretend we're inside Nuke: its module is loaded and its palette is the app's."""
    old = app.palette()
    app.setPalette(palette(NUKE_LIKE))
    sys.modules["nuke"] = types.ModuleType("nuke")
    yield
    del sys.modules["nuke"]
    app.setPalette(old)


@pytest.fixture(autouse=True)
def no_env(monkeypatch):
    monkeypatch.delenv("ILP_GALLERY_THEME", raising=False)


def test_ilp_theme_is_backwards_compatible():
    assert theme.STYLESHEET == theme.ILP.stylesheet()
    assert theme.BG == theme.ILP.bg and theme.ACCENT == theme.ILP.accent
    assert "font-size: 10pt" in theme.STYLESHEET


def test_resolve(app):
    assert theme.resolve("ilp") is theme.ILP
    custom = theme.ILP.replace(accent="#e08a3d")
    assert theme.resolve(custom) is custom
    with pytest.raises(ValueError):
        theme.resolve("purple")


def test_auto_is_ilp_standalone(app):
    assert theme.detect_host() == "standalone"
    assert theme.resolve() is theme.ILP
    assert theme.resolve("auto") is theme.ILP


def test_auto_matches_host(host_palette):
    assert theme.detect_host() == "nuke"
    t = theme.resolve()
    assert t.name == "host:nuke"
    assert t.bg == "#323232" and t.panel == "#262626"
    assert t.accent == "#f7931e" and t.accent_text == "#000000"
    assert t.font_size is None                     # keep the host's font size
    assert "font-size: 10pt" not in t.stylesheet()


def test_env_overrides_auto(host_palette, monkeypatch):
    monkeypatch.setenv("ILP_GALLERY_THEME", "ilp")
    assert theme.resolve() is theme.ILP
    assert theme.resolve("host").name == "host:nuke"   # explicit argument still wins


def test_derived_colours_keep_contrast():
    dark = theme.from_palette(palette(NUKE_LIKE))
    assert QColor(dark.tile).lightness() > QColor(dark.bg).lightness()
    assert QColor(dark.image_bg).lightness() < QColor(dark.bg).lightness()
    light = theme.from_palette(palette({QPalette.Window: "#e6e6e6", QPalette.Base: "#e6e6e6",
                                        QPalette.WindowText: "#202020"}))
    assert QColor(light.tile).lightness() < QColor(light.bg).lightness()   # darker on light themes
    assert light.panel != light.bg                                          # Base == Window handled


def test_browser_uses_host_look_and_switches_live(host_palette):
    from ilp_gallery.ui import GalleryBrowser
    b = GalleryBrowser()
    try:
        assert b.theme.name == "host:nuke"
        assert "#f7931e" in b.styleSheet()
        assert b.gallery.delegate._accent == QColor("#f7931e")
        b.set_theme("ilp")
        assert b.theme is theme.ILP
        assert theme.ILP.accent in b.styleSheet()
        assert b.gallery.delegate._accent == QColor(theme.ILP.accent)
        assert b.gallery.details._theme is theme.ILP
    finally:
        b.close()
        b.gallery.cache.shutdown()


def test_explicit_theme_argument(app):
    from ilp_gallery.ui import GalleryWidget
    custom = theme.ILP.replace(accent="#e08a3d")
    w = GalleryWidget(theme=custom)
    try:
        assert w.theme is custom
        assert "#e08a3d" in w.styleSheet()
    finally:
        w.cache.shutdown()


def test_selected_row_text_is_readable():
    # Nuke: black HighlightedText would sit on a darkened orange -> use the light text instead
    t = theme.from_palette(palette(NUKE_LIKE))
    assert QColor(t.select_text).lightness() > QColor(t.select_bg).lightness()
