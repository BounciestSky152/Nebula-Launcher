"""GOG Galaxy 2.0-inspired themes: the dark Nebula (and Classic) and the light Super Nova."""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QLinearGradient, QPalette
from PySide6.QtWidgets import QApplication

# Nebula (and Classic) are dark; Super Nova is the light theme: a bright sky with every colour of an exploding star
PALETTES = {
    "dark": {
        "BG": "#16161b", "SIDEBAR": "#0f0f13", "SURFACE": "#1e1e25", "SURFACE2": "#262630", "SURFACE3": "#30303c",
        "BORDER": "#2b2b36", "TEXT": "#e9e9f0", "TEXT_DIM": "#a0a0b0", "TEXT_FAINT": "#6c6c7c",
        "ACCENT": "#7b4fc4", "ACCENT_HOVER": "#8c62d4", "ACCENT_PRESSED": "#6a40ad", "ACCENT_SOFT": "#2a2335",
        "SUCCESS": "#4fbf73", "WARNING": "#e3a63b", "DANGER": "#e25c5c", "INFO": "#4aa3df",
        # translucent "glass" surfaces that let the starfield show through
        "GLASS": "rgba(16, 16, 20, 0.82)", "GLASS_DARK": "rgba(13, 13, 17, 0.88)",
        "GLASS_CARD": "rgba(26, 26, 32, 0.86)", "GLASS_BORDER": "rgba(255, 255, 255, 0.07)",
    },
    "light": {  # text colours keep >= 4.5:1 on the frosted glass; the bright colours live in the gradients below
        "BG": "#fbf6ff", "SIDEBAR": "#f4ecff", "SURFACE": "#ffffff", "SURFACE2": "#f7f0ff", "SURFACE3": "#ebdffa",
        "BORDER": "#e2d3f5", "TEXT": "#1e1233", "TEXT_DIM": "#54476e", "TEXT_FAINT": "#6f6390",
        "ACCENT": "#db1a76", "ACCENT_HOVER": "#c2127a", "ACCENT_PRESSED": "#b00f63", "ACCENT_SOFT": "#ffe3f1",
        "SUCCESS": "#0c8346", "WARNING": "#c05600", "DANGER": "#d42a3a", "INFO": "#0b78b8",
        "GLASS": "rgba(255, 255, 255, 0.72)", "GLASS_DARK": "rgba(255, 251, 255, 0.84)",
        "GLASS_CARD": "rgba(255, 255, 255, 0.76)", "GLASS_BORDER": "rgba(150, 70, 200, 0.18)",
    },
}
PALETTE = "dark"
# the colours as module constants, read by painted widgets; use_palette() swaps them
BG = SIDEBAR = SURFACE = SURFACE2 = SURFACE3 = BORDER = TEXT = TEXT_DIM = TEXT_FAINT = ""
ACCENT = ACCENT_HOVER = ACCENT_PRESSED = ACCENT_SOFT = SUCCESS = WARNING = DANGER = INFO = ""
GLASS = GLASS_DARK = GLASS_CARD = GLASS_BORDER = ""
globals().update(PALETTES[PALETTE])

