"""Theme: fusion-dark standalone, the host's palette inside Houdini / Nuke / Maya."""

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
    from ilp_gallery.ui.sgi import FUSION_DARK
    assert theme.resolve("ilp") is FUSION_DARK          # retired: the standalone default now
    custom = theme.ILP.replace(accent="#e08a3d")
    assert theme.resolve(custom) is custom
    with pytest.raises(ValueError):
        theme.resolve("purple")


def test_auto_is_fusion_dark_standalone(app):
    from ilp_gallery.ui.sgi import FUSION_DARK
    assert theme.detect_host() == "standalone"
    assert theme.resolve() is FUSION_DARK
    assert theme.resolve("auto") is FUSION_DARK


def test_auto_matches_host(host_palette):
    assert theme.detect_host() == "nuke"
    t = theme.resolve()
    assert t.name == "host:nuke"
    assert t.bg == "#323232" and t.panel == "#262626"
    assert t.accent == "#f7931e" and t.accent_text == "#000000"
    assert t.font_size is None                     # keep the host's font size
    assert "font-size: 10pt" not in t.stylesheet()


def test_env_overrides_auto(host_palette, monkeypatch):
    monkeypatch.setenv("ILP_GALLERY_THEME", "sgi-dark")
    assert theme.resolve().name == "sgi-dark"
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
        b.set_theme(theme.ILP)
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


# -- Houdini: colours come from hou.qt.getColor, not the Qt palette -----------

class _FakeHouQt:
    def __init__(self, colors):
        self.colors = colors

    def getColor(self, name):  # noqa: N802 (Houdini's API name)
        if name not in self.colors:
            raise RuntimeError("OperationFailed")
        return QColor(self.colors[name])


HOUDINI_DARK_PALETTE = {QPalette.Window: "#3a3a3a", QPalette.Base: "#131313",
                        QPalette.WindowText: "#cccccc", QPalette.Highlight: "#e0a030",
                        QPalette.HighlightedText: "#000000"}
HOUDINI_LIGHT_SCHEME = {"BackColor": "#d1d1d1", "TextColor": "#2e2e2e", "ButtonColor": "#d1d1d1"}


@pytest.fixture
def houdini_light(app):
    """Houdini Light as reported in the field: hou says light, the Qt palette stays dark."""
    old = app.palette()
    app.setPalette(palette(HOUDINI_DARK_PALETTE))
    hou = types.ModuleType("hou")
    hou.qt = _FakeHouQt(dict(HOUDINI_LIGHT_SCHEME))
    sys.modules["hou"] = hou
    yield hou
    del sys.modules["hou"]
    app.setPalette(old)


def test_houdini_uses_its_colour_scheme(houdini_light):
    t = theme.resolve()
    assert t.name == "host:houdini"
    assert t.bg == "#d1d1d1" and t.text == "#2e2e2e"
    assert QColor(t.panel).lightness() > 128          # not the palette's black Base
    assert t.accent == "#e0a030"                      # highlight still from the palette


def test_houdini_scheme_switch_is_picked_up_when_shown(houdini_light):
    from ilp_gallery.ui import GalleryBrowser
    b = GalleryBrowser()
    try:
        b.show()
        assert b.theme.bg == "#d1d1d1"
        b.hide()
        houdini_light.qt.colors.update(BackColor="#3a3a3a", TextColor="#cccccc", ButtonColor="#3a3a3a")
        b.show()                                       # e.g. switching back to the pane tab
        assert b.theme.bg == "#3a3a3a"
        assert "#3a3a3a" in b.styleSheet()             # window restyled via themeChanged
    finally:
        b.close()
        b.gallery.cache.shutdown()


def test_panel_never_mixes_light_and_dark():
    # light window, black Base, light text: what a half-updated host can report
    t = theme.from_palette(palette({QPalette.Window: "#d1d1d1", QPalette.Base: "#000000",
                                    QPalette.WindowText: "#e0e0e0"}))
    assert QColor(t.panel).lightness() > 128
    assert QColor(t.text).lightness() < 128           # unreadable text replaced


def test_follows_host_palette_changes(host_palette, app):
    from ilp_gallery.ui import GalleryBrowser
    b = GalleryBrowser()
    fixed = GalleryBrowser(theme=theme.ILP)
    try:
        b.show()
        fixed.show()
        assert b.theme.accent == "#f7931e"
        app.setPalette(palette({**NUKE_LIKE, QPalette.Highlight: "#30a0e0"}))
        for _ in range(5):
            app.processEvents()
        assert b.theme.accent == "#30a0e0"
        assert "#30a0e0" in b.styleSheet()
        assert fixed.theme is theme.ILP                # explicit themes don't follow
    finally:
        for w in (b, fixed):
            w.close()
            w.gallery.cache.shutdown()


def test_unstyled_widgets_get_the_theme_palette(houdini_light):
    from ilp_gallery.ui import GalleryBrowser
    b = GalleryBrowser()
    try:
        # e.g. the player's Loop check box: drawn from the palette, not the stylesheet
        assert b.gallery.details.palette().color(QPalette.Base) == QColor(b.theme.panel)
        assert QColor(b.theme.panel).lightness() > 128
    finally:
        b.close()
        b.gallery.cache.shutdown()


def test_links_are_readable():
    t = theme.from_palette(palette({QPalette.Window: "#d1d1d1", QPalette.Base: "#d1d1d1",
                                    QPalette.WindowText: "#2e2e2e", QPalette.Highlight: "#e0a030"}))
    assert theme._contrast(QColor(t.link), QColor(t.panel)) >= 4.5
