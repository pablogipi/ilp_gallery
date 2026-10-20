"""Drawer: a list of chosen items next to the gallery (batch work, or a host node's clip list).

Each entry is identified by one record field, ``key`` (default ``"name"``), not by
its path: a newly published version gets a new path but keeps its name. An entry
also stores the path it was added with and an enabled flag. Against the library
an entry is:

* ``ok``        the key is in the library and the stored path matches
* ``outdated``  the key is in the library with a different path (newer version)
* ``missing``   the key is not in the library (kept, shown greyed out)

What an entry stores is configurable: ``key`` names the identity field and
``path_key`` the field kept as its path (``"path"``, or e.g. ``"json"`` for a
Megascans batch). For full control set ``DrawerWidget.entry_factory`` to a
function ``GalleryItem -> DrawerEntry | None``.

Rows can be dragged back onto the gallery to remove them.

The drawer emits ``entriesChanged`` only for edits made in its UI (add, drop,
remove, reorder, toggle, update), never for :meth:`DrawerWidget.set_entries`, so
a host can sync both ways without loops::

    gallery = GalleryWidget(drawer=True, drawer_key="name")
    gallery.drawerChanged.connect(lambda entries: write_to_node(entries))
    gallery.set_drawer(read_from_node())       # silent
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from typing import Callable, Iterable

from PySide6.QtCore import QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu,
    QPushButton, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QVBoxLayout, QWidget,
)

from ..core.item import GalleryItem
from .actions import MIME_ITEMS
from .names import NameFormat, chip_color, format_name, stub_item, tooltip_html

STATE_OK, STATE_OUTDATED, STATE_MISSING = "ok", "outdated", "missing"
OUTDATED_COLOR = QColor("#e0a030")

EntryRole = Qt.UserRole + 20
MIME_DRAWER = "application/x-ilp-drawer-keys"     # rows dragged out of the drawer


@dataclass(frozen=True)
class DrawerEntry:
    key: str            # identity: the value of the drawer's key field (e.g. the clip name)
    path: str = ""      # the path stored with the entry; may lag behind the library
    enabled: bool = True


def as_entry(value) -> DrawerEntry:
    """DrawerEntry from an entry, a dict, a (key, path[, enabled]) tuple or a bare key."""
    if isinstance(value, DrawerEntry):
        return value
    if isinstance(value, dict):
        return DrawerEntry(str(value["key"]), str(value.get("path", "")), bool(value.get("enabled", True)))
    if isinstance(value, (tuple, list)):
        return DrawerEntry(str(value[0]), str(value[1]) if len(value) > 1 else "",
                           bool(value[2]) if len(value) > 2 else True)
    return DrawerEntry(str(value))


def _norm(path: str) -> str:
    return os.path.normpath(os.path.expandvars(path)) if path else ""


def library_path(item: GalleryItem, path_key: str = "path") -> str:
    """The value an entry's path is compared with ("" if the item lacks the field)."""
    try:
        value = item.value(path_key)
    except KeyError:
        return ""
    return "" if value is None else str(value)


def entry_state(entry: DrawerEntry, item: GalleryItem | None, path_key: str = "path") -> str:
    if item is None:
        return STATE_MISSING
    return STATE_OK if _norm(entry.path) == _norm(library_path(item, path_key)) else STATE_OUTDATED


def build_key_index(items: Iterable[GalleryItem], key: str) -> tuple[dict[str, GalleryItem], list[str]]:
    """{key value: item} for ``items``, plus the key values used by more than one item
    (the last one wins, matching how the library treats duplicate ids)."""
    index: dict[str, GalleryItem] = {}
    dupes: set[str] = set()
    for it in items:
        try:
            k = it.value(key)
        except KeyError:
            continue
        if k in (None, ""):
            continue
        k = str(k)
        if k in index:
            dupes.add(k)
        index[k] = it
    return index, sorted(dupes)


