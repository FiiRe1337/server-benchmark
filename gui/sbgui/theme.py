"""Dark theme: palette, QSS and pyqtgraph defaults."""
import os
from PySide6.QtGui import QFont, QFontDatabase, QColor, QPalette

BG = "#0b0f14"; SIDEBAR = "#0d1218"; CARD = "#121922"; CARD2 = "#17212c"; BORDER = "#1f2b38"
TEXT = "#e6edf3"; MUTED = "#8b98a9"; DIM = "#5d6b7c"
ACCENT = "#22d3ee"; TEAL = "#2dd4bf"; VIOLET = "#a78bfa"; AMBER = "#fbbf24"; RED = "#f87171"; GREEN = "#34d399"; PINK = "#f472b6"
SERIES = [ACCENT, VIOLET, AMBER, PINK, GREEN, TEAL]


def rgba(hex_color, a):
    h = hex_color.lstrip("#"); return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{a})"

UI_FONT = "Inter"; MONO_FONT = "JetBrains Mono"

QSS = f"""
* {{ font-family: '{UI_FONT}', 'Segoe UI', sans-serif; color: {TEXT}; }}
QMainWindow, QWidget#root {{ background: {BG}; }}
QWidget#page {{ background: {BG}; }}
QFrame#sidebar {{ background: {SIDEBAR}; border-right: 1px solid {BORDER}; }}
QLabel#brand {{ font-size: 17px; font-weight: 700; }}
QLabel#brandSub {{ color: {MUTED}; font-size: 11px; }}
QPushButton#nav {{ text-align: left; padding: 10px 14px; border: none; border-radius: 8px; color: {MUTED};
                   font-size: 13px; font-weight: 500; background: transparent; }}
QPushButton#nav:hover {{ background: {CARD}; color: {TEXT}; }}
QPushButton#nav:checked {{ background: rgba(34,211,238,0.10); color: {ACCENT}; font-weight: 600; }}
QFrame#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#kpi {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 12px; }}
QLabel#h1 {{ font-size: 22px; font-weight: 700; }}
QLabel#h2 {{ font-size: 14px; font-weight: 600; }}
QLabel#muted {{ color: {MUTED}; font-size: 12px; }}
QLabel#dim {{ color: {DIM}; font-size: 11px; }}
QLabel#kpiLabel {{ color: {MUTED}; font-size: 11px; font-weight: 600; letter-spacing: 1px; }}
QLabel#kpiValue {{ font-family: '{MONO_FONT}'; font-size: 28px; font-weight: 600; }}
QLabel#kpiUnit {{ color: {MUTED}; font-size: 13px; }}
QLabel#big {{ font-family: '{MONO_FONT}'; font-size: 20px; font-weight: 600; }}
QLabel#chip {{ background: {CARD2}; border: 1px solid {BORDER}; border-radius: 10px; padding: 2px 9px; font-size: 11px; color: {MUTED}; }}
QLabel#chipOk {{ background: rgba(52,211,153,0.12); border: 1px solid rgba(52,211,153,0.35); border-radius: 10px; padding: 2px 9px; font-size: 11px; color: {GREEN}; }}
QLabel#chipAccent {{ background: rgba(34,211,238,0.12); border: 1px solid rgba(34,211,238,0.35); border-radius: 10px; padding: 2px 9px; font-size: 11px; color: {ACCENT}; }}
QLabel#chipWarn {{ background: rgba(251,191,36,0.12); border: 1px solid rgba(251,191,36,0.35); border-radius: 10px; padding: 2px 9px; font-size: 11px; color: {AMBER}; }}
QLabel#chipErr {{ background: rgba(248,113,113,0.12); border: 1px solid rgba(248,113,113,0.4); border-radius: 10px; padding: 2px 9px; font-size: 11px; color: {RED}; }}
QPushButton {{ background: {CARD2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 14px; font-size: 12px; font-weight: 500; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton#primary {{ background: {ACCENT}; color: #04232a; border: none; font-weight: 700; }}
QPushButton#danger {{ background: rgba(248,113,113,0.14); color: {RED}; border: 1px solid rgba(248,113,113,0.45); font-weight: 700; }}
QPushButton#support {{ background: rgba(34,211,238,0.08); color: {ACCENT}; border: 1px solid rgba(34,211,238,0.55); font-weight: 600; padding: 9px 10px; }}
QPushButton#quick {{ text-align: left; padding: 12px 14px; background: {CARD2}; font-size: 12px; }}
QLineEdit, QSpinBox, QComboBox, QDoubleSpinBox {{ background: {BG}; border: 1px solid {BORDER}; border-radius: 7px; padding: 5px 8px; font-size: 12px;
               selection-background-color: {ACCENT}; selection-color: #04232a; }}
QLineEdit[invalid="true"], QSpinBox[invalid="true"], QComboBox[invalid="true"] {{ border: 1px solid {RED}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0px; border: none; }}
QTableWidget {{ background: transparent; border: none; gridline-color: {BORDER}; font-size: 12px; alternate-background-color: {CARD2}; }}
QTableWidget::item {{ padding: 4px 6px; border-bottom: 1px solid {BORDER}; }}
QTableWidget::item:selected {{ background: rgba(34,211,238,0.14); color: {TEXT}; }}
QHeaderView::section {{ background: transparent; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
                        padding: 6px 6px; font-size: 11px; font-weight: 600; }}
QTableCornerButton::section {{ background: transparent; border: none; }}
QListWidget {{ background: transparent; border: none; font-size: 12px; outline: 0; }}
QListWidget::item {{ padding: 9px 10px; border-radius: 8px; margin: 1px 0; }}
QListWidget::item:selected {{ background: rgba(34,211,238,0.12); color: {ACCENT}; }}
QListWidget::indicator {{ width: 15px; height: 15px; border-radius: 4px; border: 1px solid {DIM}; background: {BG}; }}
QListWidget::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QProgressBar {{ background: {CARD2}; border: none; border-radius: 4px; height: 8px; }}
QProgressBar::chunk {{ border-radius: 4px; background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {TEAL}, stop:1 {ACCENT}); }}
QCheckBox {{ font-size: 12px; spacing: 8px; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border-radius: 4px; border: 1px solid {DIM}; background: {BG}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QPlainTextEdit, QTextEdit {{ background: {BG}; border: 1px solid {BORDER}; border-radius: 8px; font-family: '{MONO_FONT}'; font-size: 11px; color: #b6c2cf; }}
QScrollBar:vertical {{ background: transparent; width: 8px; }} QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QFrame#toast {{ background: {CARD2}; border: 1px solid rgba(52,211,153,0.55); border-radius: 12px; }}
QFrame#hline {{ background: {BORDER}; max-height: 1px; }}
QFrame#dlg {{ background: {CARD}; border: 1px solid {rgba(ACCENT, .45)}; border-radius: 14px; }}
QFrame#dlgWarn {{ background: {CARD}; border: 1px solid {rgba(AMBER, .55)}; border-radius: 14px; }}
QFrame#dlgDanger {{ background: {CARD}; border: 2px solid {rgba(RED, .75)}; border-radius: 14px; }}
QFrame#dangerBox {{ background: {rgba(RED, .07)}; border: 1px solid {rgba(RED, .35)}; border-radius: 9px; }}
QFrame#warnBox {{ background: {rgba(AMBER, .07)}; border: 1px solid {rgba(AMBER, .35)}; border-radius: 9px; }}
QPushButton#dangerSolid {{ background: {RED}; color: #2a0606; border: none; font-weight: 700; }}
QPushButton#dangerSolid:disabled {{ background: {rgba(RED, .22)}; color: {rgba(TEXT, .45)}; }}
QPushButton:disabled {{ color: {DIM}; border-color: {BORDER}; }}
QLabel#mono {{ font-family: '{MONO_FONT}'; font-size: 12px; font-weight: 600; }}
QRadioButton {{ font-size: 12px; spacing: 8px; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 7px; border: 1px solid {DIM}; background: {BG}; }}
QRadioButton::indicator:checked {{ background: {ACCENT}; border: 1px solid {ACCENT}; }}
QScrollArea#pageScroll {{ background: {BG}; border: none; }}
QScrollArea#pageScroll > QWidget > QWidget {{ background: {BG}; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; }} QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 4px; }}
QPushButton#navIcon {{ padding: 10px 0px; border: none; border-radius: 8px; background: transparent; }}
QPushButton#navIcon:hover {{ background: {CARD}; }}
QPushButton#navIcon:checked {{ background: rgba(34,211,238,0.10); }}
QPushButton#flat {{ background: transparent; border: none; padding: 6px; }}
QPushButton#flat:hover {{ background: {CARD}; }}
QMenu {{ background: {CARD2}; border: 1px solid {BORDER}; padding: 4px; font-size: 12px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }} QMenu::item:selected {{ background: rgba(34,211,238,0.14); color: {ACCENT}; }}
QToolTip {{ background: {CARD2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}
"""


def apply(app):
    here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")
    for f in ("Inter.ttf", "JetBrainsMono.ttf"):
        p = os.path.join(here, f)
        if os.path.exists(p):
            QFontDatabase.addApplicationFont(p)
    f = QFont(UI_FONT); f.setPointSizeF(9.5); f.setHintingPreference(QFont.PreferNoHinting)
    app.setFont(f)
    pal = app.palette()
    for role, c in ((QPalette.Window, BG), (QPalette.Base, BG), (QPalette.Text, TEXT), (QPalette.WindowText, TEXT),
                    (QPalette.Button, CARD2), (QPalette.ButtonText, TEXT), (QPalette.Highlight, ACCENT)):
        pal.setColor(role, QColor(c))
    app.setPalette(pal)
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)
    import pyqtgraph as pg
    # No OpenGL: raster only (RDP / VMs without GPU)
    pg.setConfigOptions(antialias=True, useOpenGL=False, background=CARD, foreground=MUTED)
