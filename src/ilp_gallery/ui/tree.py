"""Category tree: All + hierarchical categories with live counts.

Hierarchy comes from ``metadata["category_paths"]`` (e.g. the Megascans
``assetCategories``: Surfaces > Rock > Cliff). Items without it use their
flat ``categories``, one node each.
"""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, Qt, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTreeView

from ..core.library import GalleryLibrary
from . import theme

PathRole = Qt.UserRole + 10
Path = tuple[str, ...]

# Known top-level names and order (Megascans); anything else is title-cased, alphabetically after.
TOP_LEVEL = [
    ("3d", "3D Assets"), ("3d asset", "3D Assets"), ("3dplant", "3D Plants"), ("3d plant", "3D Plants"),
    ("plant", "3D Plants"), ("surface", "Surfaces"), ("decal", "Decals"), ("atlas", "Atlases"),
    ("imperfection", "Imperfections"), ("displacement", "Displacements"), ("brush", "Brushes"),
]
_TOP_NAMES = dict(TOP_LEVEL)
_TOP_ORDER = {k: i for i, (k, _) in enumerate(TOP_LEVEL)}


def display_name(segment: str, depth: int) -> str:
    if depth == 0 and segment.lower() in _TOP_NAMES:
        return _TOP_NAMES[segment.lower()]
    return segment[:1].upper() + segment[1:] if segment.islower() else segment


# Different spellings of the same top level (folder name vs assetCategories key)
ALIASES = {"3d asset": "3d", "3d assets": "3d", "3d plant": "3dplant", "3d plants": "3dplant",
           "plant": "3dplant", "plants": "3dplant", "surfaces": "surface", "decals": "decal",
           "atlases": "atlas", "imperfections": "imperfection", "displacements": "displacement",
           "brushes": "brush"}


def _canon(path) -> Path:
    segs = [str(s).strip().lower() for s in path if str(s).strip()]
    if segs:
        segs[0] = ALIASES.get(segs[0], segs[0])
    return tuple(segs)


def item_paths(item) -> list[Path]:
    m = item.metadata
    paths = m.get("category_paths")
    if paths:
        out = [_canon(p) for p in paths if p]
    elif m.get("source") == "megascans":
        # Library scanned by an older importer: only the folder is reliable.
        out = [_canon([m.get("megascans_type") or (item.categories or ["other"])[0]])]
    else:
        out = [(c.lower(),) for c in item.categories]
    return list(dict.fromkeys(p for p in out if p))


