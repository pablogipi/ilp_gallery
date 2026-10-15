"""Display names from item data: NameParts, acronyms, wrapping, tooltips, details panel."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from ilp_gallery.core import GalleryItem, RecordsSource, make_record  # noqa: E402
from ilp_gallery.ui.names import (  # noqa: E402
    NameParts, acronym, chip_color, format_name, split_title, stub_item, tooltip_html,
)


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def spin(app, t=0.3):
    end = time.time() + t
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def clip(item):
    """A host formatter reading the record, not the name."""
    md = item.metadata
    action = md.get("type_label")
    if not action:
        return None
    category = (item.categories or [""])[0]
    moves, variations = md.get("movement_var") or [], md.get("var") or []
    rows = [("Category", category), ("Action", action), ("Movement Variations", ", ".join(moves)),
            ("Variations", ", ".join(variations)), ("Actor", md.get("actor") or "")]
    return NameParts(" · ".join([action, *moves, *variations]), md.get("actor") or "",
                     chip=acronym(category), wrap=True, details=[r for r in rows if r[1]])


def rec(name, category, action, moves=(), variations=(), actor=None, path=None):
    return make_record(path or f"/pub/{name}.fbx", id=name, name=name, categories=[category],
                       type_label=action, tags=[*moves, *variations], movement_var=list(moves),
                       var=list(variations), **({"actor": actor} if actor else {}))


RECORDS = [rec("locomotion__run__fwd__001__genman", "locomotion", "run", ["fwd"], ["001"], "genman"),
           rec("locomotion__run__fwd__001__genwoman", "locomotion", "run", ["fwd"], ["001"], "genwoman"),
           rec("crowd__cheer__arms_up__clap__loop__002", "crowd", "cheer", ["arms up", "clap"], ["loop", "002"]),
           make_record("/pub/plain.fbx", id="plain", name="plain_name")]


def test_acronyms_and_colours():
    assert [acronym(w) for w in ("locomotion", "combat", "crowd", "idle", "audio", "fx", "")] == \
        ["LCM", "CMB", "CRW", "IDL", "AUD", "FX", ""]
    assert chip_color(NameParts("a", chip="LCM")) == chip_color(NameParts("b", chip="LCM"))
    assert chip_color(NameParts("a", chip="X", chip_color="#ff0000")).name() == "#ff0000"


def test_format_reads_the_item_and_never_raises():
    run = GalleryItem.from_dict(RECORDS[0])
    parts = format_name(clip, run)
    assert parts.title == "run · fwd · 001" and parts.chip == "LCM" and parts.subtitle == "genman"
    assert dict(parts.details)["Movement Variations"] == "fwd" and dict(parts.details)["Actor"] == "genman"
    assert format_name(clip, GalleryItem.from_dict(RECORDS[3])) == NameParts("plain_name")   # no action
    assert format_name(None, run) == NameParts(run.name)
    assert format_name(lambda it: 1 / 0, run) == NameParts(run.name)
    assert format_name(lambda it: ("t", "s"), run) == NameParts("t", "s")
    assert stub_item("gone").name == "gone" and format_name(clip, stub_item("gone")) == NameParts("gone")


def test_split_title_at_separators():
    fits = lambda t: len(t) <= 15
    assert split_title("cheer · arms up · clap", fits) == ("cheer · arms up", "clap")
    assert split_title("run · fwd", fits) == ("run · fwd", "")
    assert split_title("averyveryverylongword", fits) == ("averyveryverylongword", "")


def test_tooltip_html():
    tip = tooltip_html("a<b", NameParts("t", details=[("Action", "run")]), ["Disabled"])
    assert tip.startswith("<b>a&lt;b</b>") and "Action" in tip and "Disabled" in tip


def make(app, **kw):
    from ilp_gallery.ui.widget import GalleryWidget
    w = GalleryWidget(drawer=True, name_format=clip, **kw)
    w.add_source(RecordsSource(list(RECORDS)), background=False)
    w.resize(1300, 700)
    w.show()
    spin(app)
    return w


def test_tiles_drawer_tooltips_and_details(app):
    from ilp_gallery.ui.drawer import _RowDelegate
    w = make(app, show_paths=False, subtitles="when_needed")
    assert w.delegate.shared_titles == {"run · fwd · 001"}           # two actors share it
    idx = w.model.index_of("locomotion__run__fwd__001__genman")
    tip = w.model.data(idx, QtWidgets.QApplication.instance() and 3)   # Qt.ToolTipRole == 3
    assert tip.startswith("<b>locomotion__run__fwd__001__genman</b>") and "Actor" in tip
    assert "/pub/" not in tip                                         # no paths

    w.add_to_drawer([w._drawer_index[n] for n in ("locomotion__run__fwd__001__genman",
                                                  "crowd__cheer__arms_up__clap__loop__002")])
    w.set_drawer([*w.drawer_entries(), ("gone__clip", "/pub/gone.fbx")])
    assert isinstance(w.drawer.list.itemDelegate(), _RowDelegate)
    tips = [w.drawer.list.item(i).toolTip() for i in range(3)]
    assert all("/pub/" not in t for t in tips) and "Movement Variations" in tips[1]
    assert "Not in the library" in tips[2]

    w.set_collapsed("details", False)
    w.select("locomotion__run__fwd__001__genman")
    spin(app)
    d = w.details
    assert d.title.text().replace("\u200b", "") == "locomotion__run__fwd__001__genman"
    assert not d.sub.isVisibleTo(d)
    info = d.info.text()
    assert "Category" in info and "Actor" in info and "Location" not in info and "/pub/" not in info
    assert not d.folder_btn.isVisibleTo(d)
    w.grab()
    w.close()


def test_paths_shown_by_default(app):
    w = make(app)
    idx = w.model.index_of("plain")
    assert "/pub/plain.fbx" in w.model.data(idx, 3)
    w.set_collapsed("details", False)
    w.select("plain")
    spin(app)
    assert "Location" in w.details.info.text() and w.details.folder_btn.isVisibleTo(w.details)
    w.close()
