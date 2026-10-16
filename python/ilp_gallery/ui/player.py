"""Viewer for a single gallery item.

* Images: fit-to-window viewer.
* GIFs: QMovie (native Qt, fine everywhere).
* Movies: frames piped from an ffmpeg subprocess into a small ring buffer.
  No QtMultimedia, so it behaves the same standalone and inside DCCs.
  Audio is ignored on purpose (reference clips).
"""

from __future__ import annotations

import subprocess
import threading
from collections import deque

from PySide6.QtCore import QObject, QPointF, QRect, QRectF, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import (
    QColor, QIcon, QImage, QImageReader, QKeySequence, QMovie, QPainter, QPixmap, QPolygonF, QShortcut,
)
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QSlider, QToolButton, QVBoxLayout, QWidget,
)

from ..core.item import MEDIA_GIF, MEDIA_MOVIE, GalleryItem
from ..media import ffmpeg


class _ImageSignals(QObject):
    loaded = Signal(str, QImage)  # path, image


class _ImageJob(QRunnable):
    """Decode a still off the GUI thread (previews live on network shares)."""

    def __init__(self, path: str, max_width: int, signals: _ImageSignals) -> None:
        super().__init__()
        self.path, self.max_width, self.signals = path, max_width, signals

    def run(self) -> None:
        reader = QImageReader(self.path)
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and size.width() > self.max_width:
            reader.setScaledSize(size.scaled(self.max_width, 10 ** 6, Qt.KeepAspectRatio))
        self.signals.loaded.emit(self.path, reader.read())