class _RowDelegate(QStyledItemDelegate):
    """Drawer rows with a name format: the title, then the second line in the muted
    colour, on one line; the category's chip colour as a border around the thumbnail.
    The style still draws the checkbox, icon and selection."""

    def __init__(self, drawer: "DrawerWidget") -> None:
        super().__init__(drawer)
        self._drawer = drawer

    def paint(self, p, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        entry = index.data(EntryRole)
        parts = self._drawer.parts_for(entry.key if entry else opt.text)
        font = QFont(opt.font)
        fg = index.data(Qt.ForegroundRole)
        color = fg.color() if hasattr(fg, "color") else (QColor(fg) if fg is not None else self._drawer._text)
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, p, widget)
        r = style.subElementRect(QStyle.SE_ItemViewItemText, opt, widget).adjusted(2, 0, -2, 0)
        p.save()
        if parts.chip:                       # the category's colour around the thumbnail
            icon = style.subElementRect(QStyle.SE_ItemViewItemDecoration, opt, widget)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(chip_color(parts), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(icon).adjusted(-1, -1, 1, 1), 2, 2)
        x = r.left()
        p.setFont(font)
        p.setPen(color)
        fm = QFontMetrics(font)
        title = fm.elidedText(parts.title, Qt.ElideRight, max(0, r.right() - x))
        p.drawText(QRect(x, r.top(), r.right() - x, r.height()), Qt.AlignLeft | Qt.AlignVCenter, title)
        x += fm.horizontalAdvance(title) + 10
        if parts.subtitle and x < r.right() - 20:
            sub_font = QFont(font)
            sub_font.setBold(False)
            sub_font.setPointSizeF(max(7.0, font.pointSizeF() * 0.88))
            p.setFont(sub_font)
            p.setPen(self._drawer._muted)
            sfm = QFontMetrics(sub_font)
            p.drawText(QRect(x, r.top(), r.right() - x, r.height()), Qt.AlignLeft | Qt.AlignVCenter,
                       sfm.elidedText(parts.subtitle, Qt.ElideRight, r.right() - x))
        p.restore()


class _DrawerList(QListWidget):
    """Accepts gallery drops and reorders by internal drag.

    Gallery drops are accepted here explicitly: a drag nobody accepts travels up to
    the host window, and a DCC (Houdini) then treats the file URL as a scene to open.
    """
    itemsDropped = Signal(list)   # list[dict] gallery records
    rowsReordered = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setIconSize(QSize(48, 27))
        self.setUniformItemSizes(True)
        self.setSpacing(1)
        self.model().rowsMoved.connect(lambda *_: self.rowsReordered.emit())

    def mimeData(self, items):
        md = super().mimeData(items)      # Qt's own data: internal moves need it
        md.setData(MIME_DRAWER, json.dumps([li.data(EntryRole).key for li in items]).encode())
        return md

    def _gallery_drag(self, event) -> bool:
        return event.source() is not self and event.mimeData().hasFormat(MIME_ITEMS)

    def dragEnterEvent(self, event) -> None:
        if self._gallery_drag(event):
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if self._gallery_drag(event):
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if self._gallery_drag(event):
            event.setDropAction(Qt.CopyAction)
            event.accept()
            try:
                records = json.loads(bytes(event.mimeData().data(MIME_ITEMS)).decode("utf-8"))
            except ValueError:
                return
            if isinstance(records, dict):
                records = records.get("items") or [records]
            self.itemsDropped.emit([r for r in records if isinstance(r, dict)])
        elif event.source() is self:
            super().dropEvent(event)      # internal move -> rowsMoved
        else:
            event.ignore()


