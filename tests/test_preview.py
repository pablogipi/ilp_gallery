"""Preview movies for items that don't move themselves (published clips, models...),
the missing-thumbnail icon, the Preview command, renaming the main action, and drag
handlers per host."""

import os
import shutil
import subprocess
import sys
import time
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtGui import QIcon, QImage, QPixmap  # noqa: E402

from ilp_gallery.core import GalleryItem, make_record  # noqa: E402
from ilp_gallery.media import ffmpeg, thumbs  # noqa: E402
from ilp_gallery.ui.actions import DragHandler, build_mime  # noqa: E402
from ilp_gallery.ui.tasks import format_command  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not ffmpeg.have_ffmpeg(), reason="ffmpeg not installed")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("clips")
    clip = d / "run_fwd_001.bclip"
    clip.write_text("not media")
    still = d / "still.png"
    img = QImage(64, 36, QImage.Format_RGB32)
    img.fill(0x3366AA)
    img.save(str(still))
    movie = d / "run_fwd_001.mp4"
    if ffmpeg.have_ffmpeg():
        subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc=size=160x90:rate=24:duration=2", "-pix_fmt", "yuv420p", str(movie)], check=True)
    return types.SimpleNamespace(dir=d, clip=str(clip), movie=str(movie), still=str(still))


def clip_item(media, **kw):
    return GalleryItem(**make_record(media.clip, id="run_fwd_001", type_label="Motion clip", **kw))


