from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QFrame, QLabel, QVBoxLayout, QHBoxLayout, QWidget, QTableWidget, QTableWidgetItem,
                               QHeaderView, QAbstractItemView, QSizePolicy)
import pyqtgraph as pg
from . import theme as T


def label(text, name=None, wrap=False):
    l = QLabel(text)
    if name:
        l.setObjectName(name)
    l.setWordWrap(wrap)
    return l


def chip(text, kind="chip"):
    l = QLabel(text); l.setObjectName(kind); l.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return l


class Card(QFrame):
    def __init__(self, title=None, subtitle=None, right=None, margins=16):
        super().__init__(); self.setObjectName("card")
        self.lay = QVBoxLayout(self); self.lay.setContentsMargins(margins, margins - 2, margins, margins); self.lay.setSpacing(10)
        if title:
            h = QHBoxLayout(); h.setSpacing(8)
            v = QVBoxLayout(); v.setSpacing(1); self.title_label = label(title, "h2"); v.addWidget(self.title_label)
            self.subtitle_label = label(subtitle or "", "muted"); v.addWidget(self.subtitle_label)
            self.subtitle_label.setVisible(bool(subtitle))
            h.addLayout(v); h.addStretch(1)
            for w in (right or []):
                h.addWidget(w)
            self.lay.addLayout(h)

    def add(self, w, stretch=0):
        if isinstance(w, QWidget):
            self.lay.addWidget(w, stretch)
        else:
            self.lay.addLayout(w, stretch)
        return w


class Kpi(QFrame):
    def __init__(self, title, unit, color=T.ACCENT):
        super().__init__(); self.setObjectName("kpi")
        self.setStyleSheet(f"QFrame#kpi {{ border-top: 2px solid {color}; }}")
        v = QVBoxLayout(self); v.setContentsMargins(16, 12, 16, 12); v.setSpacing(2)
        self.title_lbl = label(title.upper(), "kpiLabel"); v.addWidget(self.title_lbl)
        h = QHBoxLayout(); h.setSpacing(6)
        self.value = label("–", "kpiValue"); h.addWidget(self.value, 0, Qt.AlignBottom)
        self.unit = label(unit, "kpiUnit"); h.addWidget(self.unit, 0, Qt.AlignBottom); h.addStretch(1)
        v.addLayout(h)
        self.sub = label(" ", "dim"); v.addWidget(self.sub)
        # long sub texts (cpu/memory facts) must never widen the KPI row (1280x720 / RDP): clip instead of growing
        self.sub.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred); self.sub.setMinimumWidth(0)

    def set_title(self, title, unit):
        self.title_lbl.setText(title.upper()); self.unit.setText(unit)

    def set(self, value, sub=None):
        self.value.setText(value)
        if sub is not None:
            self.sub.setText(sub); self.sub.setToolTip(sub.strip())


def table(headers, rows=(), stretch_col=0, mono_cols=()):
    t = QTableWidget(0, len(headers)); t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False); t.setShowGrid(False); t.setFocusPolicy(Qt.NoFocus)
    t.setSelectionBehavior(QAbstractItemView.SelectRows); t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    if stretch_col is not None:
        t.horizontalHeader().setSectionResizeMode(stretch_col, QHeaderView.Stretch)
    t.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    t.verticalHeader().setDefaultSectionSize(32)
    t.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded); t.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    t.setWordWrap(False); t.setTextElideMode(Qt.ElideMiddle)
    t._mono = set(mono_cols)
    for r in rows:
        add_row(t, r)
    return t


def add_row(t, values, colors=None):
    r = t.rowCount(); t.insertRow(r)
    for c, v in enumerate(values):
        it = QTableWidgetItem(str(v))
        if c in getattr(t, "_mono", ()):
            f = QFont(T.MONO_FONT); f.setPointSizeF(9); it.setFont(f)
            it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        if colors and colors.get(c):
            it.setForeground(QColor(colors[c]))
        t.setItem(r, c, it)
    return r


def si(v):
    a = abs(v)
    if a >= 1e6:
        return f"{v / 1e6:.3g}M"
    if a >= 1e3:
        return f"{v / 1e3:.3g}k"
    return f"{v:.4g}"