class CategoryTree(QTreeView):
    pathSelected = Signal(tuple)  # () == all

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._model = QStandardItemModel(self)
        self._model.setColumnCount(2)
        self.setModel(self._model)
        self.setHeaderHidden(True)
        self.setUniformRowHeights(True)
        self.setIndentation(14)
        self.setAnimated(False)  # animated trees ignore clicks while animating
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self._setup_header()
        self.setMinimumWidth(250)
        self.ids: dict[Path, set[str]] = {}
        self._rows: dict[Path, tuple[QStandardItem, QStandardItem]] = {}
        self._theme = theme.ILP
        self._visible: set[str] | None = None
        self._total = 0
        self.setExpandsOnDoubleClick(False)
        self.selectionModel().currentChanged.connect(self._on_current)
        self.pressed.connect(self._remember_state)
        self.clicked.connect(self._on_clicked)
        self._press_state: tuple[QModelIndex, bool, bool] | None = None
        self._special: dict[Path, tuple[str, set[str]]] = {}     # set_special()

    def _setup_header(self) -> None:
        h = self.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)

    # -- building ------------------------------------------------------
    def build(self, library: GalleryLibrary) -> None:
        current = self.current_path()
        ids: dict[Path, set[str]] = {}
        for it in library:
            for path in item_paths(it):
                for depth in range(1, len(path) + 1):
                    ids.setdefault(path[:depth], set()).add(it.id)
        for path, (_label, members) in self._special.items():
            ids[path] = set(members)
        self.ids = ids
        self._total = len(library)

        self._model.clear()
        self._model.setColumnCount(2)
        self._setup_header()  # clear() resets the header's resize modes
        self._rows = {}
        root = self._model.invisibleRootItem()
        self._add_row(root, (), "All assets", bold=True)
        for path, (label, _members) in self._special.items():     # pinned under "All assets"
            self._add_row(root, path, label, italic=True)

        def sort_key(p: Path):
            return (_TOP_ORDER.get(p[-1], 999) if len(p) == 1 else 0, display_name(p[-1], len(p) - 1).lower())

        for path in sorted((p for p in ids if p not in self._special), key=lambda p: (len(p), sort_key(p))):
            parent = root if len(path) == 1 else self._rows[path[:-1]][0]
            self._add_row(parent, path, display_name(path[-1], len(path) - 1))
        self.update_counts(None)
        self.select_path(current if current in self._rows else ())

    def _add_row(self, parent: QStandardItem, path: Path, label: str, bold: bool = False,
                 italic: bool = False) -> None:
        name = QStandardItem(label)
        name.setData(path, PathRole)
        count = QStandardItem("")
        count.setData(path, PathRole)
        count.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        count.setForeground(self._theme.c("muted"))
        if bold or italic:
            f = name.font()
            f.setBold(bold)
            f.setItalic(italic)
            name.setFont(f)
        parent.appendRow([name, count])
        self._rows[path] = (name, count)

    # -- pinned rows ---------------------------------------------------
    def set_special(self, path: Path, label: str, members) -> None:
        """A pinned row under "All assets" with its own members (e.g. the items in a
        drawer). Selecting it filters to them like a category; calling again updates
        the members (and the label) without rebuilding the tree."""
        path = tuple(path)
        members = set(members)
        is_new = path not in self._special
        self._special[path] = (label, members)
        self.ids[path] = members
        if path in self._rows:
            self._rows[path][0].setText(label)
        elif is_new:
            root = self._model.invisibleRootItem()
            name = QStandardItem(label)                     # insert below "All assets"
            name.setData(path, PathRole)
            f = name.font()
            f.setItalic(True)
            name.setFont(f)
            count = QStandardItem("")
            count.setData(path, PathRole)
            count.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            count.setForeground(self._theme.c("muted"))
            root.insertRow(len(self._special), [name, count])
            self._rows[path] = (name, count)
        self.update_counts(self._visible)

    def remove_special(self, path: Path) -> None:
        path = tuple(path)
        if self._special.pop(path, None) is None:
            return
        self.ids.pop(path, None)
        row = self._rows.pop(path, None)
        if row is not None:
            if self.current_path() == path:
                self.select_path(())
            self._model.invisibleRootItem().removeRow(row[0].row())

    # -- counts / selection --------------------------------------------
    def update_counts(self, visible: set[str] | None) -> None:
        """``visible`` = ids matching the search (None = no search: full counts)."""
        self._visible = visible
        muted, text = self._theme.c("muted"), self._theme.c("text")
        for path, (name, count) in self._rows.items():
            members = self.ids.get(path) if path else None
            if path == ():
                n = self._total if visible is None else len(visible)
            elif visible is None:
                n = len(members)
            else:
                n = len(members & visible)
            count.setText(f"{n:,}")
            name.setForeground(text if n or visible is None else muted)

    def set_theme(self, t: theme.Theme) -> None:
        self._theme = t
        muted = t.c("muted")
        for _name, count in self._rows.values():
            count.setForeground(muted)
        self.update_counts(self._visible)

    def current_path(self) -> Path:
        idx = self.currentIndex()
        return tuple(idx.data(PathRole) or ()) if idx.isValid() else ()

    def select_path(self, path: Path) -> None:
        path = tuple(path)
        row = self._rows.get(path)
        if row is None:
            return
        idx = self._model.indexFromItem(row[0])
        p = idx.parent()
        while p.isValid():
            self.expand(p)
            p = p.parent()
        self.setCurrentIndex(idx)
        self.scrollTo(idx)

    # One click selects the category and opens its next level; clicking the
    # already-selected, open category closes it again.
    def _remember_state(self, index: QModelIndex) -> None:
        name_idx = index.siblingAtColumn(0)
        was_current = name_idx == self.currentIndex().siblingAtColumn(0)
        self._press_state = (name_idx, was_current, self.isExpanded(name_idx))

    def mouseDoubleClickEvent(self, event) -> None:
        # A quick second click arrives as a double-click, which QTreeView swallows.
        # Treat it as a normal click so "click again to close" always works.
        self.mousePressEvent(event)

    def _on_clicked(self, index: QModelIndex) -> None:
        name_idx = index.siblingAtColumn(0)
        if not self._model.hasChildren(name_idx):
            return
        state, self._press_state = self._press_state, None
        if state is None or state[0] != name_idx:
            return
        _, was_current, was_open = state
        self.setExpanded(name_idx, not (was_current and was_open))

    def _on_current(self, cur: QModelIndex, _prev: QModelIndex) -> None:
        self.pathSelected.emit(tuple(cur.data(PathRole) or ()))