def wait(app, cond, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


# -- the item -----------------------------------------------------------------------

def test_preview_and_motion(media):
    it = clip_item(media, preview=media.movie)
    assert it.preview == media.movie
    assert it.motion == (media.movie, "movie")
    played = it.motion_item()
    assert (played.path, played.type, played.id) == (media.movie, "movie", "run_fwd_001")
    assert clip_item(media).motion is None
    assert clip_item(media, preview=media.still).motion is None        # a still image doesn't move
    movie_itself = GalleryItem(path=media.movie)
    assert movie_itself.motion_item() is movie_itself


def test_preview_placeholders_in_commands(media):
    it = clip_item(media, preview=media.movie)
    assert format_command("rv {preview}", it) == f"rv {media.movie}"
    assert format_command("rv {preview}", GalleryItem(path=media.movie)) == f"rv {media.movie}"


# -- thumbnails ------------------------------------------------------------------------

@needs_ffmpeg
def test_scrub_frames_come_from_the_preview_movie(media, tmp_path):
    frames = thumbs.generate(clip_item(media, preview=media.movie), str(tmp_path), frames=6)
    assert len(frames) == 6                                         # poster + scrub frames from the mp4
    assert all(os.path.isfile(f) for f in frames)


def test_still_preview_is_used_as_poster(media, tmp_path):
    frames = thumbs.generate(clip_item(media, preview=media.still), str(tmp_path))
    assert len(frames) == 1 and not QImage(frames[0]).isNull()


def test_no_preview_no_frames(media, tmp_path):
    assert thumbs.generate(clip_item(media), str(tmp_path)) == []    # -> the placeholder icon


# -- placeholder icon ----------------------------------------------------------------------

def test_placeholder_icon(app, media, monkeypatch):
    from ilp_gallery.ui import widget
    from ilp_gallery.ui import GalleryWidget
    assert os.path.isfile(widget.PLACEHOLDER_SVG)
    g = GalleryWidget()
    try:
        assert not g.delegate.placeholder.isNull()
        g.set_placeholder(media.still)
        assert not g.delegate.placeholder.isNull()
        g.set_placeholder(QPixmap(8, 8))
        assert isinstance(g.delegate.placeholder, QIcon)
        g.set_placeholder(None)                                         # back to the default
        assert not g.delegate.placeholder.isNull()
    finally:
        g.cache.shutdown()


def test_houdini_uses_its_own_warning_icon(app, monkeypatch):
    from ilp_gallery.ui import widget
    asked = []
    pix = QPixmap(16, 16)
    pix.fill()
    hou = types.ModuleType("hou")
    hou.qt = types.SimpleNamespace(Icon=lambda name: asked.append(name) or QIcon(pix))
    monkeypatch.setitem(sys.modules, "hou", hou)
    icon = widget.default_placeholder()
    assert asked == ["BUTTONS_list_warning_up"] and not icon.isNull()


# -- preview command -----------------------------------------------------------------------

@pytest.fixture
def launched(monkeypatch):
    calls = []

    class FakePopen:
        def __init__(self, args, **kw):
            calls.append(args)

    monkeypatch.setattr("ilp_gallery.ui.widget.subprocess.Popen", FakePopen)
    return calls


@pytest.fixture
def gallery(app, media):
    from ilp_gallery.ui import GalleryWidget
    g = GalleryWidget()
    g.set_items([make_record(media.clip, id="a", preview=media.movie),
                 make_record(media.clip + "2", id="b", preview=media.movie),
                 make_record(media.clip + "3", id="c")])                 # no preview
    yield g
    g.cache.shutdown()


def test_preview_command_per_item(gallery, launched, media):
    gallery.set_preview_command("rv {preview}")
    items = [gallery.library.get(i) for i in ("a", "b", "c")]
    assert gallery.preview(items) == 2                                 # "c" has nothing to play
    assert launched == [["rv", media.movie], ["rv", media.movie]]


def test_preview_command_all_at_once(gallery, launched, media):
    gallery.set_preview_command("rv -fullscreen {previews}")
    assert gallery.preview([gallery.library.get("a"), gallery.library.get("b")]) == 1
    assert launched == [["rv", "-fullscreen", media.movie, media.movie]]


def test_preview_without_command_logs(gallery, launched):
    lines = []
    gallery.logMessage.connect(lines.append)
    gallery.set_preview_command("")
    assert gallery.preview([gallery.library.get("a")]) == 0 and launched == []
    assert any("No preview command" in m for m in lines)


def test_preview_button_only_for_items_that_move(gallery, app):
    gallery.set_preview_command("rv {preview}")
    d = gallery.details
    d.set_item(gallery.library.get("a"))
    assert d.preview_btn.isVisibleTo(d)
    d.set_item(gallery.library.get("c"))
    assert not d.preview_btn.isVisibleTo(d)
    gallery.set_preview_command("")                                    # no command: no button
    d.set_item(gallery.library.get("a"))
    assert not d.preview_btn.isVisibleTo(d)


def test_details_player_plays_the_preview(gallery):
    gallery.details.set_item(gallery.library.get("a"))
    assert gallery.details.player.item.path.endswith(".mp4")


# -- main action label ---------------------------------------------------------------------

def test_export_label(app):
    from ilp_gallery.ui import GalleryBrowser
    b = GalleryBrowser(theme="ilp")
    try:
        labels = []
        b.gallery.exportLabelChanged.connect(labels.append)
        b.gallery.register_exporter(lambda it: True, label="Ingest", default=True)
        assert b.gallery.export_label() == "Ingest" and labels[-1] == "Ingest"
        assert b.export_btn.text() == "Ingest selected"
        b.gallery.set_export_label("Assign to shot")
        assert b.export_btn.text() == "Assign to shot selected"
        b.gallery.details.set_export_label("Assign to shot")
        assert b.gallery.details.export_btn.text() == "Assign to shot"   # no more 14-character limit
        b.gallery.details.set_export_label("A very long action label for this")
        assert b.gallery.details.export_btn.text().endswith("…")
        b.gallery.set_export_label(None)
        assert b.gallery.export_label() == "Ingest"
    finally:
        b.close()
        b.gallery.cache.shutdown()


# -- drag handlers per host -------------------------------------------------------------------

def test_drag_handlers_per_host(media):
    it = GalleryItem(path=media.movie)
    houdini = DragHandler(lambda items: {"application/x-houdini": "hou"}, hosts=("houdini",))
    nuke = DragHandler(lambda items: {"application/x-nuke": "nuke"}, hosts=("nuke",))
    anywhere = DragHandler(lambda items: {"application/x-any": "any"})
    md = build_mime([it], [houdini, nuke, anywhere], host="nuke")
    assert md.hasFormat("application/x-nuke") and md.hasFormat("application/x-any")
    assert not md.hasFormat("application/x-houdini")
    md = build_mime([it], [houdini, nuke, anywhere], host="houdini")
    assert md.hasFormat("application/x-houdini") and not md.hasFormat("application/x-nuke")


def test_register_drag_handler_hosts(app):
    from ilp_gallery.ui import GalleryWidget
    g = GalleryWidget()
    try:
        h = g.register_drag_handler(lambda items: None, hosts=["Houdini", "maya"])
        assert h.hosts == ("houdini", "maya")
        assert h.applies("maya") and not h.applies("nuke")
    finally:
        g.cache.shutdown()
