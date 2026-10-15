"""Display names: how an item is shown on tiles, in the drawer, in tooltips and
in the details panel.

A ``name_format`` function gets the :class:`GalleryItem` and returns
:class:`NameParts`: a title, an optional second line, an optional category chip
and optional detail rows. It can read anything the record carries, which is more
reliable than parsing names. The full name stays the identity (search, drawer
keys) and heads every tooltip; only what's painted changes.

    def clip(item):
        action = item.metadata.get("type_label")
        if not action:
            return None                                # show it as usual
        category = (item.categories or [""])[0]
        return NameParts(f"{action} · {' · '.join(item.tags)}", chip=acronym(category),
                         wrap=True, details=[("Category", category), ("Action", action)])

    GalleryWidget(name_format=clip)

Tiles show the chip in their corner; the drawer shows its colour as a border
around the row's thumbnail. Drawer entries missing from the library are passed
as a stand-in item that only has the name (see :func:`stub_item`).
"""

from __future__ import annotations

import zlib
import html
from dataclasses import dataclass
from typing import Callable, Sequence

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter


@dataclass(frozen=True)
class NameParts:
    title: str
    subtitle: str | None = None       # None: the tile keeps its usual second line
    chip: str = ""                    # short tag in front, e.g. "LCM"
    chip_color: str | None = None     # "#rrggbb"; default: a stable colour per chip text
    details: Sequence[tuple[str, str]] = ()   # (label, value) rows: tooltips, details panel
    wrap: bool = False                # a title too long for a tile carries on in line two


NameFormat = Callable[["GalleryItem"], "NameParts | tuple | str | None"]


def stub_item(name: str):
    """A stand-in item for a name with no library item (a missing drawer entry)."""
    from ..core.item import GalleryItem
    return GalleryItem(path=f"missing/{name}", id=name, name=name)


def format_name(fmt: NameFormat | None, item) -> NameParts:
    """``fmt(item)`` as NameParts; the plain name when there's no format, it returns
    None, or it fails (a display helper must never break painting)."""
    if fmt is None:
        return NameParts(item.name)
    try:
        out = fmt(item)
    except Exception:
        return NameParts(item.name)
    if out is None:
        return NameParts(item.name)
    if isinstance(out, NameParts):
        return out
    if isinstance(out, str):
        return NameParts(out)
    out = tuple(out)
    return NameParts(*out[:4]) if out else NameParts(item.name)


def split_title(title: str, fits: Callable[[str], bool]) -> tuple[str, str]:
    """Split ``title`` so the first part fits: at " · " separators, else at spaces.
    Returns (first line, rest); rest is "" when it all fits."""
    if fits(title):
        return title, ""
    for sep in (" · ", " "):
        words = title.split(sep)
        if len(words) < 2:
            continue
        for n in range(len(words) - 1, 0, -1):
            head = sep.join(words[:n])
            if fits(head):
                return head, sep.join(words[n:])
    return title, ""                  # one long word: elided on line one


def tooltip_html(name: str, parts: NameParts, extra: Sequence[str] = ()) -> str:
    """The full name in bold, then the detail rows, then ``extra`` lines."""
    esc = html.escape
    rows = "".join(f"<tr><td style='color:gray; padding-right:10px'>{esc(str(k))}</td>"
                   f"<td>{esc(str(v))}</td></tr>" for k, v in parts.details)
    out = f"<b>{esc(name)}</b>"
    if rows:
        out += f"<table cellspacing='0'>{rows}</table>"
    if extra:
        out += ("" if rows else "<br>") + "<br>".join(esc(x) for x in extra)
    return out


_VOWELS = set("aeiouAEIOU")


def acronym(word: str, length: int = 3) -> str:
    """A short upper-case tag: the first letter, then the next consonants
    ("locomotion" -> "LCM", "combat" -> "CMB", "idle" -> "IDL"); words with too
    few consonants use their first letters ("audio" -> "AUD")."""
    letters = [c for c in word if c.isalnum()]
    if not letters:
        return ""
    consonants = [c for c in letters[1:] if c not in _VOWELS]
    if len(consonants) >= length - 1:
        return (letters[0] + "".join(consonants[:length - 1])).upper()
    return "".join(letters[:length]).upper()          # e.g. "audio" -> "AUD"


def chip_color(parts: NameParts) -> QColor:
    """The chip's colour: its own, or a muted hue picked from the chip text, so the
    same category always gets the same colour."""
    if parts.chip_color:
        return QColor(parts.chip_color)
    hue = zlib.crc32(parts.chip.encode("utf-8")) % 360
    return QColor.fromHsl(hue, 110, 92)


def chip_size(parts: NameParts, font: QFont) -> tuple[QFont, QRect]:
    f = QFont(font)
    f.setPointSizeF(max(6.5, f.pointSizeF() * 0.72))
    f.setBold(True)
    fm = QFontMetrics(f)
    return f, QRect(0, 0, fm.horizontalAdvance(parts.chip) + 8, fm.height() + 1)


def paint_chip(p: QPainter, parts: NameParts, top_left: QPoint, font: QFont) -> int:
    """Paint the chip with its top-left corner at ``top_left``; returns its width."""
    f, r = chip_size(parts, font)
    r.moveTopLeft(top_left)
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(chip_color(parts))
    p.drawRoundedRect(r, 3, 3)
    p.setFont(f)
    p.setPen(QColor(250, 250, 250))
    p.drawText(r, Qt.AlignCenter, parts.chip)
    p.restore()
    return r.width()
