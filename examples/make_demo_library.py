"""Generate a small demo library (movies, GIFs, images + JSON) with ffmpeg.

    python examples/make_demo_library.py /tmp/gallery_demo
    ilp_gallery /tmp/gallery_demo/library.json
"""

import json
import os
import subprocess
import sys

SOURCES = {
    "testsrc2": "testsrc2=size=1280x720:rate=24",
    "mandelbrot": "mandelbrot=size=960x540:rate=24",
    "life": "life=size=640x360:rate=24:mold=10:ratio=0.2:death_color=#101030:life_color=#ec9628",
    "smpte": "smptehdbars=size=1280x720:rate=24",
    "gradients": "gradients=size=1280x720:rate=24:speed=0.05",
    "cellauto": "cellauto=size=640x360:rate=24:rule=110",
}

ITEMS = [
    ("walk_cycle_male", "movie", "testsrc2", 4, ["crowd", "locomotion"], ["walk", "male", "loop"]),
    ("run_cycle_female", "movie", "gradients", 3, ["crowd", "locomotion"], ["run", "female"]),
    ("idle_breathing", "movie", "life", 6, ["crowd", "idle"], ["idle", "loop"]),
    ("fractal_zoom", "movie", "mandelbrot", 30, ["fx", "reference"], ["hero shot", "abstract"]),
    ("fire_loop", "gif", "life", 3, ["fx"], ["fire", "loop"]),
    ("magic_sparkle", "gif", "cellauto", 2, ["fx"], ["magic", "particles"]),
    ("colour_bars", "image", "smpte", 0, ["reference", "technical"], ["calibration"]),
    ("forest_plate", "image", "gradients", 0, ["plates", "environment"], ["forest", "day"]),
    ("test_pattern", "image", "testsrc2", 0, ["technical"], ["calibration", "grid"]),
]


def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main(out):
    media = os.path.join(out, "media")
    os.makedirs(media, exist_ok=True)
    records = []
    for name, mtype, src, dur, cats, tags in ITEMS:
        base = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", SOURCES[src]]
        if mtype == "movie":
            path = os.path.join(media, f"{name}.mov")
            run(base + ["-t", str(dur), "-c:v", "libx264", "-pix_fmt", "yuv420p", path])
        elif mtype == "gif":
            path = os.path.join(media, f"{name}.gif")
            run(base + ["-t", str(dur), "-vf", "fps=15,scale=480:-2:flags=lanczos", path])
        else:
            path = os.path.join(media, f"{name}.png")
            run(base + ["-frames:v", "1", path])
        records.append({
            "id": name, "name": name.replace("_", " ").title(), "path": f"media/{os.path.basename(path)}",
            "type": mtype, "categories": cats, "tags": tags,
            "description": f"Demo {mtype} generated from ffmpeg '{src}'",
            "metadata": {"source": src, "seconds": dur},
        })
        print("made", path)
    with open(os.path.join(out, "library.json"), "w") as fh:
        json.dump({"items": records}, fh, indent=2)
    print("library:", os.path.join(out, "library.json"))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "gallery_demo")