QSS_TEMPLATE = """
* {{
    outline: none;
}}
QWidget {{
    color: {TEXT};
    font-size: 10pt;
}}
QMainWindow {{
    background: {BG};
}}
#Page, #ContentArea, QStackedWidget#Pages, #Central {{
    background: transparent;
}}
QScrollArea, QScrollArea > QWidget > QWidget {{
    background: transparent;
    border: none;
}}
QToolTip {{
    background: {SURFACE2};
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 4px 8px;
    border-radius: 4px;
}}
/* ---------------- sidebar ---------------- */
#Sidebar {{
    background: transparent;  /* painted by Sidebar.paintEvent */
}}
#Brand {{
    font-size: 15pt;
    font-weight: 800;
    letter-spacing: 3px;
}}
#BrandSub {{
    color: #a9a3c9;
    font-size: 8pt;
    font-weight: 700;
    letter-spacing: 2px;
}}
#SidebarSection {{
    color: #7d7899;
    font-size: 7.5pt;
    font-weight: 700;
    letter-spacing: 2px;
    padding: 16px 20px 5px 20px;
}}
QPushButton#SidebarBrand {{ background: transparent; border: none; }}
QPushButton#SidebarButton {{
    text-align: left;
    padding: 7px 14px 7px 16px;
    border: none;
    border-radius: 6px;
    margin: 0 8px;
    color: {TEXT_DIM};
    background: transparent;
}}
QPushButton#SidebarTool {{
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(183, 156, 255, 0.18);
    border-radius: 6px;
    padding: 6px;
    color: {TEXT};
}}
QPushButton#SidebarTool:hover {{
    background: rgba(140, 98, 212, 0.22);
    border-color: rgba(183, 156, 255, 0.5);
}}
QPushButton#SidebarAdd {{
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(183, 156, 255, 0.25);
    border-radius: 6px;
    padding: 8px;
    color: {TEXT};
}}
QPushButton#SidebarAdd:hover {{
    background: rgba(140, 98, 212, 0.22);
    border-color: rgba(183, 156, 255, 0.5);
}}
#ProfileName {{
    font-weight: 600;
    font-size: 11pt;
}}
#ProfileStatus {{
    color: {TEXT_FAINT};
    font-size: 8pt;
}}
/* ---------------- top bar ---------------- */
#TopBar {{
    background: {GLASS};
    border-bottom: 1px solid {GLASS_BORDER};
}}
#UpdateBanner {{
    background: rgba(143, 94, 240, 0.22);
    border-bottom: 1px solid rgba(163, 120, 255, 0.45);
}}
QPushButton#GogSignIn {{
    background: {ACCENT};
    border: none;
    border-radius: 6px;
    color: white;
    font-weight: 700;
    padding: 6px 16px;
}}
QPushButton#GogSignIn:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#AvatarButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 18px;
    padding: 2px 10px 2px 2px;
    text-align: left;
}}
QPushButton#AvatarButton:hover {{ background: {SURFACE2}; border-color: {BORDER}; }}
QPushButton#AvatarButton::menu-indicator {{ image: none; }}
#DrmFree {{
    background: rgba(79, 191, 115, 0.18);
    color: {SUCCESS};
    border: 1px solid rgba(79, 191, 115, 0.5);
    border-radius: 4px;
    padding: 1px 7px;
    font-size: 8pt;
    font-weight: 800;
    letter-spacing: 1px;
}}
#FeatureChip {{
    background: rgba(255, 255, 255, 0.06);
    color: {TEXT_DIM};
    border: 1px solid {GLASS_BORDER};
    border-radius: 4px;
    padding: 1px 7px;
    font-size: 8pt;
    font-weight: 600;
}}
QLineEdit#SearchBox {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 12px 6px 32px;
    min-width: 260px;
}}
QLineEdit#SearchBox:focus {{
    border-color: {ACCENT};
}}
/* ---------------- buttons ---------------- */
QPushButton {{
    background: {SURFACE2};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 14px;
    color: {TEXT};
}}
QPushButton:hover {{
    background: {SURFACE3};
}}
QPushButton:pressed {{
    background: {SURFACE};
}}
QPushButton:disabled {{
    color: {TEXT_FAINT};
    background: {SURFACE};
}}
QPushButton:checked {{
    background: {ACCENT_SOFT};
    border-color: {ACCENT};
}}
QPushButton#Primary {{
    background: {ACCENT};
    border: none;
    color: white;
    font-weight: 600;
    padding: 8px 22px;
}}
QPushButton#Primary:hover {{
    background: {ACCENT_HOVER};
}}
QPushButton#Primary:pressed {{
    background: {ACCENT_PRESSED};
}}
QPushButton#Primary:disabled {{
    background: {SURFACE3};
    color: {TEXT_DIM};
}}
QPushButton#Danger {{
    background: transparent;
    border: 1px solid {DANGER};
    color: {DANGER};
}}
QPushButton#Danger:hover {{
    background: #3a1e22;
}}
QPushButton#PlayButton {{
    background: {ACCENT};
    border: none;
    border-top-left-radius: 6px;
    border-bottom-left-radius: 6px;
    border-top-right-radius: 0;
    border-bottom-right-radius: 0;
    color: white;
    font-weight: 700;
    font-size: 11pt;
    padding: 10px 34px;
    min-width: 120px;
}}
QPushButton#PlayButton:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#PlayButton[state="running"] {{ background: {SUCCESS}; }}
QPushButton#PlayButton[state="update"] {{ background: {WARNING}; color: #1a1408; }}
QPushButton#PlayButton[state="busy"] {{ background: {SURFACE3}; color: {TEXT}; }}
QPushButton#PlayMenu {{
    background: {ACCENT_PRESSED};
    border: none;
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
    border-top-left-radius: 0;
    border-bottom-left-radius: 0;
    padding: 10px 8px;
}}
QPushButton#PlayMenu:hover {{ background: {ACCENT}; }}
QPushButton#PlayMenu::menu-indicator {{ image: none; }}
QPushButton#IconButton, QToolButton#IconButton {{
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 6px;
}}
QPushButton#IconButton:hover, QToolButton#IconButton:hover {{
    background: {SURFACE2};
}}
QPushButton#IconButton:checked {{
    background: {ACCENT_SOFT};
}}
QPushButton#IconButton::menu-indicator, QToolButton#IconButton::menu-indicator {{ image: none; }}
QPushButton#Chip {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 3px 10px;
    color: {TEXT_DIM};
    font-size: 9pt;
}}
QPushButton#Chip:hover {{ color: {TEXT}; border-color: {ACCENT}; }}
QPushButton#Chip:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT}; color: {TEXT}; }}
QPushButton#Link {{
    background: transparent;
    border: none;
    color: {ACCENT_HOVER};
    padding: 2px 4px;
    text-align: left;
}}
QPushButton#Link:hover {{ text-decoration: underline; }}
/* ---------------- inputs ---------------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT_SOFT};
    outline: none;
}}
QCheckBox, QRadioButton {{
    spacing: 8px;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {TEXT_FAINT};
    background: {SURFACE};
    border-radius: 4px;
}}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}
QCheckBox::indicator:indeterminate {{
    background: {SURFACE3};
    border-color: {TEXT_DIM};
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: {SURFACE3};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {ACCENT};
    width: 14px;
    margin: -6px 0;
    border-radius: 7px;
}}
QSlider::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 2px;
}}
/* ---------------- progress ---------------- */
QProgressBar {{
    background: {SURFACE3};
    border: none;
    border-radius: 3px;
    height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 3px;
}}
QProgressBar#Achievements::chunk {{ background: {WARNING}; }}
/* ---------------- lists & tables ---------------- */
QListView, QTreeView, QTableView, QListWidget, QTreeWidget, QTableWidget {{
    background: transparent;
    border: none;
    alternate-background-color: {SURFACE};
}}
QTableView {{
    gridline-color: transparent;
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT};
}}
QTableView::item {{
    padding: 4px 6px;
    border-bottom: 1px solid {BORDER};
}}
QHeaderView::section {{
    background: {GLASS_DARK};
    color: {TEXT_FAINT};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 6px;
    font-size: 8pt;
    font-weight: 600;
    text-transform: uppercase;
}}
QListWidget::item, QTreeWidget::item {{
    padding: 6px;
    border-radius: 6px;
}}
QListWidget::item:selected, QTreeWidget::item:selected {{
    background: {ACCENT_SOFT};
    color: {TEXT};
}}
QListWidget::item:hover, QTreeWidget::item:hover {{
    background: {SURFACE};
}}
/* ---------------- scrollbars ---------------- */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {SURFACE3};
    border-radius: 4px;
    min-height: 40px;
    margin: 2px;
}}
QScrollBar::handle:vertical:hover {{ background: {TEXT_FAINT}; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
}}
QScrollBar::handle:horizontal {{
    background: {SURFACE3};
    border-radius: 4px;
    min-width: 40px;
    margin: 2px;
}}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
    border: none;
    height: 0;
    width: 0;
}}
/* ---------------- tabs ---------------- */
QTabWidget::pane {{
    border: none;
    border-top: 1px solid {BORDER};
}}
QTabBar::tab {{
    background: transparent;
    color: {TEXT_DIM};
    padding: 10px 16px;
    border: none;
    border-bottom: 2px solid transparent;
    font-weight: 600;
}}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{
    color: {TEXT};
    border-bottom: 2px solid {ACCENT};
}}
/* ---------------- menus ---------------- */
QMenu {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px;
}}
QMenu::item {{
    padding: 7px 26px 7px 12px;
    border-radius: 4px;
}}
QMenu::item:selected {{ background: {ACCENT_SOFT}; }}
QMenu::item:disabled {{ color: {TEXT_FAINT}; }}
QMenu::separator {{
    height: 1px;
    background: {BORDER};
    margin: 5px 4px;
}}
QMenu::icon {{ padding-left: 8px; }}
/* ---------------- cards & labels ---------------- */
#Card {{
    background: {GLASS_CARD};
    border: 1px solid {GLASS_BORDER};
    border-radius: 6px;
}}
#PageTitle {{
    font-size: 20pt;
    font-weight: 700;
}}
#SectionTitle {{
    font-size: 13pt;
    font-weight: 700;
}}
#Subtle {{ color: {TEXT_DIM}; }}
#Faint {{ color: {TEXT_FAINT}; font-size: 9pt; }}
#StatValue {{ font-size: 15pt; font-weight: 700; }}
#StatLabel {{ color: {TEXT_FAINT}; font-size: 8pt; font-weight: 600; letter-spacing: 1px; }}
#Badge {{
    background: {ACCENT};
    color: white;
    border-radius: 8px;
    padding: 0 6px;
    font-size: 8pt;
    font-weight: 700;
}}
#Warning {{ color: {WARNING}; }}
#Error {{ color: {DANGER}; }}
#Success {{ color: {SUCCESS}; }}
QDialog {{ background: {BG}; }}
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    margin-top: 14px;
    padding: 12px 10px 10px 10px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: {TEXT_DIM};
}}
QSplitter::handle {{ background: {BORDER}; }}
"""
QSS = QSS_TEMPLATE.format(**PALETTES[PALETTE])


