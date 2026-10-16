"""Shared libraries: several galleries, one library, sources loaded once, one poller."""

import gc
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from ilp_gallery.core import Changes, DataSource, make_record  # noqa: E402
from ilp_gallery.ui import shared as shared_mod  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def wait(app, cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


class Clips(DataSource):
    name = "clips"
    label = "Motion Clips"
    poll_interval = 0.2
    loads = 0
    polls = 0
    records = []

    def load(self):
        type(self).loads += 1
        return list(type(self).records)

    def poll(self):
        type(self).polls += 1
        return None


@pytest.fixture
def key():
    Clips.loads = Clips.polls = 0
    Clips.records = [make_record(f"/c/{n}.fbx", id=n, name=n) for n in ("walk", "run")]
    yield "test-shared"
    shared_mod.drop_shared("test-shared")


def test_sources_load_once_and_galleries_share_items(app, key):
    from ilp_gallery.ui.widget import GalleryWidget
    a = GalleryWidget(shared=key)
    src_a = a.add_source(Clips())
    assert wait(app, lambda: len(a.library) == 2)
    b = GalleryWidget(shared=key)
    src_b = b.add_source(Clips())                     # a new instance, as a new panel makes
    assert src_b is src_a and Clips.loads == 1         # reused, not loaded again
    assert b.library is a.library and b.cache is a.cache
    assert b.model.ids() and sorted(b.model.ids()) == ["run", "walk"]
    assert b.panes["grid"].title() == "Motion Clips"

    Clips.records.append(make_record("/c/idle.fbx", id="idle", name="idle"))
    b.reload_source(src_b)                             # a Refresh in one panel...
    assert wait(app, lambda: len(a.model.ids()) == 3)  # ...updates the other
    assert Clips.loads == 2
    a.close(); b.close()


def test_one_gallery_polls_and_another_takes_over(app, key):
    from ilp_gallery.ui.widget import GalleryWidget
    a, b = GalleryWidget(shared=key), GalleryWidget(shared=key)
    a.add_source(Clips()); b.add_source(Clips())
    assert wait(app, lambda: Clips.polls >= 3, timeout=6)
    time.sleep(0)
    polls_before, t0 = Clips.polls, time.time()
    assert wait(app, lambda: time.time() - t0 > 2.5, timeout=4)
    rate = (Clips.polls - polls_before) / 2.5          # ~1 per second (the poll timer), not 2
    assert rate < 1.6
    owner = shared_mod.shared(key).poller()
    owner.close()
    other = b if owner is a else a
    n = Clips.polls
    assert wait(app, lambda: Clips.polls > n, timeout=4)
    assert shared_mod.shared(key).poller() is other
    other.close()


def test_deleted_gallery_unregisters_its_listener(app, key):
    from ilp_gallery.ui.widget import GalleryWidget
    a = GalleryWidget(shared=key)
    a.add_source(Clips())
    assert wait(app, lambda: len(a.library) == 2)
    lib = a.library
    n = len(lib._listeners)
    b = GalleryWidget(shared=key)
    assert len(lib._listeners) == n + 1
    b.deleteLater()                                    # deleted without closeEvent, as a host may
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not shared_mod.alive(b)
    del b
    gc.collect()
    Clips.records.append(make_record("/c/idle.fbx", id="idle", name="idle"))
    a.reload_source("clips", background=False)         # notifies: the dead one drops out
    assert len(lib._listeners) == n
    a.close()


def test_unshared_galleries_stay_independent(app):
    from ilp_gallery.ui.widget import GalleryWidget
    a, b = GalleryWidget(), GalleryWidget()
    assert a.library is not b.library and a.cache is not b.cache
    a.close(); b.close()
