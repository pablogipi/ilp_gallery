"""Thin wrappers around the ffmpeg / ffprobe executables.

We shell out instead of using QtMultimedia because QtMultimedia's
backends are missing or unreliable inside Houdini / Maya / Nuke, while a
subprocess behaves identically everywhere.

Override the executables with ``ILP_GALLERY_FFMPEG`` / ``ILP_GALLERY_FFPROBE``
(handy with rez: ``ILP_GALLERY_FFMPEG={root}/bin/ffmpeg``).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction


def ffmpeg_exe() -> str | None:
    return os.environ.get("ILP_GALLERY_FFMPEG") or shutil.which("ffmpeg")


def ffprobe_exe() -> str | None:
    return os.environ.get("ILP_GALLERY_FFPROBE") or shutil.which("ffprobe")


def have_ffmpeg() -> bool:
    return bool(ffmpeg_exe() and ffprobe_exe())


@dataclass
class VideoInfo:
    width: int
    height: int
    fps: float
    duration: float  # seconds
    frames: int

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 16 / 9


_info_cache: dict[tuple[str, float], VideoInfo] = {}


def probe(path: str, timeout: float = 15.0) -> VideoInfo | None:
    """Return basic stream info, or None if the file can't be probed."""
    exe = ffprobe_exe()
    if not exe:
        return None
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return None
    if key in _info_cache:
        return _info_cache[key]
    cmd = [
        exe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,avg_frame_rate,r_frame_rate,nb_frames,duration",
        "-show_entries", "format=duration",
        "-of", "json", path,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout, check=True).stdout
        data = json.loads(out)
        st = data["streams"][0]
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, IndexError):
        return None

    def _rate(s: str | None) -> float:
        try:
            f = float(Fraction(s)) if s else 0.0
        except (ValueError, ZeroDivisionError):
            f = 0.0
        return f

    fps = _rate(st.get("avg_frame_rate")) or _rate(st.get("r_frame_rate")) or 24.0
    dur = float(st.get("duration") or data.get("format", {}).get("duration") or 0.0)
    try:
        frames = int(st.get("nb_frames") or 0)
    except ValueError:
        frames = 0
    if not frames and dur:
        frames = max(1, round(dur * fps))
    if not dur and frames:
        dur = frames / fps
    info = VideoInfo(int(st.get("width") or 0), int(st.get("height") or 0), fps, dur, frames)
    _info_cache[key] = info
    return info


def _even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def fit_size(w: int, h: int, max_w: int, max_h: int | None = None) -> tuple[int, int]:
    """Scale (w, h) to fit, keep aspect, even dimensions (required by most codecs)."""
    if not w or not h:
        return _even(max_w), _even(max_w * 9 / 16)
    s = min(1.0, max_w / w, (max_h / h) if max_h else 1.0)
    return _even(w * s), _even(h * s)


def extract_frames(path: str, out_dir: str, count: int, width: int,
                   info: VideoInfo | None = None, timeout: float = 120.0) -> list[str]:
    """Write ``count`` evenly spaced JPEG frames to ``out_dir``. Returns file paths."""
    exe = ffmpeg_exe()
    info = info or probe(path)
    if not exe or info is None:
        return []
    os.makedirs(out_dir, exist_ok=True)
    w, h = fit_size(info.width, info.height, width)
    scale = f"scale={w}:{h}:flags=bicubic"
    count = max(1, min(count, info.frames or count))
    base = [exe, "-v", "error", "-nostdin", "-y"]
    files: list[str] = []

    if info.duration <= 20.0:
        # Short clip: one decode pass, keep every Nth frame.
        rate = count / info.duration if info.duration else 1
        pattern = os.path.join(out_dir, "f%03d.jpg")
        cmd = base + ["-i", path, "-vf", f"fps={rate:.6f},{scale}", "-frames:v", str(count),
                      "-q:v", "4", pattern]
        try:
            subprocess.run(cmd, capture_output=True, timeout=timeout, check=True)
        except (subprocess.SubprocessError, OSError):
            return []
        files = sorted(os.path.join(out_dir, f) for f in os.listdir(out_dir)
                       if f.startswith("f") and f.endswith(".jpg"))
    else:
        # Long clip: seek to each sample instead of decoding everything.
        for i in range(count):
            t = info.duration * (i + 0.5) / count
            dst = os.path.join(out_dir, f"f{i + 1:03d}.jpg")
            cmd = base + ["-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-vf", scale,
                          "-q:v", "4", dst]
            try:
                subprocess.run(cmd, capture_output=True, timeout=timeout / count + 5, check=True)
            except (subprocess.SubprocessError, OSError):
                continue
            if os.path.exists(dst):
                files.append(dst)
    return files


def extract_still(path: str, dst: str, width: int, timeout: float = 30.0) -> bool:
    """Decode a single image with ffmpeg (used for formats Qt can't read, e.g. EXR)."""
    exe = ffmpeg_exe()
    if not exe:
        return False
    cmd = [exe, "-v", "error", "-nostdin", "-y", "-i", path, "-frames:v", "1",
           "-vf", f"scale='min({width},iw)':-2", "-q:v", "3", dst]
    try:
        subprocess.run(cmd, capture_output=True, timeout=timeout, check=True)
    except (subprocess.SubprocessError, OSError):
        return False
    return os.path.exists(dst)