class SIAxis(pg.AxisItem):
    """Tick labels like 250k / 1.2M instead of 1e+06."""
    def tickStrings(self, values, scale, spacing):
        return [si(v * scale) for v in values]


def plot(title=None, ylabel=None, left_width=58):
    w = pg.PlotWidget(background=T.CARD, axisItems={"left": SIAxis("left")})
    w.setMinimumHeight(120)
    pi = w.getPlotItem(); pi.showGrid(x=True, y=True, alpha=0.10); pi.setMenuEnabled(False); w.setMouseEnabled(False, False)
    pi.hideButtons()
    f = QFont(T.UI_FONT); f.setPointSizeF(8.5)
    for ax in ("left", "bottom"):
        a = pi.getAxis(ax); a.setPen(pg.mkPen(T.BORDER)); a.setTextPen(pg.mkPen(T.MUTED)); a.setStyle(tickFont=f, tickLength=0, tickTextOffset=6)
    pi.getAxis("left").setWidth(left_width)
    if ylabel:
        pi.getAxis("left").enableAutoSIPrefix(False)
    return w


def fmt_num(v, d=0):
    if v is None:
        return "–"
    if abs(v) >= 1e6:
        return f"{v/1e6:.2f} M"
    if abs(v) >= 1e4:
        return f"{v/1e3:.1f} k"
    return f"{v:,.{d}f}".replace(",", " ")


def listw():
    from PySide6.QtWidgets import QListWidget
    l = QListWidget(); l.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); l.setTextElideMode(Qt.ElideRight); l.setWordWrap(True)
    return l


def short_target(path):
    r"""D:\sbench\testfile.dat -> D:\…\testfile.dat ; \\.\PhysicalDrive2 -> PhysicalDrive2"""
    if path.startswith("\\\\.\\"):
        return path[4:]
    parts = path.split("\\")
    return path if len(parts) <= 2 else parts[0] + "\\…\\" + parts[-1]


def hline():
    f = QFrame(); f.setObjectName("hline"); f.setFixedHeight(1); return f


# ---------------------------------------------------------------- icons (drawn, no icon font needed; crisp at any DPI)
def nav_icon(kind, color, size=18):
    from PySide6.QtGui import QIcon, QPixmap, QPainter, QPen, QPainterPath
    from PySide6.QtCore import QRectF, QPointF
    icon = QIcon()
    for dpr in (1, 1.25, 1.5, 2):
        pm = QPixmap(int(size * dpr), int(size * dpr)); pm.setDevicePixelRatio(dpr); pm.fill(Qt.transparent)
        p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(color)); pen.setWidthF(1.6); pen.setCapStyle(Qt.RoundCap); pen.setJoinStyle(Qt.RoundJoin); p.setPen(pen)
        s = size
        if kind == "dashboard":
            for x, y, w, h in ((2, 2, 6, 8), (10, 2, 6, 5), (10, 9, 6, 7), (2, 12, 6, 4)):
                p.drawRoundedRect(QRectF(x, y, w, h), 1.5, 1.5)
        elif kind == "profiles":
            for i, y in enumerate((4, 9, 14)):
                p.drawLine(QPointF(2, y), QPointF(16, y)); kx = (11, 5, 9)[i]
                p.setBrush(QColor(color)); p.drawEllipse(QPointF(kx, y), 1.9, 1.9); p.setBrush(Qt.NoBrush)
        elif kind == "live":
            path = QPainterPath(QPointF(1.5, 10)); [path.lineTo(QPointF(x, y)) for x, y in ((5, 10), (7, 4), (10, 15), (12.5, 8), (14, 10), (16.5, 10))]
            p.drawPath(path)
        elif kind == "compare":
            for x, h in ((3, 7), (8, 12), (13, 9)):
                p.drawRoundedRect(QRectF(x, s - 2 - h, 3, h), 1, 1)
        elif kind == "export":
            p.drawLine(QPointF(9, 2), QPointF(9, 11)); p.drawLine(QPointF(5.5, 7.5), QPointF(9, 11)); p.drawLine(QPointF(12.5, 7.5), QPointF(9, 11))
            path = QPainterPath(QPointF(2.5, 11)); path.lineTo(2.5, 15.5); path.lineTo(15.5, 15.5); path.lineTo(15.5, 11); p.drawPath(path)
        elif kind == "results":
            p.drawRoundedRect(QRectF(3, 2, 12, 14), 2, 2)
            for y, x2 in ((6, 12), (9, 12), (12, 9)):
                p.drawLine(QPointF(6, y), QPointF(x2, y))
        elif kind == "support":
            p.drawRoundedRect(QRectF(2.5, 6, 13, 9.5), 1.5, 1.5); p.drawLine(QPointF(9, 1.5), QPointF(9, 10))
            p.drawLine(QPointF(6, 4.5), QPointF(9, 1.5)); p.drawLine(QPointF(12, 4.5), QPointF(9, 1.5))
        elif kind == "menu":
            for y in (4.5, 9, 13.5):
                p.drawLine(QPointF(3, y), QPointF(15, y))
        p.end(); icon.addPixmap(pm)
    return icon


