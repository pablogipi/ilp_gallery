"""Search index over gallery items.

The JSON stays the source of truth. The index is derived from it and can
be rebuilt at any time (in memory by default, ~10k items in well under a
second).

Two backends with the same query language and results:

* ``FtsSearchIndex``: SQLite FTS5 (bm25 ranking). Used when the Python's
  sqlite3 is built with FTS5.
* ``PySearchIndex``: pure Python inverted index. Used when FTS5 is missing
  (e.g. Nuke's bundled Python: "no such module: fts5").

``SearchIndex()`` picks one. Force a backend with the environment variable
``ILP_GALLERY_SEARCH=fts5`` or ``ILP_GALLERY_SEARCH=python``.

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

import bisect
import logging
import math
import os
import re
import sqlite3
import threading
import unicodedata
from typing import Iterable, NamedTuple

from .item import GalleryItem

log = logging.getLogger(__name__)

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


class Term(NamedTuple):
    """One search term: consecutive ``words`` (a phrase), optionally restricted
    to ``column``; ``prefix`` means the last word is prefix-matched."""
    column: str | None
    words: tuple[str, ...]
    prefix: bool


def parse_query(query: str) -> tuple[list[list[Term]], list[Term]]:
    """Parse the user query into (OR-groups of AND-ed terms, excluded terms).

    Raises QueryError if nothing searchable remains.
    """
    groups: list[list[Term]] = [[]]
    negatives: list[Term] = []
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
            prefix = not raw.endswith('"') or len(raw) == 1  # unterminated quote while typing
        else:
            words = [w for w in _SPLIT_RE.split(raw) if w]
            if not words:
                continue
            # "crowd_walk" -> phrase "crowd walk"*, so it matches the same way it was indexed
            prefix = True
        (negatives if neg else groups[-1]).append(Term(column, tuple(words), prefix))

    groups = [g for g in groups if g]
    if not groups and not negatives:
        raise QueryError(f"Nothing to search for in {query!r}")
    return groups, negatives


def _fts_term(t: Term) -> str:
    expr = _quote(" ".join(t.words)) + ("*" if t.prefix else "")
    return f"{t.column} : {expr}" if t.column else expr


def build_match(query: str) -> tuple[str, str]:
    """Translate the user query into (positive, negative) FTS5 expressions.

    Either may be empty. Raises QueryError if nothing searchable remains.
    """
    groups, negatives = parse_query(query)
    positive = " OR ".join("(" + " AND ".join(map(_fts_term, g)) + ")" for g in groups)
    negative = " OR ".join(map(_fts_term, negatives))
    return positive, negative


def _row_values(it: GalleryItem) -> tuple[str, ...]:
    """Column values for one item, in COLUMNS order (shared by both backends)."""
    return (
        it.name,
        " ; ".join(it.tags),
        " ; ".join(it.categories),
        it.description,
        it.path,
        it.type,
        _flatten_meta(it.metadata),
    )


class FtsSearchIndex:
    """SQLite FTS5 backend."""

    backend = "fts5"

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
                    (rowid, *_row_values(it)),
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


# -- pure-Python backend --------------------------------------------------

# unicode61 treats everything except letters and digits as a separator,
# including "_", so split on that rather than on \W.
_PY_TOKEN_SPLIT = re.compile(r"[\W_]+", re.UNICODE)


def _fold(text: str) -> str:
    """Lower-case and strip diacritics, like unicode61 remove_diacritics."""
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _tokenize(text: str) -> tuple[str, ...]:
    return tuple(t for t in _PY_TOKEN_SPLIT.split(_fold(text)) if t)


_COL_INDEX = {c: i for i, c in enumerate(COLUMNS)}


class PySearchIndex:
    """Pure-Python backend: same query language and results as FTS5.

    An inverted index (token -> rows) narrows the candidates; phrases and
    column restrictions are then checked against each candidate's tokens.
    Ranking is FTS5's bm25 formula with the same column weights, so both
    backends order results alike; ties in load order.
    """

    backend = "python"

    def __init__(self, path: str = ":memory:") -> None:
        if path != ":memory:":
            log.info("ilp_gallery: pure-Python search index is in memory only; ignoring %s", path)
        self._lock = threading.RLock()
        self._next_rowid = 1
        self._rowids: dict[str, int] = {}             # item_id -> rowid (stable, like AUTOINCREMENT)
        self._ids: dict[int, str] = {}                # rowid -> item_id
        self._cols: dict[int, tuple[tuple[str, ...], ...]] = {}  # rowid -> tokens per column
        self._postings: dict[str, set[int]] = {}      # token -> rowids containing it (any column)
        self._doclen: dict[int, int] = {}             # rowid -> tokens in all columns (bm25)
        self._total_len = 0
        self._vocab: list[str] = []                   # sorted tokens, for prefix lookups
        self._vocab_dirty = False

    def close(self) -> None:
        self.clear()

    # -- writing -------------------------------------------------------
    def _drop(self, rowid: int) -> None:
        self._total_len -= self._doclen.pop(rowid, 0)
        for col in self._cols.pop(rowid, ()):
            for tok in set(col):
                rows = self._postings.get(tok)
                if rows is not None:
                    rows.discard(rowid)
                    if not rows:
                        del self._postings[tok]
                        self._vocab_dirty = True

    def upsert(self, items: Iterable[GalleryItem]) -> None:
        with self._lock:
            for it in items:
                rowid = self._rowids.get(it.id)
                if rowid is None:
                    rowid = self._next_rowid
                    self._next_rowid += 1
                    self._rowids[it.id] = rowid
                    self._ids[rowid] = it.id
                else:
                    self._drop(rowid)
                cols = tuple(_tokenize(v or "") for v in _row_values(it))
                self._cols[rowid] = cols
                self._doclen[rowid] = n = sum(map(len, cols))
                self._total_len += n
                for col in cols:
                    for tok in col:
                        rows = self._postings.get(tok)
                        if rows is None:
                            self._postings[tok] = rows = set()
                            self._vocab_dirty = True
                        rows.add(rowid)

    def remove(self, item_ids: Iterable[str]) -> None:
        with self._lock:
            for iid in item_ids:
                rowid = self._rowids.pop(iid, None)
                if rowid is not None:
                    self._drop(rowid)
                    del self._ids[rowid]

    def clear(self) -> None:
        with self._lock:
            self._rowids.clear()
            self._ids.clear()
            self._cols.clear()
            self._postings.clear()
            self._doclen.clear()
            self._total_len = 0
            self._vocab = []
            self._vocab_dirty = False

    def __len__(self) -> int:
        with self._lock:
            return len(self._rowids)

    # -- reading -------------------------------------------------------
    def _rows_with(self, word: str, prefix: bool) -> set[int]:
        if not prefix:
            return set(self._postings.get(word, ()))
        if self._vocab_dirty:
            self._vocab = sorted(self._postings)
            self._vocab_dirty = False
        out: set[int] = set()
        i = bisect.bisect_left(self._vocab, word)
        while i < len(self._vocab) and self._vocab[i].startswith(word):
            out |= self._postings[self._vocab[i]]
            i += 1
        return out

    def _term_query(self, t: Term) -> tuple[tuple[str, ...], set[int]]:
        """Folded words and the candidate rows that contain all of them."""
        words = tuple(w for w in _tokenize(" ".join(t.words)))
        if not words:
            return words, set()
        last = len(words) - 1
        cands: set[int] | None = None
        for i, w in enumerate(words):
            rows = self._rows_with(w, t.prefix and i == last)
            cands = rows if cands is None else cands & rows
            if not cands:
                break
        return words, cands or set()

    @staticmethod
    def _count(tokens: tuple[str, ...], words: tuple[str, ...], prefix: bool) -> int:
        """Occurrences of the phrase ``words`` in ``tokens``."""
        n, last, hits = len(words), len(words) - 1, 0
        for start in range(len(tokens) - n + 1):
            for j, w in enumerate(words):
                tok = tokens[start + j]
                if not (tok.startswith(w) if prefix and j == last else tok == w):
                    break
            else:
                hits += 1
        return hits

    def _matches(self, t: Term) -> dict[int, float]:
        """rowid -> bm25 score for every row matching term ``t``.

        Same formula as FTS5's bm25() (k1=1.2, b=0.75): the weighted hit count
        over the columns, normalised by the row's total length and scaled by
        how rare the term is. Higher is better here (FTS5 negates it).
        """
        words, cands = self._term_query(t)
        cols = [_COL_INDEX[t.column]] if t.column else range(len(COLUMNS))
        tfs: dict[int, float] = {}
        for rowid in cands:
            row = self._cols[rowid]
            tf = 0.0
            for c in cols:
                hits = self._count(row[c], words, t.prefix)
                if hits:
                    tf += WEIGHTS[c] * hits
            if tf:
                tfs[rowid] = tf
        if not tfs:
            return {}
        n_rows = len(self._ids)
        idf = math.log((n_rows - len(tfs) + 0.5) / (len(tfs) + 0.5))
        idf = max(idf, 1e-6)  # FTS5 clamps non-positive IDF the same way
        avgdl = (self._total_len / n_rows) or 1.0
        k1, b = 1.2, 0.75
        return {
            r: idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * self._doclen[r] / avgdl))
            for r, tf in tfs.items()
        }

    def search(self, query: str, limit: int | None = None) -> list[str]:
        """Return item ids for ``query``, best first. Bad syntax -> []."""
        try:
            groups, negatives = parse_query(query)
        except QueryError:
            return []
        with self._lock:
            excluded: set[int] = set()
            for t in negatives:
                excluded |= self._matches(t).keys()
            if not groups:  # only exclusions -> everything else, in load order
                rows = sorted(r for r in self._ids if r not in excluded)
            else:
                scores: dict[int, float] = {}
                for group in groups:
                    group_scores: dict[int, float] | None = None
                    for t in group:
                        m = self._matches(t)
                        if group_scores is None:
                            group_scores = m
                        else:
                            group_scores = {r: s + m[r] for r, s in group_scores.items() if r in m}
                        if not group_scores:
                            break
                    for r, s in (group_scores or {}).items():
                        if r not in excluded:
                            scores[r] = scores.get(r, 0.0) + s
                rows = sorted(scores, key=lambda r: (-scores[r], r))
            if limit:
                rows = rows[: int(limit)]
            return [self._ids[r] for r in rows]


# -- backend selection ------------------------------------------------------

_fts5_ok: bool | None = None


def fts5_available() -> bool:
    """True if this Python's sqlite3 has the FTS5 module (cached)."""
    global _fts5_ok
    if _fts5_ok is None:
        try:
            db = sqlite3.connect(":memory:")
            try:
                db.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
                _fts5_ok = True
            finally:
                db.close()
        except sqlite3.Error:
            _fts5_ok = False
    return _fts5_ok


def SearchIndex(path: str = ":memory:") -> FtsSearchIndex | PySearchIndex:  # noqa: N802 (kept as the public name)
    """Create the best available search index.

    FTS5 when this Python's sqlite3 supports it, the pure-Python index
    otherwise. ``ILP_GALLERY_SEARCH=fts5|python`` forces one.
    """
    forced = os.environ.get("ILP_GALLERY_SEARCH", "").strip().lower()
    if forced == "python":
        return PySearchIndex(path)
    if forced in ("fts5", "fts", "sqlite"):
        return FtsSearchIndex(path)
    if fts5_available():
        return FtsSearchIndex(path)
    log.info("ilp_gallery: sqlite3 has no FTS5 (sqlite %s); using the pure-Python search index",
             sqlite3.sqlite_version)
    return PySearchIndex(path)