# fonts picked in Settings › General › Appearance ("" = system font); Audiowide and Orbitron ship with Super Nova
FONTS = {"ui": "", "display": "Audiowide", "sidebar": "Orbitron"}
_base_font: QFont | None = None
_fonts_loaded = False


def load_fonts() -> None:
    global _fonts_loaded
    if _fonts_loaded:
        return
    _fonts_loaded = True
    from pathlib import Path

    from PySide6.QtGui import QFontDatabase

    for f in sorted((Path(__file__).resolve().parent.parent / "assets" / "fonts").glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(f))


HANDHELD = False  # fullscreen, bigger text and targets, gamepad navigation
HANDHELD_QSS = """
QPushButton#SidebarButton { min-height: 46px; }
QPushButton:focus { border: 2px solid #b79cff; }
*[controllerForm="true"] QCheckBox { padding: 4px 8px; border-radius: 6px; border: 2px solid transparent; }
*[controllerForm="true"] QCheckBox:focus { background: rgba(140, 98, 212, 0.28); border-color: #b79cff; }
*[controllerForm="true"] QComboBox, *[controllerForm="true"] QSpinBox, *[controllerForm="true"] QLineEdit
    { min-height: 30px; }
*[controllerForm="true"] QComboBox:focus, *[controllerForm="true"] QSpinBox:focus,
*[controllerForm="true"] QLineEdit:focus, *[controllerForm="true"] QListWidget:focus
    { border: 2px solid #b79cff; background: rgba(140, 98, 212, 0.18); }
*[controllerForm="true"] QTabBar::tab:selected:focus { background: rgba(140, 98, 212, 0.35); border-radius: 6px; }
"""
MODE = "nebula"  # "nebula" (space-styled sidebar, bundled fonts) | "supernova" (light) | "classic" (the original look)
CLASSIC_QSS = """
#Sidebar { background: rgba(8, 7, 18, 0.72); border-right: 1px solid rgba(160, 140, 255, 0.14); }
QPushButton#SidebarButton:hover { background: #1e1e25; }
QPushButton#SidebarButton:checked { background: #2c2142; }
#TopBar { background: rgba(12, 10, 24, 0.55); }
#Card { background: rgba(28, 26, 44, 0.72); border: 1px solid rgba(160, 140, 255, 0.14); }
"""


