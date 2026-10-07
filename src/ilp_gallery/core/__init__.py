"""Qt-free data layer: items, data sources, library (middle layer) and search index."""

from .item import MEDIA_GIF, MEDIA_IMAGE, MEDIA_MOVIE, MEDIA_TYPES, GalleryItem, guess_media_type
from .index import FtsSearchIndex, PySearchIndex, SearchIndex, build_match, fts5_available
from .library import GalleryLibrary, scan_folder
from .source import (
    Changes, DataSource, FolderSource, JsonFileSource, RecordsSource, make_record, read_json,
    source_for_path, write_json,
)

__all__ = [
    "GalleryItem", "GalleryLibrary", "SearchIndex", "FtsSearchIndex", "PySearchIndex", "fts5_available", "build_match", "scan_folder",
    "DataSource", "Changes", "JsonFileSource", "FolderSource", "RecordsSource", "source_for_path",
    "make_record", "read_json", "write_json",
    "guess_media_type", "MEDIA_IMAGE", "MEDIA_GIF", "MEDIA_MOVIE", "MEDIA_TYPES",
]