class _Canvas(QWidget):
    """Draws a QImage/QPixmap fitted and centred."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pix: QPixmap | None = None
        self.background = Qt.black
        self.setMinimumSize(240, 180)
        self.setAttribute(Qt.WA_OpaquePaintEvent)

    def set_image(self, img) -> None:
        self._pix = QPixmap.fromImage(img) if isinstance(img, QImage) else img
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), self.background)
        if self._pix and not self._pix.isNull():
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            s = self._pix.size().scaled(self.size(), Qt.KeepAspectRatio)
            r = QRect(0, 0, s.width(), s.height())
            r.moveCenter(self.rect().center())
            p.drawPixmap(r, self._pix)


class FfmpegStream:
    """Decodes frames on a background thread into a bounded buffer."""

    def __init__(self, path: str, info: ffmpeg.VideoInfo, max_width: int = 1920, buffer: int = 48) -> None:
        self.path, self.info = path, info
        self.w, self.h = ffmpeg.fit_size(info.width, info.height, max_width, 1080 if max_width <= 1920 else None)
        self._buf: deque[tuple[int, QImage]] = deque()
        self._cap = buffer
        self._cond = threading.Condition()
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stop = False
        self.eof = False

    def start(self, frame: int = 0) -> None:
        self.stop()
        self._stop, self.eof = False, False
        self._buf.clear()
        t = frame / self.info.fps if self.info.fps else 0
        cmd = [ffmpeg.ffmpeg_exe() or "ffmpeg", "-v", "error", "-nostdin",
               "-ss", f"{t:.4f}", "-i", self.path, "-an",
               "-vf", f"scale={self.w}:{self.h}:flags=bilinear",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                      bufsize=self.w * self.h * 3 * 4)
        self._thread = threading.Thread(target=self._read, args=(frame,), daemon=True)
        self._thread.start()

    def _read(self, frame: int) -> None:
        size = self.w * self.h * 3
        out = self._proc.stdout
        while not self._stop:
            data = out.read(size)
            if not data or len(data) < size:
                break
            img = QImage(data, self.w, self.h, self.w * 3, QImage.Format_RGB888).copy()
            with self._cond:
                while len(self._buf) >= self._cap and not self._stop:
                    self._cond.wait(0.1)
                self._buf.append((frame, img))
            frame += 1
        with self._cond:
            self.eof = True

    def pop(self) -> tuple[int, QImage] | None:
        with self._cond:
            if not self._buf:
                return None
            item = self._buf.popleft()
            self._cond.notify()
            return item

    def exhausted(self) -> bool:
        with self._cond:
            return self.eof and not self._buf

    def stop(self) -> None:
        self._stop = True
        with self._cond:
            self._cond.notify_all()
        if self._proc:
            try:
                self._proc.kill()
                self._proc.wait(1)
            except Exception:
                pass
            self._proc = None
        if self._thread:
            self._thread.join(1)
            self._thread = None


class PlayerWidget(QWidget):
    """Embeddable viewer. Call :meth:`set_item` to show something."""

    frameChanged = Signal(int)

    def __init__(self, cache=None, parent=None) -> None:
        super().__init__(parent)
        self.cache = cache  # ThumbnailCache, used for instant preview while scrubbing
        self.item: GalleryItem | None = None
        self._stream: FfmpegStream | None = None
        self._movie: QMovie | None = None
        self._frame = 0
        self._playing = False

        self.canvas = _Canvas(self)
        # Painted icons, not "▶" text: host stylesheets (Houdini) frame text buttons
        # and fonts draw the glyph at very different sizes.
        self.play_btn = QToolButton(autoRaise=True)
        self.play_btn.setObjectName("playButton")
        self.play_btn.setFixedSize(30, 26)
        self.play_btn.setIconSize(QSize(20, 20))
        self.play_btn.setStyleSheet("QToolButton#playButton { border: none; background: transparent; padding: 0; }"
                                    "QToolButton#playButton:hover { background: rgba(127,127,127,60);"
                                    " border-radius: 4px; }")
        self.set_icon_color(QColor("#d9dce3"))
        self.slider = QSlider(Qt.Horizontal)
        self.frame_lbl = QLabel("")
        self.frame_lbl.setMinimumWidth(90)
        self.frame_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.loop_chk = QCheckBox("Loop", checked=True)
        self.info_lbl = QLabel("")
        self.info_lbl.setObjectName("muted")

        controls = QHBoxLayout()
        controls.setContentsMargins(6, 2, 6, 2)
        for w in (self.play_btn, self.slider, self.frame_lbl, self.loop_chk):
            controls.addWidget(w)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lay.addWidget(self.canvas, 1)
        lay.addLayout(controls)
        lay.addWidget(self.info_lbl)
        self.info_lbl.setVisible(False)  # details panel shows the metadata
        self._controls = [self.play_btn, self.slider, self.frame_lbl, self.loop_chk]

        # No Qt parent on purpose: queued jobs keep it alive via Python refs, so a job
        # finishing after this widget is deleted emits on a live object (and Qt drops it).
        self._img_signals = _ImageSignals()
        self._img_signals.loaded.connect(self._on_image_loaded)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self.play_btn.clicked.connect(self.toggle)
        self.slider.sliderMoved.connect(self._preview_at)
        self.slider.sliderReleased.connect(lambda: self.seek(self.slider.value()))
        self.slider.valueChanged.connect(self._on_slider_value)

        for key, fn in ((Qt.Key_Space, self.toggle), (Qt.Key_Left, lambda: self.step(-1)),
                        (Qt.Key_Right, lambda: self.step(1)), (Qt.Key_Home, lambda: self.seek(0))):
            QShortcut(QKeySequence(key), self, fn, context=Qt.WidgetWithChildrenShortcut)

    # -- public --------------------------------------------------------
    def set_item(self, item: GalleryItem | None, autoplay: bool = True) -> None:
        self.stop()
        self.info_lbl.setVisible(False)
        self.item = item
        self._frame = 0
        if item is None:
            self.canvas.set_image(None)
            self.info_lbl.setText("")
            return
        if item.type == MEDIA_MOVIE:
            info = ffmpeg.probe(item.path) if ffmpeg.have_ffmpeg() else None
            if info is None:
                self._show_error("Can't read movie (is ffmpeg installed?)")
                return
            self._stream = FfmpegStream(item.path, info)
            self._setup_range(info.frames, info.fps)
            self.info_lbl.setText(f"  {item.path}   {info.width}×{info.height}  {info.fps:.3g} fps  {info.frames} f")
            self._show_first_poster()
            if autoplay:
                self.play()
            else:
                self.seek(0)
        elif item.type == MEDIA_GIF:
            self._movie = QMovie(item.path)
            self._movie.setCacheMode(QMovie.CacheAll)
            self._movie.frameChanged.connect(self._on_gif_frame)
            self._movie.jumpToFrame(0)
            n = max(1, self._movie.frameCount())
            delay = self._movie.nextFrameDelay() or 40
            self._setup_range(n, 1000 / delay)
            self.info_lbl.setText(f"  {item.path}   {n} f")
            self._on_gif_frame(0)
            if autoplay:
                self.play()
        else:
            for w in self._controls:
                w.setVisible(False)
            self.canvas.set_image(None)
            self._show_first_poster()  # instant, from the thumbnail cache
            self.info_lbl.setText("")
            self._pool.start(_ImageJob(item.path, 2048, self._img_signals))

    def play(self) -> None:
        if self._movie:
            self._movie.setPaused(False) if self._movie.state() == QMovie.Paused else self._movie.start()
        elif self._stream:
            if self._stream_needs_restart:
                self._stream.start(self._frame)
                self._stream_needs_restart = False
            self._timer.start(max(1, int(1000 / (self._stream.info.fps or 24))))
        else:
            return
        self._playing = True
        self.play_btn.setIcon(self._icons["pause"])
        self.play_btn.setToolTip("Pause (Space)")

    def pause(self) -> None:
        self._playing = False
        self._show_play()
        if self._movie:
            try:
                self._movie.setPaused(True)
            except RuntimeError:          # already deleted (the app is shutting down)
                self._movie = None
        self._timer.stop()

    def set_icon_color(self, color: QColor) -> None:
        """Play / pause icons in ``color`` (the theme's text colour)."""
        self._icons = {kind: _media_icon(kind, QColor(color)) for kind in ("play", "pause")}
        self.play_btn.setIcon(self._icons["pause" if getattr(self, "_playing", False) else "play"])

    def _show_play(self) -> None:
        self.play_btn.setIcon(self._icons["play"])
        self.play_btn.setToolTip("Play (Space)")

    def toggle(self) -> None:
        self.pause() if self._playing else self.play()

    def stop(self) -> None:
        self._timer.stop()
        self._playing = False
        self._show_play()
        if self._stream:
            self._stream.stop()
            self._stream = None
        if self._movie:
            # Disconnect first and let Python free it now. deleteLater() here kept the
            # bound-method connection alive until a later event-loop pass, which could drop
            # the last reference to an already-closed player inside Qt's destructor (crash).
            movie, self._movie = self._movie, None
            movie.frameChanged.disconnect(self._on_gif_frame)
            movie.stop()
            del movie

    def seek(self, frame: int) -> None:
        frame = max(0, min(frame, self.slider.maximum()))
        self._frame = frame
        if self._movie:
            self._movie.jumpToFrame(frame)
        elif self._stream:
            self._stream.start(frame)
            self._stream_needs_restart = False
            if not self._playing:
                # Show one decoded frame, then stay paused.
                QTimer.singleShot(0, self._show_next_when_ready)
        self._update_label()

    def step(self, delta: int) -> None:
        self.pause()
        self.seek(self._frame + delta)

    # -- internals -------------------------------------------------------
    _stream_needs_restart = True

    def _setup_range(self, frames: int, fps: float) -> None:
        for w in self._controls:
            w.setVisible(True)
        self.slider.blockSignals(True)
        self.slider.setRange(0, max(0, frames - 1))
        self.slider.setValue(0)
        self.slider.blockSignals(False)
        self._stream_needs_restart = True
        self._update_label()

    def _tick(self) -> None:
        if not self._stream:
            return
        got = self._stream.pop()
        if got is None:
            if self._stream.exhausted():
                if self.loop_chk.isChecked():
                    self._frame = 0
                    self._stream.start(0)
                else:
                    self.pause()
                    self._stream_needs_restart = True
            return  # decoder is behind: hold the current frame
        self._frame, img = got
        self.canvas.set_image(img)
        self._sync_slider()

    def _show_next_when_ready(self, tries: int = 0) -> None:
        if not self._stream or self._playing:
            return
        got = self._stream.pop()
        if got:
            self._frame, img = got
            self.canvas.set_image(img)
            self._sync_slider()
            # The buffer continues from frame+1, which is exactly where play() resumes.
        elif tries < 100 and not self._stream.exhausted():
            QTimer.singleShot(10, lambda: self._show_next_when_ready(tries + 1))

    def _on_gif_frame(self, n: int) -> None:
        if not self._movie:
            return
        self._frame = n
        self.canvas.set_image(self._movie.currentPixmap())
        self._sync_slider()
        if n == self._movie.frameCount() - 1 and not self.loop_chk.isChecked():
            QTimer.singleShot(0, self.pause)

    def _preview_at(self, frame: int) -> None:
        """While dragging: show the nearest cached scrub frame instantly."""
        self._frame = frame
        self._update_label()
        if self._movie:
            self._movie.jumpToFrame(frame)
            return
        if self.cache and self.item:
            frames = self.cache.frames(self.item)
            if frames:
                i = min(len(frames) - 1, int(frame / max(1, self.slider.maximum() + 1) * len(frames)))
                self.canvas.set_image(frames[i])

    def _on_slider_value(self, v: int) -> None:
        if not self.slider.isSliderDown() and v != self._frame:
            self.seek(v)

    def _sync_slider(self) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(self._frame)
        self.slider.blockSignals(False)
        self._update_label()
        self.frameChanged.emit(self._frame)

    def _update_label(self) -> None:
        self.frame_lbl.setText(f"{self._frame + 1} / {self.slider.maximum() + 1}")

    def _show_first_poster(self) -> None:
        if self.cache and self.item:
            pix = self.cache.poster(self.item)
            if pix:
                self.canvas.set_image(pix)

    def _on_image_loaded(self, path: str, img: QImage) -> None:
        if not self.item or self.item.path != path or self.item.is_animated:
            return  # user moved on
        if img.isNull():
            if self.canvas._pix is None:
                self._show_error("Can't read image")
            return
        self.canvas.set_image(img)

    def _show_error(self, msg: str) -> None:
        for w in self._controls:
            w.setVisible(False)
        self.canvas.set_image(None)
        self.info_lbl.setText(f"  {msg}: {self.item.path if self.item else ''}")
        self.info_lbl.setVisible(True)

    def sizeHint(self) -> QSize:
        return QSize(960, 600)

    def closeEvent(self, e) -> None:
        self.stop()
        super().closeEvent(e)

    def hideEvent(self, e) -> None:
        self.pause()
        super().hideEvent(e)


def _media_icon(kind: str, color: QColor, size: int = 28) -> QIcon:
    """A crisp play triangle or pause bars, drawn at 2x for high-DPI screens."""
    pix = QPixmap(size * 2, size * 2)
    pix.fill(Qt.transparent)
    pix.setDevicePixelRatio(2.0)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    s = float(size)
    if kind == "play":
        p.drawPolygon(QPolygonF([QPointF(s * 0.30, s * 0.20), QPointF(s * 0.30, s * 0.80),
                                 QPointF(s * 0.80, s * 0.50)]))
    else:
        for x in (0.28, 0.56):
            p.drawRoundedRect(QRectF(s * x, s * 0.22, s * 0.16, s * 0.56), 1.5, 1.5)
    p.end()
    return QIcon(pix)
