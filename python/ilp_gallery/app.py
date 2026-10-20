"""Standalone app and CLI.

    ilp_gallery library.json [more.json | folder ...]
    ilp_gallery scan /path/to/media -o library.json   # build JSON from a folder tree
    ilp_gallery megascans -o megascans.json           # build JSON from the Megascans downloads
    ilp_gallery search library.json "tag:walk -loop"   # headless search, prints paths
    ilp_gallery thumbs library.json                     # pre-generate thumbnails (farm-friendly)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time


def _cmd_scan(args) -> int:
    from .core.library import scan_folder
    records = scan_folder(args.folder, recursive=not args.flat)
    payload = json.dumps({"items": records}, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(payload)
        print(f"Wrote {len(records)} items to {args.output}", file=sys.stderr)
    else:
        print(payload)
    return 0


def _cmd_megascans(args) -> int:
    from .sources.megascans import build_library_json

    def progress(n, total):
        print(f"  {n}/{total} asset folders", file=sys.stderr)

    t = time.perf_counter()
    n, reused = build_library_json(args.output, root=args.root, full=args.full,
                                   threads=args.threads, progress=progress)
    print(f"Wrote {n} assets ({reused} unchanged) to {args.output} in {time.perf_counter() - t:.1f} s",
          file=sys.stderr)
    return 0


def _cmd_search(args) -> int:
    from .core.library import GalleryLibrary
    lib = GalleryLibrary()
    for s in args.sources:
        lib.load(s)
    t = time.perf_counter()
    ids = lib.search(args.query, limit=args.limit)
    dt = (time.perf_counter() - t) * 1000
    for i in ids:
        it = lib.get(i)
        print(it.path if not args.json else json.dumps(it.to_dict()))
    print(f"{len(ids)} of {len(lib)} items in {dt:.1f} ms", file=sys.stderr)
    return 0


def _cmd_thumbs(args) -> int:
    from concurrent.futures import ThreadPoolExecutor
    from PySide6.QtGui import QGuiApplication
    from .core.library import GalleryLibrary
    from .media.thumbs import DEFAULT_CACHE, generate

    _app = QGuiApplication.instance() or QGuiApplication(["ilp_gallery", "-platform", "offscreen"])
    lib = GalleryLibrary()
    for s in args.sources:
        lib.load(s)
    items = list(lib)
    done = 0
    with ThreadPoolExecutor(args.threads) as pool:
        for files in pool.map(lambda it: generate(it, DEFAULT_CACHE), items):
            done += bool(files)
    print(f"Thumbnails ready for {done}/{len(items)} items in {DEFAULT_CACHE}", file=sys.stderr)
    return 0


def _cmd_gui(args) -> int:
    from PySide6.QtWidgets import QApplication
    from .ui.browser import GalleryBrowser, setting

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("ILP Gallery")
    app.setStyle("Fusion")
    _dark_palette(app)
    w = GalleryBrowser()
    sources = args.sources or [s for s in setting("library/sources").split(":") if s]
    for s in sources:
        w.load(s, recursive=args.recursive, background=True)
    if not sources:
        w.log("No library loaded: pass a JSON on the command line or set one in Settings.")
    if args.query:
        w.gallery.set_query(args.query)
    w.show()
    rc = app.exec()
    w.gallery.cache.shutdown()
    return rc


def _dark_palette(app) -> None:
    from PySide6.QtGui import QColor, QPalette
    p = QPalette()
    for role, c in {
        QPalette.Window: (29, 32, 39), QPalette.WindowText: (220, 220, 220),
        QPalette.Base: (35, 38, 46), QPalette.AlternateBase: (42, 46, 55),
        QPalette.Text: (220, 220, 220), QPalette.Button: (42, 46, 55),
        QPalette.ButtonText: (220, 220, 220), QPalette.Highlight: (61, 127, 224),
        QPalette.HighlightedText: (255, 255, 255), QPalette.ToolTipBase: (30, 30, 32),
        QPalette.ToolTipText: (220, 220, 220), QPalette.PlaceholderText: (120, 120, 125),
    }.items():
        p.setColor(role, QColor(*c))
    app.setPalette(p)

def parse_args( argv: list[str] | None = None ):
    """Parse arguments
    """
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(prog="ilp_gallery", description="Image / movie gallery")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("scan", help="build a library JSON from a folder of media")
    p.add_argument("folder")
    p.add_argument("-o", "--output")
    p.add_argument("--flat", action="store_true", help="don't recurse")
    p.set_defaults(fn=_cmd_scan)

    from .sources.megascans import DEFAULT_ROOT
    p = sub.add_parser("megascans", help="build a library JSON from the Megascans download folder")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--root", default=DEFAULT_ROOT)
    p.add_argument("--full", action="store_true", help="rescan everything (default: reuse unchanged)")
    p.add_argument("--threads", type=int, default=16)
    p.set_defaults(fn=_cmd_megascans)

    p = sub.add_parser("search", help="search from the command line")
    p.add_argument("sources", nargs="+")
    p.add_argument("query")
    p.add_argument("--limit", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_search)

    p = sub.add_parser("thumbs", help="pre-generate thumbnails")
    p.add_argument("sources", nargs="+")
    p.add_argument("--threads", type=int, default=4)
    p.set_defaults(fn=_cmd_thumbs)

    p = sub.add_parser("gui", help="open the gallery (default)")
    p.add_argument("sources", nargs="*")
    p.add_argument("-q", "--query")
    p.add_argument("-r", "--recursive", action="store_true")
    p.set_defaults(fn=_cmd_gui)

    if not argv or argv[0] not in {"scan", "megascans", "search", "thumbs", "gui", "-h", "--help"}:
        argv = ["gui", *argv]
    args = parser.parse_args(argv)
    return args


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args( argv )

    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
