"""The gallery's own log pane: messages (loads, exports, errors) with a time stamp,
Clear, and Stop while a stoppable task (an export) runs. Enabled with
``GalleryWidget(log=True)``; it sits under the details panel in the right column
and collapses like the other panes, its tab counting messages arrived meanwhile."""

from __future__ import annotations

import time

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget


class LogPane(QWidget):
    stopRequested = Signal()

    def __init__(self, max_lines: int = 2000, parent=None) -> None:
        super().__init__(parent)
        self.view = QPlainTextEdit(readOnly=True)
        self.view.setMaximumBlockCount(max_lines)
        self.view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        font.setPointSizeF(max(7.5, QFont().pointSizeF() * 0.9))
        self.view.setFont(font)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.clear)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stopRequested)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(6, 0, 6, 6)
        buttons.addStretch(1)
        buttons.addWidget(self.stop_btn)
        buttons.addWidget(self.clear_btn)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(self.view, 1)
        lay.addLayout(buttons)
        self.unseen = 0

    def log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        lines = str(message).splitlines() or [""]
        self.view.appendPlainText(f"{stamp}  {lines[0]}")
        for line in lines[1:]:                       # tracebacks: indented under their line
            self.view.appendPlainText(f"          {line}")
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())
        self.unseen += 1

    def text(self) -> str:
        return self.view.toPlainText()

    def clear(self) -> None:
        self.view.clear()
        self.unseen = 0

    def set_running(self, stoppable: bool) -> None:
        self.stop_btn.setEnabled(stoppable)