# ---------------------------------------------------------------- responsive layout (RDP 1366x768)
class Responsive(QWidget):
    """Row of widgets that stacks vertically below `breakpoint` px of its own width.
    items: [(widget, stretch, side_width or None)] - side panels get a fixed width side-by-side, full width when stacked."""

    def __init__(self, items, breakpoint=1100, spacing=14):
        super().__init__()
        self.items = items; self.bp = breakpoint; self.stacked = None
        self.lay = QHBoxLayout(self); self.lay.setContentsMargins(0, 0, 0, 0); self.lay.setSpacing(spacing)
        for w, st, _ in items:
            self.lay.addWidget(w, st)
        self._apply(False)

    def _apply(self, stacked):
        if stacked == self.stacked:
            return
        self.stacked = stacked
        from PySide6.QtWidgets import QBoxLayout
        self.lay.setDirection(QBoxLayout.TopToBottom if stacked else QBoxLayout.LeftToRight)
        for w, st, side in self.items:
            if side:
                if stacked:
                    w.setMinimumWidth(0); w.setMaximumWidth(16777215)
                else:
                    w.setFixedWidth(side)
            self.lay.setStretchFactor(w, (0 if side else 1) if stacked else st)

    def _need(self):
        return sum(side or w.minimumSizeHint().width() for w, _, side in self.items) + self.lay.spacing() * (len(self.items) - 1)

    def minimumSizeHint(self):
        """Minimum = stacked width (so a scroll area / small window lets us stack instead of scrolling sideways)."""
        from PySide6.QtCore import QSize
        h = super().minimumSizeHint().height()
        return QSize(max(w.minimumSizeHint().width() for w, _, _ in self.items), h)

    def hasHeightForWidth(self):
        return False   # wrapped labels deep inside would otherwise drive the scroll-area height via preferred sizes

    def sizeHint(self):
        # keep preferred == minimum: inside a resizable QScrollArea a large hint (pyqtgraph, tables) would make
        # heightForWidth() overshoot the viewport and force needless vertical scrolling
        return self.minimumSizeHint()

    def resizeEvent(self, e):
        self._apply(e.size().width() < max(self.bp, self._need()))
        super().resizeEvent(e)


def scroll(page):
    """Wrap a page in a vertical scroll area (small screens / RDP); page keeps its minimum size."""
    from PySide6.QtWidgets import QScrollArea
    sa = QScrollArea(); sa.setWidgetResizable(True); sa.setFrameShape(QFrame.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded); sa.setWidget(page); sa.setObjectName("pageScroll")
    return sa


class WrapLabel(QLabel):
    """Word-wrapping label that reserves its real height (layouts without heightForWidth would clip it)."""
    def __init__(self, text, name=None):
        super().__init__(text); self.setWordWrap(True)
        if name:
            self.setObjectName(name)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        h = self.heightForWidth(self.width())
        if h > 0 and h != self.minimumHeight():
            self.setMinimumHeight(h)
