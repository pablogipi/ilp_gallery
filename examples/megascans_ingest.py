"""Example: the Megascans gallery driving your own ingest tool.

Run standalone:
    python examples/megascans_ingest.py /ice/resources/ASSET_LIB/megascans/megascans.json.gz

or from a DCC (Houdini shelf, Nuke menu, ...):
    import megascans_ingest; megascans_ingest.show()

Replace ``ingest`` with a call into your ingest project. It receives the asset's
original Megascans JSON file, as requested (``arg="json"``).
"""

from __future__ import annotations

import sys

from ilp_gallery import dcc
from ilp_gallery.sources.megascans import MegascansSource, texture_files

DEFAULT_CACHE = "/ice/resources/ASSET_LIB/megascans/megascans.json.gz"


def ingest(json_file: str, log) -> bool:
    """Called once per selected asset, on a worker thread (UI stays responsive, Stop works)."""
    log(f"Ingesting {json_file}")
    # from ilp_ingest import megascans
    # megascans.ingest(json_file, project=os.environ["ILP_PROJECT"])
    return True


def show(cache: str = DEFAULT_CACHE):
    browser = dcc.show([], name="Megascans")
    browser.add_source(MegascansSource(cache=cache))
    browser.register_exporter(ingest, label="Ingest into project", arg="json", default=True,
                              sources=["megascans"])
    # Drags carry the albedo map instead of the preview image:
    browser.register_drag_handler(
        lambda items: [texture_files(it, ["albedo"]).get("albedo", it.path) for it in items],
        sources=["megascans"])
    return browser


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    b = show(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CACHE)
    sys.exit(app.exec())
