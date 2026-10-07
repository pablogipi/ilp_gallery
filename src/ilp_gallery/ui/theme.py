"""Colours + stylesheet. Applied to our top widget only, so hosts keep their own look."""

from PySide6.QtGui import QColor

BG = "#1d2027"          # window
PANEL = "#23262e"       # side panels
TILE = "#2a2e37"
TILE_HOVER = "#323743"
IMAGE_BG = "#1f2229"
BORDER = "#363b47"
TEXT = "#d9dce3"
MUTED = "#8a909c"
ACCENT = "#3d7fe0"
ACCENT_HOVER = "#4b8ef0"
SELECT_BG = "#2d4a78"
LINK = "#6ea6f5"


def c(hex_: str) -> QColor:
    return QColor(hex_)


STYLESHEET = f"""
QWidget#ILPGalleryRoot, QWidget#ILPGalleryRoot QWidget {{
    color: {TEXT};
    font-size: 10pt;
}}
QMainWindow#ILPGalleryRoot, QWidget#ILPGallery {{ background: {BG}; }}
QMenuBar {{ background: {BG}; color: {TEXT}; }}
QMenuBar::item:selected {{ background: {TILE_HOVER}; }}
QMenu {{ background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER}; }}
QMenu::item:selected {{ background: {SELECT_BG}; }}
QStatusBar {{ background: {BG}; color: {MUTED}; }}

QLineEdit {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 4px;
    padding: 5px 8px; selection-background-color: {ACCENT};
}}
QLineEdit:focus {{ border-color: {ACCENT}; }}

QPushButton, QToolButton {{
    background: {TILE}; border: 1px solid {BORDER}; border-radius: 4px; padding: 5px 12px;
}}
QPushButton:hover, QToolButton:hover {{ background: {TILE_HOVER}; }}
QWidget#ILPGalleryRoot QPushButton:disabled {{ color: {MUTED}; background: {PANEL}; }}
QPushButton#flat, QToolButton#flat {{ background: transparent; border: none; padding: 5px 8px; }}
QPushButton#flat:hover {{ background: {TILE_HOVER}; }}
QPushButton#primary {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 600; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; }}
QWidget#ILPGalleryRoot QPushButton#primary:disabled {{ background: {BORDER}; border-color: {BORDER}; color: {MUTED}; }}

QTreeView {{
    background: {PANEL}; border: none; outline: 0; padding-top: 4px;
    show-decoration-selected: 1;
}}
QTreeView::item {{ padding: 4px 2px; }}
QTreeView::item:hover {{ background: {TILE}; }}
QTreeView::item:selected {{ background: {SELECT_BG}; color: white; }}

QListView {{ background: {BG}; border: none; outline: 0; }}
QScrollArea {{ background: {PANEL}; border: none; }}
QScrollArea > QWidget > QWidget {{ background: {PANEL}; }}
QWidget#details {{ background: {PANEL}; }}

QScrollBar:vertical {{ background: {BG}; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {MUTED}; }}
QScrollBar:horizontal {{ background: {BG}; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}

QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {TEXT}; width: 12px; height: 12px; margin: -5px 0; border-radius: 6px;
}}
QSplitter::handle {{ background: {BG}; }}
QSplitter::handle:horizontal {{ width: 3px; }}

QDockWidget {{ color: {TEXT}; titlebar-close-icon: none; }}
QDockWidget::title {{ background: {BG}; padding: 4px 6px; }}
QPlainTextEdit {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 4px;
    font-family: monospace; font-size: 9pt;
}}
QWidget#ILPGalleryRoot QLabel#muted {{ color: {MUTED}; }}
QWidget#ILPGalleryRoot QLabel#title {{ font-size: 14pt; font-weight: 600; }}
QToolTip {{ background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER}; }}
QCheckBox {{ spacing: 6px; }}
"""
