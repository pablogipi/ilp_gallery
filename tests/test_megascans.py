import json
import os
import shutil

import pytest

from ilp_gallery.core import GalleryLibrary
from ilp_gallery.sources import megascans

FIXTURE = os.path.join(os.path.dirname(__file__), "data", "megascans_surface_rmlkx2p0.json")


def make_asset(root, cat, folder, files, json_src=FIXTURE, json_name="rmlkx2p0.json"):
    d = root / cat / folder
    d.mkdir(parents=True)
    shutil.copy(json_src, d / json_name)
    for f in files:
        (d / f).write_bytes(b"x" * 10)
    return d


@pytest.fixture
def root(tmp_path):
    for c in megascans.MAIN_CATEGORIES:
        (tmp_path / c).mkdir()
    return tmp_path


def test_record_from_full_download(root):
    files = ["rmlkx2p_Grid_360_thumb.jpg", "rmlkx2p_Grid_720_thumb.jpg", "rmlkx2p_Grid_360.png",
             "rmlkx2p_Popup_1440_preview.jpg", "rmlkx2p_Popup_1920_preview.jpg",
             "rmlkx2p_Popup_3840_preview.jpg", "rmlkx2p_PopupClay_1920_preview.jpg",
             "rmlkx2p_4K_Albedo.exr", "rmlkx2p_256_Albedo.jpg"]
    d = make_asset(root, "surface", "Chickpeas_rmlkx2p0", files)
    rec = megascans.asset_record(str(d), "surface")

    assert rec["id"] == "rmlkx2p0"
    assert rec["name"] == "Chickpeas"
    assert rec["thumbnail"] == str(d / "rmlkx2p_Grid_360_thumb.jpg")   # smallest JPEG, not the PNG
    assert rec["path"] == str(d / "rmlkx2p_Popup_1920_preview.jpg")     # player: plain preview <= 1920
    assert rec["categories"][:3] == ["surface", "debris", "food"]       # folder first, no duplicate
    for t in ("brown", "archviz", "chickpeas", "raw", "edible", "rural"):
        assert t in rec["tags"]
    assert "" not in rec["tags"] and " raw" not in rec["tags"] and "other" not in rec["tags"]
    m = rec["metadata"]
    assert m["asset_dir"] == str(d) and m["megascans_type"] == "surface"
    assert m["tileable"] is True and m["scanArea"] == "0.29x0.29 m"
    assert "albedo" in m["maps"] and m["resolutions"][0] == "4096x4096"
    assert rec["description"] == ""                                     # biome / region are tags now
    assert "mediterranean-forest" in rec["tags"] and "Southeast Asia" in rec["tags"]
    assert all(k not in ("Biome", "Region") for k, _v in m["display"])


def test_thumbnail_fallbacks(root):
    # Bridge often downloads only one preview that isn't in the JSON list
    d = make_asset(root, "surface", "a", ["rmlkx2p_Preview.png", "rmlkx2p_1K_Albedo.jpg"])
    assert megascans.asset_record(str(d), "surface")["thumbnail"].endswith("rmlkx2p_Preview.png")
    # nothing preview-like: smallest albedo JPEG
    d = make_asset(root, "3d", "b", ["rmlkx2p_1K_Albedo.jpg", "rmlkx2p_256_Albedo.jpg"])
    rec = megascans.asset_record(str(d), "3d")
    assert rec["thumbnail"].endswith("rmlkx2p_256_Albedo.jpg")
    assert rec["path"] == rec["thumbnail"]
    # nothing at all: still listed (placeholder tile), never crashes
    d = make_asset(root, "atlas", "c", [])
    assert megascans.asset_record(str(d), "atlas")["thumbnail"] is None


def test_not_an_asset(root):
    (root / "3d" / "empty").mkdir()
    assert megascans.asset_record(str(root / "3d" / "empty"), "3d") is None


def test_scan_build_and_search(root, tmp_path):
    make_asset(root, "surface", "Chickpeas_rmlkx2p0", ["rmlkx2p_Grid_360_thumb.jpg"])
    data = json.load(open(FIXTURE))
    data.update(id="xyz123", name="Mossy Rock", tags=["green", "rock"], categories=["3d", "rock"],
                semanticTags={"contains": ["moss"]}, assetCategories={"3d": {"rock": {}}})
    alt = tmp_path / "alt.json"
    alt.write_text(json.dumps(data))
    make_asset(root, "3d", "Mossy_Rock_xyz123", ["x_Grid_360_thumb.jpg"], json_src=alt, json_name="xyz123.json")
    (root / "3dplant" / "junk_folder").mkdir()

    out = tmp_path / "lib" / "megascans.json"
    n, reused = megascans.build_library_json(str(out), root=str(root), threads=4)
    assert (n, reused) == (2, 0)

    lib = GalleryLibrary()
    lib.load(str(out))
    assert set(lib.search("cat:3d")) == {"xyz123"}
    assert set(lib.search("chickpeas")) == {"rmlkx2p0"}
    assert set(lib.search("tag:edible")) == {"rmlkx2p0"}
    assert set(lib.search("meta:tileable")) == {"rmlkx2p0", "xyz123"}
    assert lib.categories()["surface"] == 1

    # incremental: nothing changed -> everything reused
    n, reused = megascans.build_library_json(str(out), root=str(root), threads=4)
    assert (n, reused) == (2, 2)
    # touch one JSON -> only that one is re-read
    j = root / "3d" / "Mossy_Rock_xyz123" / "xyz123.json"
    os.utime(j, (os.path.getmtime(j) + 10,) * 2)
    n, reused = megascans.build_library_json(str(out), root=str(root), threads=4)
    assert (n, reused) == (2, 1)



def test_clean_display_drops_internal_and_raw_rows():
    rows = [["Scan Area", "2x2 m"], ["Points", 4], ["UseBillboardMaterial", False],
            ["Dimensions", [{"width": "1.01m", "length": "1.03m"}]],
            ["LodDistance", "[{'distance': [{'lod': 0}]}]"], ["Region", "Asia"], ["Tileable", True],
            ["Height", "0.028 m"]]
    assert megascans.clean_display(rows) == [["Scan Area", "2x2 m"], ["Tileable", "Yes"], ["Height", "0.028 m"]]


def test_old_caches_are_cleaned_on_load(tmp_path):
    from ilp_gallery.core.source import write_json
    cache = tmp_path / "ms.json"
    rec = {"id": "a", "name": "Ginger Lily", "path": str(tmp_path / "a.jpg"), "description": "tropical, Asia",
           "tags": ["lily"], "metadata": {"source": "megascans", "biome": "tropical", "region": "Asia",
                                          "display": [["Points", 4], ["Region", "Asia"], ["Scan Area", "1 m"]]}}
    write_json(str(cache), {"source": "megascans", "items": [rec]})
    out = megascans.MegascansSource(cache=str(cache)).load()[0]
    assert out["metadata"]["display"] == [["Scan Area", "1 m"]]
    assert out["description"] == "" and {"tropical", "Asia"} <= set(out["tags"])
