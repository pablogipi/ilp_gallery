"""SQLite FTS5 search index over gallery items.

The JSON stays the source of truth. The index is derived from it and can
be rebuilt at any time (in memory by default, ~10k items in well under a
second).

Query language (all case-insensitive, words are prefix-matched)::

    walk                    any field starts with "walk"  (walk, walking, walk_cycle)
    walk male               both words (AND is implicit)
    walk OR run             either word  ("|" works too)
    "walk cycle"            exact phrase
    -loop                   exclude items matching "loop"
    tag:hero                only in tags
    cat:crowd               only in categories   (also category:, c:)
    name:bob  desc:mocap  path:/jobs/abc  meta:24fps
    type:movie              image | gif | movie  (also is:)
    tag:"hero shot"         field + phrase
"""

from __future__ import annotations

import re
import sqlite3
import threading
from typing import Iterable

from .item import GalleryItem

FIELD_ALIASES = {
    "tag": "tags", "tags": "tags", "t": "tags",
    "cat": "categories", "category": "categories", "categories": "categories", "c": "categories",
    "name": "name", "n": "name",
    "desc": "description", "description": "description",
    "path": "path", "p": "path",
    "type": "type", "is": "type",
    "meta": "meta", "metadata": "meta",
}

# Column order matters for bm25() weights below.
COLUMNS = ("name", "tags", "categories", "description", "path", "type", "meta")
WEIGHTS = (10.0, 6.0, 4.0, 1.0, 0.5, 1.0, 0.5)

_TOKEN_RE = re.compile(r'(-)?(?:([A-Za-z]+):)?("([^"]*)"?|\S+)')
# Characters unicode61 treats as separators; used to split user words the same way.
_SPLIT_RE = re.compile(r"[^\w]+", re.UNICODE)


class QueryError(ValueError):
    pass


def _quote(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def _flatten_meta(meta: dict) -> str:
    parts = []
    for k, v in meta.items():
        if isinstance(v, dict):
            parts.append(f"{k} {_flatten_meta(v)}")
        elif isinstance(v, (list, tuple)):
            parts.append(f"{k} " + " ".join(map(str, v)))
        else:
            parts.append(f"{k} {v}")
    return " ".join(parts)


def build_match(query: str) -> tuple[str, str]:
    """Translate the user query into (positive, negative) FTS5 expressions.

    Either may be empty. Raises QueryError if nothing searchable remains.
    """
    groups: list[list[str]] = [[]]  # OR-groups of AND-ed terms
    negatives: list[str] = []
    for m in _TOKEN_RE.finditer(query):
        neg, field, raw, phrase = m.group(1), m.group(2), m.group(3), m.group(4)
        if not neg and not field and raw in ("OR", "|"):
            if groups[-1]:
                groups.append([])
            continue
        column = None
        if field:
            column = FIELD_ALIASES.get(field.lower())
            if column is None:  # "foo:bar" with unknown field -> treat as plain text
                raw = f"{field}:{raw}"
                phrase = None
        if phrase is not None:
            words = [w for w in _SPLIT_RE.split(phrase) if w]
            if not words:
                continue
            expr = _quote(" ".join(words))
            if not raw.endswith('"') or len(raw) == 1:  # unterminated quote while typing
                expr += "*"
        else:
            words = [w for w in _SPLIT_RE.split(raw) if w]
            if not words:
                continue
            # "crowd_walk" -> phrase "crowd walk"*, so it matches the same way it was indexed
            expr = _quote(" ".join(words)) + "*"
        if column:
            expr = f"{column} : {expr}"
        (negatives if neg else groups[-1]).append(expr)

    groups = [g for g in groups if g]
    positive = " OR ".join("(" + " AND ".join(g) + ")" for g in groups)
    negative = " OR ".join(negatives)
    if not positive and not negative:
        raise QueryError(f"Nothing to search for in {query!r}")
    return positive, negative


class SearchIndex:
    def __init__(self, path: str = ":memory:") -> None:
        self._lock = threading.RLock()  # loaders index on worker threads while the UI searches
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(
            f"""
            PRAGMA journal_mode = MEMORY;
            PRAGMA synchronous = OFF;
            CREATE TABLE IF NOT EXISTS ids (
                rowid   INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT UNIQUE NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
                {", ".join(COLUMNS)},
                tokenize = "unicode61 remove_diacritics 2"
            );
            """
        )

    def close(self) -> None:
        self._db.close()

    # -- writing -------------------------------------------------------
    def upsert(self, items: Iterable[GalleryItem]) -> None:
        with self._lock, self._db:  # one transaction per batch
            cur = self._db.cursor()
            for it in items:
                cur.execute("INSERT OR IGNORE INTO ids(item_id) VALUES (?)", (it.id,))
                (rowid,) = cur.execute("SELECT rowid FROM ids WHERE item_id = ?", (it.id,)).fetchone()
                cur.execute("DELETE FROM fts WHERE rowid = ?", (rowid,))
                cur.execute(
                    f"INSERT INTO fts(rowid, {', '.join(COLUMNS)}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        rowid,
                        it.name,
                        " ; ".join(it.tags),
                        " ; ".join(it.categories),
                        it.description,
                        it.path,
                        it.type,
                        _flatten_meta(it.metadata),
                    ),
                )

    def remove(self, item_ids: Iterable[str]) -> None:
        with self._lock, self._db:
            for iid in item_ids:
                row = self._db.execute("SELECT rowid FROM ids WHERE item_id = ?", (iid,)).fetchone()
                if row:
                    self._db.execute("DELETE FROM fts WHERE rowid = ?", row)
                    self._db.execute("DELETE FROM ids WHERE rowid = ?", row)

    def clear(self) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM fts")
            self._db.execute("DELETE FROM ids")

    def __len__(self) -> int:
        with self._lock:
            return self._db.execute("SELECT count(*) FROM ids").fetchone()[0]

    # -- reading -------------------------------------------------------
    def search(self, query: str, limit: int | None = None) -> list[str]:
        """Return item ids for ``query``, best first. Bad syntax -> []."""
        try:
            positive, negative = build_match(query)
        except QueryError:
            return []
        lim = f" LIMIT {int(limit)}" if limit else ""
        weights = ", ".join(str(w) for w in WEIGHTS)
        try:
            if positive:
                sql = (
                    "SELECT ids.item_id FROM fts JOIN ids ON ids.rowid = fts.rowid "
                    "WHERE fts MATCH ? "
                )
                args: list[str] = [positive]
                if negative:
                    sql += "AND fts.rowid NOT IN (SELECT rowid FROM fts WHERE fts MATCH ?) "
                    args.append(negative)
                sql += f"ORDER BY bm25(fts, {weights}), ids.rowid{lim}"
            else:  # only exclusions -> everything else, in load order
                sql = (
                    "SELECT item_id FROM ids WHERE rowid NOT IN "
                    f"(SELECT rowid FROM fts WHERE fts MATCH ?) ORDER BY rowid{lim}"
                )
                args = [negative]
            with self._lock:
                return [r[0] for r in self._db.execute(sql, args)]
        except sqlite3.OperationalError:
            return []
