"""Dark, GOG Galaxy 2.0-inspired theme."""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

BG = "#16161b"
SIDEBAR = "#0f0f13"
SURFACE = "#1e1e25"
SURFACE2 = "#262630"
SURFACE3 = "#30303c"
BORDER = "#2b2b36"
TEXT = "#e9e9f0"
TEXT_DIM = "#a0a0b0"
TEXT_FAINT = "#6c6c7c"
ACCENT = "#7b4fc4"
ACCENT_HOVER = "#8c62d4"
ACCENT_PRESSED = "#6a40ad"
ACCENT_SOFT = "#2a2335"
SUCCESS = "#4fbf73"
WARNING = "#e3a63b"
DANGER = "#e25c5c"
INFO = "#4aa3df"
# translucent "glass" surfaces that let the starfield show through
GLASS = "rgba(16, 16, 20, 0.82)"
GLASS_DARK = "rgba(13, 13, 17, 0.88)"
GLASS_CARD = "rgba(26, 26, 32, 0.86)"
GLASS_BORDER = "rgba(255, 255, 255, 0.07)"

QSS = f"""
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


# fonts picked in Settings › General › Appearance ("" = system font); Audiowide and Orbitron ship with Nebula
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


def font(role: str, point_size: float = 10) -> QFont:
    """The configured font for ``role`` (ui | display | sidebar)."""
    f = QFont(_base_font) if _base_font is not None else QFont()
    if FONTS.get(role):
        f.setFamily(FONTS[role])
    f.setPointSizeF(point_size)
    return f


def set_fonts(app: QApplication, ui: str = "", display: str = "", sidebar: str = "") -> None:
    FONTS.update(ui=ui or "", display=display or "", sidebar=sidebar or "")
    load_fonts()
    app.setFont(font("ui", _base_font.pointSizeF() if _base_font is not None else 10))
    rules = []
    if FONTS["display"]:
        rules.append(f'#PageTitle {{ font-family: "{FONTS["display"]}"; font-weight: 400; }}')
    if FONTS["sidebar"]:
        rules.append(f'QPushButton#SidebarButton QLabel {{ font-family: "{FONTS["sidebar"]}"; font-size: 9pt; }}')
    app.setStyleSheet(QSS + "\n".join(rules))


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
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
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(SURFACE2))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(TEXT_FAINT))
    pal.setColor(QPalette.ColorRole.Link, QColor(ACCENT_HOVER))
    app.setPalette(pal)
    f = app.font()
    if f.pointSize() < 10:
        f.setPointSize(10)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    global _base_font
    _base_font = QFont(f)
    set_fonts(app, FONTS["ui"], FONTS["display"], FONTS["sidebar"])