# Super Nova's colours, for style sheets and painted widgets alike
NOVA = ("#7a2cf0", "#e5157f", "#ff6a1a")  # violet, hot pink, tangerine: what is "on" (deep enough for white text)
SPECTRUM = ("#14b8e6", "#8a50ff", "#ff3d8b", "#ff862e", "#ffb600")  # the whole explosion, cyan to gold (no text on it)
GOLD = ("#ffb600", "#ff6a1a")
FLARE = ("#ffe1f0", "#f1e4ff", "#ffefd6")  # pastel pink, lilac and peach: soft highlights behind dark text


def stops(colors) -> str:
    """``colors`` as evenly spaced qlineargradient stops."""
    return ", ".join(f"stop:{i / (len(colors) - 1):g} {c}" for i, c in enumerate(colors))


def gradient(x1: float, y1: float, x2: float, y2: float, colors=NOVA, alpha: int = 255) -> QLinearGradient:
    """A painter gradient through ``colors``, for the light theme's painted accents."""
    g = QLinearGradient(x1, y1, x2, y2)
    for i, c in enumerate(colors):
        col = QColor(c)
        col.setAlpha(alpha)
        g.setColorAt(i / (len(colors) - 1), col)
    return g


# the light theme on top of the shared style sheet: everything that is on, selected or in focus burns in supernova
# colours; text stays dark on light glass
SUPERNOVA_QSS = """
QPushButton#Primary, QPushButton[primary="true"], QPushButton#GogSignIn, QPushButton#PlayButton {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, {nova});
    color: white;
    border: none;
}}
QPushButton#Primary:hover, QPushButton[primary="true"]:hover, QPushButton#GogSignIn:hover,
QPushButton#PlayButton:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #8c45ff, stop:0.5 #f02a8e, stop:1 #ff7d33);
}}
QPushButton#Primary:pressed, QPushButton#PlayButton:pressed {{ background: {ACCENT_PRESSED}; }}
QPushButton#Primary:disabled {{ background: {SURFACE3}; color: {TEXT_FAINT}; }}
QPushButton#PlayButton[state="running"] {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0c8346, stop:1 #0b7d86);
}}
QPushButton#PlayButton[state="update"] {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, {gold}); color: #2b1400; }}
QPushButton#PlayButton[state="busy"] {{ background: {SURFACE3}; color: {TEXT}; }}
QPushButton#PlayMenu {{ background: #e2560c; }}
QPushButton#PlayMenu:hover {{ background: #ff6a1a; }}
QPushButton#Danger:hover {{ background: #ffe4e7; }}
QPushButton:checked, QPushButton#Chip:checked {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, {flare});
    border-color: {ACCENT};
}}
QPushButton#Chip, #FeatureChip {{ background: rgba(255, 255, 255, 0.72); }}
#Badge {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, {nova}); }}
#BrandSub {{ color: {ACCENT}; }}
#TopBar {{ border-bottom: 1px solid qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(138, 80, 255, 0.35),
    stop:0.5 rgba(255, 61, 139, 0.35), stop:1 rgba(255, 182, 0, 0.35)); }}
#UpdateBanner {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(138, 80, 255, 0.16),
        stop:0.5 rgba(255, 61, 139, 0.14), stop:1 rgba(255, 182, 0, 0.18));
    border-bottom: 1px solid rgba(229, 21, 127, 0.3);
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QLineEdit#SearchBox:focus {{
    border-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, {nova});
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, {nova});
    border-color: {ACCENT};
}}
QSlider::sub-page:horizontal {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, {spectrum}); }}
QSlider::handle:horizontal {{ background: white; border: 3px solid {ACCENT}; width: 8px; }}
QProgressBar::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, {spectrum}); }}
QProgressBar#Achievements::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, {gold}); }}
QTabBar::tab:hover {{ color: {ACCENT_HOVER}; }}
QTabBar::tab:selected {{
    color: {TEXT};
    border-bottom: 2px solid qlineargradient(x1:0, y1:0, x2:1, y2:0, {nova});
}}
QListWidget::item:selected, QTreeWidget::item:selected, QMenu::item:selected {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, {flare});
    color: {TEXT};
}}
QComboBox QAbstractItemView {{ selection-color: {TEXT}; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background: rgba(122, 44, 240, 0.2); }}
QScrollBar::handle:vertical:hover {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, {nova}); }}
QScrollBar::handle:horizontal:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, {nova}); }}
QDialog {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #fff5fa, stop:0.5 #f8f1ff, stop:1 #eef6ff); }}
""".format(nova=stops(NOVA), spectrum=stops(SPECTRUM), gold=stops(GOLD), flare=stops(FLARE), **PALETTES["light"])