class DrawerWidget(QWidget):
    entriesChanged = Signal(list)        # list[DrawerEntry], after an edit in the UI
    showRequested = Signal(object)       # GalleryItem: "Show in gallery"
    currentChanged = Signal(object)      # GalleryItem | None: the row clicked in the drawer
    runRequested = Signal()              # the run button (e.g. "Ingest drawer")
    titleChanged = Signal(str)           # "Clips: 4  (3 enabled)", e.g. for a pane title
    logMessage = Signal(str)

    def __init__(self, key: str = "name", title: str = "Drawer", path_key: str = "path",
                 parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ILPDrawer")
        self.key = key
        self.path_key = path_key
        self.entry_factory: Callable[[GalleryItem], DrawerEntry | None] | None = None
        self._actions: list[tuple[str, Callable, bool]] = []   # add_action()
        self.viewer: Callable[[list[DrawerEntry]], None] | None = None   # View / double-click
        self._current_key: str | None = None                    # set_current_key()
        self.name_format: NameFormat | None = None              # set_name_format()
        self.show_paths = True                                  # paths in tooltips
        self._current_bg = QColor(70, 130, 220, 60)
        self.current_label = "Current"                          # tooltip line for it
        self.resolver: Callable[[str], GalleryItem | None] = lambda k: None
        self.by_id: Callable[[str], GalleryItem | None] = lambda i: None
        self.icon_for: Callable[[GalleryItem], QIcon | None] = lambda it: None
        self._entries: list[DrawerEntry] = []
        blank = QPixmap(48, 27)
        blank.fill(Qt.transparent)
        self._blank = QIcon(blank)        # keeps names aligned until a thumbnail exists
        self._muted = QColor("#8a909c")
        self._text = QColor("#d9dce3")

        self.title = title
        self.header = QLabel(title)
        self.status = QLabel("", objectName="muted")      # "2 outdated · 1 missing"
        self.update_btn = QPushButton("Update all", objectName="flat")
        self.update_btn.setToolTip("Point every outdated entry at the library's current path")
        self.update_btn.clicked.connect(lambda: self.update_to_latest(None))
        self.update_btn.hide()
        status_row = QHBoxLayout()
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.update_btn)
        top = QVBoxLayout()
        top.setContentsMargins(8, 6, 8, 0)
        top.setSpacing(2)
        top.addWidget(self.header)
        top.addLayout(status_row)

        self.list = _DrawerList(self)
        self.list.itemsDropped.connect(self._on_dropped)
        self.list.rowsReordered.connect(self._on_reordered)
        self.list.itemChanged.connect(self._on_item_changed)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.list.itemDoubleClicked.connect(self._show_entry)
        self.list.currentItemChanged.connect(self._on_current)

        remove = QPushButton("Remove")
        remove.clicked.connect(self.remove_selected)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear)
        self.run_btn = QPushButton("Run", objectName="primary")
        self.run_btn.clicked.connect(self.runRequested)
        self.run_btn.hide()                # shown by the gallery when an exporter exists
        buttons = QHBoxLayout()
        buttons.setContentsMargins(8, 0, 8, 8)
        buttons.addWidget(remove)
        buttons.addWidget(clear)
        buttons.addStretch(1)
        buttons.addWidget(self.run_btn)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addLayout(top)
        lay.addWidget(self.list, 1)
        lay.addLayout(buttons)
        self.setMinimumWidth(200)
        self._rebuild()

    # -- public API ----------------------------------------------------------
    def entries(self) -> list[DrawerEntry]:
        return list(self._entries)

    def set_entries(self, entries: Iterable, emit: bool = False) -> None:
        """Replace the list (silent unless ``emit``). Duplicate keys keep the first."""
        seen: set[str] = set()
        out = []
        for e in map(as_entry, entries):
            if e.key and e.key not in seen:
                seen.add(e.key)
                out.append(e)
        self._entries = out
        self._rebuild()
        if emit:
            self.entriesChanged.emit(self.entries())

    def keys(self) -> list[str]:
        return [e.key for e in self._entries]

    def state(self, entry: DrawerEntry) -> str:
        return entry_state(entry, self.resolver(entry.key), self.path_key)

    def make_entry(self, item: GalleryItem) -> DrawerEntry | None:
        """The entry for ``item`` (``entry_factory`` if set). None: the item can't be added."""
        if self.entry_factory is not None:
            e = self.entry_factory(item)
            return as_entry(e) if e is not None else None
        try:
            k = item.value(self.key)
        except KeyError:
            return None
        if k in (None, ""):
            return None
        return DrawerEntry(str(k), library_path(item, self.path_key), True)

    def states(self) -> dict[str, str]:
        return {e.key: self.state(e) for e in self._entries}

    def add_items(self, items: Iterable[GalleryItem]) -> int:
        """Add gallery items at the end (enabled, with their current path). Returns how many."""
        known = set(self.keys())
        added, skipped, keyless = [], 0, []
        for it in items:
            e = self.make_entry(it)
            if e is None:
                keyless.append(it.name)
                continue
            if e.key in known:
                skipped += 1
                continue
            known.add(e.key)
            added.append(e)
        if keyless:
            self.logMessage.emit(f"Not added (no {self.key!r} value): {', '.join(keyless)}")
        if skipped:
            self.logMessage.emit(f"{skipped} already in the {self.title.lower()}")
        if added:
            self._entries += added
            self._rebuild()
            self.entriesChanged.emit(self.entries())
        return len(added)

    def remove_keys(self, keys: Iterable[str]) -> None:
        drop = set(keys)
        if drop & set(self.keys()):
            self._entries = [e for e in self._entries if e.key not in drop]
            self._rebuild()
            self.entriesChanged.emit(self.entries())

    def remove_selected(self) -> None:
        self.remove_keys(self._selected_keys())

    def clear(self) -> None:
        if self._entries:
            self._entries = []
            self._rebuild()
            self.entriesChanged.emit(self.entries())

    def set_enabled_keys(self, keys: Iterable[str], enabled: bool) -> None:
        keys = set(keys)
        changed = False
        for i, e in enumerate(self._entries):
            if e.key in keys and e.enabled != enabled:
                self._entries[i] = replace(e, enabled=enabled)
                changed = True
        if changed:
            self._rebuild()
            self.entriesChanged.emit(self.entries())

    def update_to_latest(self, keys: Iterable[str] | None = None) -> int:
        """Point outdated entries (all, or ``keys``) at the library's current path."""
        keys = set(keys) if keys is not None else None
        n = 0
        for i, e in enumerate(self._entries):
            if keys is not None and e.key not in keys:
                continue
            item = self.resolver(e.key)
            if entry_state(e, item, self.path_key) == STATE_OUTDATED:
                self._entries[i] = replace(e, path=library_path(item, self.path_key))
                n += 1
        if n:
            self._rebuild()
            self.entriesChanged.emit(self.entries())
            self.logMessage.emit(f"Updated {n} entr{'y' if n == 1 else 'ies'} to the latest version")
        return n

    def refresh(self) -> None:
        """Re-check every entry against the library (after it loaded or changed)."""
        self._rebuild()

    def refresh_icons(self) -> None:
        for row in range(self.list.count()):
            li = self.list.item(row)
            icon = self.icon_for(self.resolver(li.data(EntryRole).key))
            if icon is not None:
                li.setIcon(icon)

    def parts_for(self, key: str):
        """NameParts for an entry: from its library item, or a stand-in when missing."""
        item = self.resolver(key)
        return format_name(self.name_format, item if item is not None else stub_item(key))

    def set_name_format(self, fmt: NameFormat | None) -> None:
        """How entry names are shown (see ui/names.py); the full name stays in the
        tooltip and is still the entry's key."""
        self.name_format = fmt
        self.list.setItemDelegate(_RowDelegate(self) if fmt is not None else QStyledItemDelegate(self.list))
        self.list.viewport().update()

    def set_current_key(self, key: str | None) -> None:
        """Mark one entry as the current one (bold, tinted row), e.g. the clip a host
        node is showing; None clears it. Not an edit: no ``entriesChanged``."""
        if key != self._current_key:
            self._current_key = key
            self._rebuild()

    def current_key(self) -> str | None:
        return self._current_key

    def set_colors(self, text: QColor, muted: QColor, accent: QColor | None = None) -> None:
        if accent is not None:
            self._current_bg = QColor(accent)
            self._current_bg.setAlpha(60)
        self._text, self._muted = QColor(text), QColor(muted)
        self._rebuild()

    # -- internals -----------------------------------------------------------
    def _selected_keys(self) -> list[str]:
        return [li.data(EntryRole).key for li in self.list.selectedItems()]

    def _rebuild(self) -> None:
        selected = set(self._selected_keys()) if self.list.count() else set()
        self.list.blockSignals(True)
        self.list.clear()
        outdated = missing = enabled = 0
        for e in self._entries:
            item = self.resolver(e.key)
            state = entry_state(e, item, self.path_key)
            name = item.name if item is not None else e.key   # the key can be an id ("id" drawers)
            li = QListWidgetItem(name)
            li.setSizeHint(QSize(0, 31))
            li.setData(EntryRole, e)
            li.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
            li.setCheckState(Qt.Checked if e.enabled else Qt.Unchecked)
            tip = [f"Path: {e.path or '(none)'}"] if self.show_paths else []
            if self.key != "name" and item is not None:
                tip.insert(0, f"{self.key}: {e.key}")
            if state == STATE_OUTDATED:
                outdated += 1
                li.setForeground(OUTDATED_COLOR)
                tip.append(f"Library has a newer path: {library_path(item, self.path_key)}"
                           if self.show_paths else "A newer version is in the library")
            elif state == STATE_MISSING:
                missing += 1
                li.setForeground(self._muted)
                f = QFont(li.font())
                f.setItalic(True)
                li.setFont(f)
                tip.append(f"Not in the library ({self.key} {e.key!r})")
            else:
                li.setForeground(self._text)
            if not e.enabled:
                tip.append("Disabled")
            if e.key == self._current_key:                 # e.g. the agent's current clip
                f = QFont(li.font())
                f.setBold(True)
                li.setFont(f)
                li.setBackground(self._current_bg)
                tip.append(self.current_label)
            enabled += e.enabled
            li.setToolTip(tooltip_html(name, self.parts_for(e.key), tip))
            icon = self.icon_for(item)                   # None: missing from the library
            li.setIcon(icon if icon is not None else self._blank)
            self.list.addItem(li)
            li.setSelected(e.key in selected)
        self.list.blockSignals(False)

        n = len(self._entries)
        title = f"{self.title}: {n}" + (f"  ({enabled} enabled)" if enabled != n else "")
        self.header.setText(title)
        self.titleChanged.emit(title)
        status = [f"{outdated} outdated"] * bool(outdated) + [f"{missing} missing"] * bool(missing)
        self.status.setText("  ·  ".join(status))
        self.status.setVisible(bool(status))
        self.update_btn.setVisible(outdated > 0)

    def _on_dropped(self, records: list[dict]) -> None:
        items = []
        for r in records:
            it = self.by_id(str(r.get("id", ""))) if r.get("id") else None
            if it is None:
                try:
                    it = GalleryItem.from_dict(r)
                except Exception:   # not a usable record: skip it
                    continue
            items.append(it)
        self.add_items(items)

    def _on_reordered(self) -> None:
        order = [self.list.item(r).data(EntryRole) for r in range(self.list.count())]
        if [e.key for e in order] != self.keys():
            self._entries = order
            self.entriesChanged.emit(self.entries())

    def _on_item_changed(self, li: QListWidgetItem) -> None:
        e: DrawerEntry = li.data(EntryRole)
        on = li.checkState() == Qt.Checked
        if e is not None and on != e.enabled:
            self.set_enabled_keys([e.key], on)

    def _on_current(self, li, _previous=None) -> None:
        e = li.data(EntryRole) if li is not None else None
        self.currentChanged.emit(self.resolver(e.key) if e is not None else None)

    def _show_entry(self, li: QListWidgetItem) -> None:
        """Double-click: View the entry when a viewer is set, else show it in the gallery."""
        entry = li.data(EntryRole)
        item = self.resolver(entry.key)
        if item is None:
            return
        if self.viewer is not None:
            self._run_action(self.viewer, [entry])
        else:
            self.showRequested.emit(item)

    def add_action(self, label: str, callback: Callable[[list[DrawerEntry]], None],
                   single: bool = False) -> None:
        """An entry at the top of the rows' context menu. ``callback`` receives the
        selected entries, in drawer order. ``single=True``: only offered when exactly
        one row is selected (e.g. "Set As Current")."""
        self._actions.append((label, callback, single))

    def context_menu(self, keys: list[str]) -> QMenu | None:
        """The context menu for the rows with ``keys`` (None if there are none)."""
        keys = [k for k in self.keys() if k in set(keys)]
        if not keys:
            return None
        entries = [e for e in self._entries if e.key in set(keys)]
        menu = QMenu(self)
        for label, callback, single in self._actions:
            if single and len(entries) != 1:
                continue
            menu.addAction(label, lambda cb=callback: self._run_action(cb, entries))
        viewable = [e for e in entries if self.resolver(e.key) is not None]
        if self.viewer is not None and viewable:
            menu.addAction(f"View ({len(viewable)})" if len(viewable) > 1 else "View",
                           lambda: self._run_action(self.viewer, viewable))
        if menu.actions():
            menu.addSeparator()
        outdated = [e.key for e in entries if self.state(e) == STATE_OUTDATED]
        if outdated:
            menu.addAction(f"Update to latest ({len(outdated)})", lambda: self.update_to_latest(outdated))
        menu.addAction("Enable", lambda: self.set_enabled_keys(keys, True))
        menu.addAction("Disable", lambda: self.set_enabled_keys(keys, False))
        if len(keys) == 1 and self.resolver(keys[0]) is not None:
            menu.addAction("Show in gallery", lambda: self.showRequested.emit(self.resolver(keys[0])))
        menu.addSeparator()
        menu.addAction(f"Remove Items ({len(keys)})" if len(keys) > 1 else "Remove Item",
                       lambda: self.remove_keys(keys))
        return menu

    def _run_action(self, callback, entries: list[DrawerEntry]) -> None:
        try:
            callback(list(entries))
        except Exception as exc:  # a broken host action must not break the drawer
            self.logMessage.emit(f"Drawer action failed: {exc}")

    def _context_menu(self, pos) -> None:
        li = self.list.itemAt(pos)
        if li is not None and not li.isSelected():
            self.list.clearSelection()
            li.setSelected(True)
        menu = self.context_menu(self._selected_keys())
        if menu is not None:
            menu.exec(self.list.viewport().mapToGlobal(pos))
