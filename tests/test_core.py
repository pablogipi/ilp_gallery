import json
import time

import pytest

from ilp_gallery.core import GalleryItem, GalleryLibrary, build_match
from ilp_gallery.core.index import QueryError

RECORDS = [
    {"id": "a", "name": "Crowd walk cycle 01", "path": "/lib/crowd_walk_001.mov",
     "categories": ["crowd", "locomotion"], "tags": ["walk", "male", "loop"],
     "description": "Neutral walk from mocap", "metadata": {"fps": 24}},
    {"id": "b", "name": "Crowd run", "path": "/lib/crowd_run_001.mov",
     "categories": ["crowd", "locomotion"], "tags": ["run", "female"]},
    {"id": "c", "name": "Hero explosion", "path": "/lib/fx/explosion.gif",
     "categories": ["fx"], "tags": ["hero shot", "fire"]},
    {"id": "d", "name": "Forest plate", "path": "/lib/plates/forest.exr",
     "categories": ["plates", "environment"], "tags": ["forest", "day"],
     "description": "Walkable forest path"},
    {"id": "e", "name": "Café sign", "path": "/lib/ref/cafe_sign.jpg",
     "categories": ["reference"], "tags": ["signage"]},
]


@pytest.fixture
def lib():
    lib = GalleryLibrary()
    lib.add_records(RECORDS)
    return lib


def test_item_defaults():
    it = GalleryItem.from_dict({"path": "/x/y/shot_010_v003.mov", "tags": "a, b, a", "foo": 1})
    assert it.id == "/x/y/shot_010_v003.mov"
    assert it.name == "shot_010_v003"
    assert it.type == "movie"
    assert it.tags == ["a", "b"]
    assert it.to_dict()["foo"] == 1  # unknown keys round-trip


def test_item_requires_path():
    with pytest.raises(ValueError):
        GalleryItem.from_dict({"name": "no path"})


@pytest.mark.parametrize("query, expected", [
    ("walk", {"a", "d"}),           # name/tag + description ("Walkable")
    ("wal", {"a", "d"}),            # prefix
    ("crowd", {"a", "b"}),
    ("crowd walk", {"a"}),          # AND
    ("walk OR run", {"a", "b", "d"}),
    ("walk | explosion", {"a", "c", "d"}),
    ("tag:walk", {"a"}),            # field restricted: not d
    ("cat:crowd -tag:loop", {"b"}),
    ("-crowd", {"c", "d", "e"}),    # negation only
    ("type:gif", {"c"}),
    ("is:movie female", {"b"}),
    ('tag:"hero shot"', {"c"}),
    ('"hero sh', {"c"}),            # unterminated quote while typing
    ("cafe", {"e"}),                # diacritics folded
    ("crowd_walk", {"a"}),          # underscores behave like the index
    ("meta:24", {"a"}),
    ("path:plates", {"d"}),
    ("fps:24", {"a"}),              # unknown field -> plain text, matches metadata "fps 24"
])
def test_search(lib, query, expected):
    assert set(lib.search(query)) == expected


def test_ranking_prefers_name(lib):
    # "walk" is in a's name and tags, only in d's description
    assert lib.search("walk")[0] == "a"


def test_empty_query_returns_all_in_order(lib):
    assert lib.search("") == ["a", "b", "c", "d", "e"]


def test_bad_queries_do_not_raise(lib):
    for q in ['"', "-", "tag:", "OR", "((", "*", "NOT"]:
        lib.search(q)  # no exception


def test_build_match_empty():
    with pytest.raises(QueryError):
        build_match("  ")


def test_update_and_remove(lib):
    it = lib.get("e")
    it.tags.append("neon")
    lib.update(it)
    assert lib.search("neon") == ["e"]
    lib.remove(["e"])
    assert lib.search("neon") == []
    assert "e" not in lib and len(lib) == 4


def test_listener(lib):
    calls = []
    lib.add_listener(lambda: calls.append(1))
    lib.add_records([{"path": "/lib/new.png"}])
    assert calls


def test_facets(lib):
    assert lib.categories()["crowd"] == 2
    assert lib.tags()["hero shot"] == 1


def test_load_save_roundtrip(tmp_path, lib):
    p = tmp_path / "lib.json"
    lib.save(str(p))
    lib2 = GalleryLibrary()
    assert lib2.load(str(p)) == 5
    assert [i.to_dict() for i in lib2] == [i.to_dict() for i in lib]


def test_load_folder_and_relative_paths(tmp_path):
    (tmp_path / "one.json").write_text(json.dumps([{"path": "media/a.png", "tags": ["x"]}]))
    (tmp_path / "two.json").write_text(json.dumps({"items": [{"path": "/abs/b.mov"}]}))
    (tmp_path / "broken.json").write_text("{not json")
    lib = GalleryLibrary()
    assert lib.load(str(tmp_path)) == 2
    assert lib.get(str(tmp_path / "media" / "a.png")) is not None


def test_same_id_later_source_wins(lib):
    lib.add_records([{"id": "a", "path": "/lib/other.mov", "name": "Replaced"}])
    assert len(lib) == 5
    assert lib.get("a").name == "Replaced"
    assert lib.search("replaced") == ["a"]
    assert lib.search("neutral") == []


def test_search_speed():
    lib = GalleryLibrary()
    recs = [{"id": str(i), "path": f"/lib/asset_{i:05d}.mov", "name": f"Asset {i}",
             "categories": [f"cat{i % 20}"], "tags": [f"tag{i % 97}", "common"],
             "description": "lorem ipsum dolor " * 3} for i in range(20000)]
    t = time.perf_counter()
    lib.add_records(recs)
    build = time.perf_counter() - t
    t = time.perf_counter()
    for q in ("tag:tag5", "cat:cat3 common", "asset 12", "-common", "lorem"):
        lib.search(q)
    per_query = (time.perf_counter() - t) / 5
    print(f"\n20k items: index {build * 1000:.0f} ms, {per_query * 1000:.1f} ms/query")
    assert build < 5 and per_query < 0.25
