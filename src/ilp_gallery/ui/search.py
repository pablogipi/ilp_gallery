"""Search field that completes the word under the cursor.

Typing ``her`` offers ``hero``, ``tag:hero``, ``cat:heroes`` ... and
picking one replaces only the current word, so queries can be built up
token by token.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QStringListModel, Qt, QTimer, Signal
from PySide6.QtWidgets import QCompleter, QLineEdit

_WORD_RE = re.compile(r'(-?(?:[A-Za-z]+:)?(?:"[^"]*"?|[^\s"]*))$')


def quote_value(value: str) -> str:
    return f'"{value}"' if re.search(r"\s", value) else value


class SearchField(QLineEdit):
    queryChanged = Signal(str)  # debounced

    def __init__(self, parent=None, delay_ms: int = 120) -> None:
        super().__init__(parent)
        self.setClearButtonEnabled(True)
        self.setPlaceholderText('Search…   walk   tag:hero   cat:crowd   type:movie   -loop   "exact phrase"')
        self._words = QStringListModel(self)
        self._completer = QCompleter(self._words, self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchContains)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.setMaxVisibleItems(12)
        self._completer.setWidget(self)
        self._completer.activated.connect(self._insert_completion)

        self._debounce = QTimer(self, singleShot=True, interval=delay_ms)
        self._debounce.timeout.connect(lambda: self.queryChanged.emit(self.text()))
        self.textChanged.connect(lambda _t: self._debounce.start())
        self.textEdited.connect(self._update_popup)
        self.returnPressed.connect(lambda: (self._debounce.stop(), self.queryChanged.emit(self.text())))

    def set_vocabulary(self, tags: list[str], categories: list[str]) -> None:
        words = []
        for t in tags:
            words += [t, f"tag:{quote_value(t)}"]
        for c in categories:
            words += [c, f"cat:{quote_value(c)}"]
        words += ["type:image", "type:gif", "type:movie"]
        self._words.setStringList(sorted(set(words), key=str.lower))

    def _current_word(self) -> tuple[int, str]:
        head = self.text()[: self.cursorPosition()]
        m = _WORD_RE.search(head)
        word = m.group(1) if m else ""
        return len(head) - len(word), word

    def _update_popup(self, _text: str) -> None:
        start, word = self._current_word()
        bare = word.lstrip("-")
        if len(bare) < 2:
            self._completer.popup().hide()
            return
        self._completer.setCompletionPrefix(bare)
        if self._completer.completionCount() == 0:
            self._completer.popup().hide()
            return
        rect = self.cursorRect()
        rect.setWidth(max(260, self._completer.popup().sizeHintForColumn(0) + 30))
        self._completer.complete(rect)

    def _insert_completion(self, completion: str) -> None:
        start, word = self._current_word()
        neg = "-" if word.startswith("-") else ""
        text, pos = self.text(), self.cursorPosition()
        new = text[:start] + neg + completion + " "
        self.setText(new + text[pos:].lstrip())
        self.setCursorPosition(len(new))

    def toggle_token(self, token: str) -> None:
        """Add ``token`` to the query, or remove it if already there (sidebar clicks)."""
        parts = self.text().split()
        if token in parts:
            parts.remove(token)
            self.setText(" ".join(parts))
        else:
            self.setText((self.text().rstrip() + " " + token).strip() + " ")
        self.setFocus()
        self.setCursorPosition(len(self.text()))

    def keyPressEvent(self, e) -> None:
        popup = self._completer.popup()
        if popup.isVisible() and e.key() in (Qt.Key_Enter, Qt.Key_Return, Qt.Key_Tab):
            idx = popup.currentIndex()
            if idx.isValid():
                self._insert_completion(idx.data())
                popup.hide()
                return
        if e.key() == Qt.Key_Escape and not popup.isVisible() and self.text():
            self.clear()
            return
        super().keyPressEvent(e)
