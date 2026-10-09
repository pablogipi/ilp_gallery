"""Gallery item: the JSON record that describes one piece of media.

Qt-free on purpose, so the data layer can be used from farm scripts,
publish hooks or tests without a QApplication.

Example record::

    {
        "id": "crowd_walk_001",
        "name": "Crowd walk cycle 01",
        "path": "/jobs/lib/clips/crowd_walk_001.mov",
        "type": "movie",
        "categories": ["crowd", "locomotion"],
        "tags": ["walk", "male", "loop"],
        "description": "Neutral walk, 24 fps, loopable",
        "thumbnail": null,
        "metadata": {"fps": 24, "frames": 48, "source": "mocap"}
    }

Only ``path`` is required. ``id`` defaults to the path, ``name`` to the
file stem and ``type`` is guessed from the extension.
"""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass, field
from typing import Any

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".bmp", ".webp", ".tga"}
GIF_EXTS = {".gif"}
MOVIE_EXTS = {".mov", ".mp4", ".m4v", ".mkv", ".avi", ".webm", ".mxf"}

MEDIA_IMAGE = "image"
MEDIA_GIF = "gif"
MEDIA_MOVIE = "movie"
MEDIA_TYPES = (MEDIA_IMAGE, MEDIA_GIF, MEDIA_MOVIE)

_KNOWN_KEYS = {
    "id", "name", "path", "type", "categories", "tags",
    "description", "thumbnail", "metadata",
}


def guess_media_type(path: str) -> str | None:
    ext = os.path.splitext(path)[1].lower()
    if ext in GIF_EXTS:
        return MEDIA_GIF
    if ext in MOVIE_EXTS:
        return MEDIA_MOVIE
    if ext in IMAGE_EXTS:
        return MEDIA_IMAGE
    return None


def _as_str_list(value: Any) -> list[str]:
    """Accept "a, b", ["a", "b"] or None; return clean, de-duplicated list."""
    if value is None:
        return []
    if isinstance(value, str):
        value = value.split(",")
    out: list[str] = []
    seen: set[str] = set()
    for v in value:
        s = str(v).strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


@dataclass
class GalleryItem:
    path: str
    id: str = ""
    name: str = ""
    type: str = ""
    categories: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    description: str = ""
    thumbnail: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # Unknown keys from the JSON are kept so round-tripping never loses data.
    extra: dict[str, Any] = field(default_factory=dict, repr=False)
    # Name of the DataSource this item came from (set by the library, not saved).
    source: str = field(default="", repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("GalleryItem requires a 'path'")
        self.path = os.path.expandvars(os.path.expanduser(self.path))
        if not self.id:
            self.id = self.path
        if not self.name:
            self.name = os.path.splitext(os.path.basename(self.path))[0]
        if not self.type:
            self.type = guess_media_type(self.path) or MEDIA_IMAGE
        if self.type not in MEDIA_TYPES:
            raise ValueError(f"Unknown media type {self.type!r} for {self.path}")
        self.categories = _as_str_list(self.categories)
        self.tags = _as_str_list(self.tags)

    # -- convenience ---------------------------------------------------
    @property
    def is_animated(self) -> bool:
        return self.type in (MEDIA_GIF, MEDIA_MOVIE)

    # Reserved metadata keys the UI understands (see core.source)
    @property
    def type_label(self) -> str:
        return self.metadata.get("type_label") or {"image": "Image", "gif": "GIF", "movie": "Movie"}[self.type]

    @property
    def category_paths(self) -> list[list[str]]:
        return self.metadata.get("category_paths") or []

    @property
    def display(self) -> list[list[Any]]:
        return self.metadata.get("display") or []

    @property
    def preview(self) -> str:
        """Preview media for the item (``metadata["preview"]``), e.g. a small mp4 next
        to a published asset. Empty if none."""
        p = self.metadata.get("preview") or ""
        return os.path.expandvars(os.path.expanduser(str(p))) if p else ""

    @property
    def motion(self) -> tuple[str, str] | None:
        """(path, media type) of what plays and scrubs for this item: the item itself
        if it's a movie or GIF, otherwise its preview movie / GIF. None if neither."""
        if self.is_animated:
            return self.path, self.type
        p = self.preview
        if p:
            t = guess_media_type(p)
            if t in (MEDIA_MOVIE, MEDIA_GIF):
                return p, t
        return None

    def motion_item(self) -> "GalleryItem":
        """The item as it should be played: same item, with its path and type swapped
        for the preview movie when the item itself doesn't move."""
        m = self.motion
        if m is None or m[0] == self.path:
            return self
        return dataclasses.replace(self, path=m[0], type=m[1])

    def value(self, key: str) -> Any:
        """A field or metadata value by name: "path", "id", "json", "asset_dir", ..."""
        if key in ("item", ""):
            return self
        if key in _KNOWN_KEYS and key != "metadata":
            return getattr(self, key)
        if key in self.metadata:
            return self.metadata[key]
        if key in self.extra:
            return self.extra[key]
        raise KeyError(f"{self.name}: no field or metadata key {key!r}")

    @property
    def exists(self) -> bool:
        return os.path.exists(self.path)

    # -- (de)serialisation ---------------------------------------------
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GalleryItem":
        if "path" not in data:
            raise ValueError(f"Gallery record has no 'path': {data!r}")
        return cls(
            path=data["path"],
            id=str(data.get("id") or ""),
            name=str(data.get("name") or ""),
            type=str(data.get("type") or ""),
            categories=data.get("categories") or data.get("category"),
            tags=data.get("tags"),
            description=str(data.get("description") or ""),
            thumbnail=data.get("thumbnail"),
            metadata=dict(data.get("metadata") or {}),
            extra={k: v for k, v in data.items() if k not in _KNOWN_KEYS and k != "category"},
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = dict(self.extra)
        d.update(
            id=self.id,
            name=self.name,
            path=self.path,
            type=self.type,
            categories=list(self.categories),
            tags=list(self.tags),
            description=self.description,
            thumbnail=self.thumbnail,
            metadata=dict(self.metadata),
        )
        return d