def classic() -> bool:
    return MODE == "classic"


def light() -> bool:
    return PALETTE == "light"


def use_palette(name: str) -> bool:
    """Switch the colour constants and the style sheet to ``name`` ("dark" | "light"); True when it changed."""
    global PALETTE, QSS
    if name == PALETTE or name not in PALETTES:
        return False
    PALETTE = name
    globals().update(PALETTES[name])
    QSS = QSS_TEMPLATE.format(**PALETTES[name])
    return True


def font(role: str, point_size: float = 10) -> QFont:
    """The configured font for ``role`` (ui | display | sidebar); the classic theme uses the system font."""
    f = QFont(_base_font) if _base_font is not None else QFont()
    if FONTS.get(role) and (role == "ui" or not classic()):
        f.setFamily(FONTS[role])
    f.setPointSizeF(point_size)
    return f


def set_fonts(app: QApplication, ui: str = "", display: str = "", sidebar: str = "", mode: str | None = None) -> None:
    global MODE
    MODE = mode or MODE
    if use_palette("light" if MODE == "supernova" else "dark"):
        _qt_palette(app)
    FONTS.update(ui=ui or "", display=display or "", sidebar=sidebar or "")
    load_fonts()
    size = _base_font.pointSizeF() if _base_font is not None else 10
    app.setFont(font("ui", size + 3 if HANDHELD else size))
    rules = [CLASSIC_QSS] if classic() else [SUPERNOVA_QSS] if light() else []
    if HANDHELD:
        rules.append(HANDHELD_QSS)
    if FONTS["display"] and not classic():
        rules.append(f'#PageTitle {{ font-family: "{FONTS["display"]}"; font-weight: 400; }}')
    if FONTS["sidebar"] and not classic():
        rules.append(f'QPushButton#SidebarButton QLabel {{ font-family: "{FONTS["sidebar"]}"; font-size: 9pt; }}')
    app.setStyleSheet(QSS + "\n".join(rules))


def _qt_palette(app: QApplication) -> None:
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(BG))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Base, QColor(SURFACE))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(SURFACE2))
    pal.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Button, QColor(SURFACE2))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("white"))
    pal.setColor(QPalette.ColorRole.Light, QColor(SURFACE3))
    pal.setColor(QPalette.ColorRole.Mid, QColor(BORDER))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(SURFACE2))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(TEXT_FAINT))
    pal.setColor(QPalette.ColorRole.Link, QColor(ACCENT_HOVER))
    app.setPalette(pal)


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    _qt_palette(app)
    f = app.font()
    if f.pointSize() < 10:
        f.setPointSize(10)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    global _base_font
    _base_font = QFont(f)
    set_fonts(app, FONTS["ui"], FONTS["display"], FONTS["sidebar"])
