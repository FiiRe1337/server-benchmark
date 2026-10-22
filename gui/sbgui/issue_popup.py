"""'<n> Fehler' chip -> popup listing every wrong field (German field name + reason); click focuses the field."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QScrollArea, QWidget, QFrame
from .overlay import Overlay
from .widgets import label, chip, WrapLabel, ClickFrame
from .issues import entries


class IssuePopup(Overlay):
    focus_requested = Signal(str)      # JSON pointer of the clicked issue

    def __init__(self, parent, res, job):
        rows = entries(res, job)
        ne = sum(1 for r in rows if r[0]); nw = len(rows) - ne
        title = (f"{ne} Fehler" + (f", {nw} Warnung{'en' if nw != 1 else ''}" if nw else "")) if ne else (
            f"{nw} Warnung{'en' if nw != 1 else ''}" if nw else "Keine Fehler, keine Warnungen")
        super().__init__(parent, f"Prüfung (sbench validate): {title}",
                         "Eintrag anklicken, um zum betroffenen Feld zu springen." if rows else "Der Job ist gültig.",
                         "danger" if ne else ("warn" if nw else "info"), 640)
        self.rows = []
        inner = QWidget(); iv = QVBoxLayout(inner); iv.setContentsMargins(0, 0, 0, 0); iv.setSpacing(6)
        for is_err, field, why, ptr, code in rows:
            box = ClickFrame(); box.setObjectName("dangerBox" if is_err else "warnBox")
            bl = QVBoxLayout(box); bl.setContentsMargins(10, 7, 10, 7); bl.setSpacing(2)
            h = QHBoxLayout(); h.addWidget(label(field, "h2" if is_err else "muted"), 1)
            h.addWidget(chip(code, "chipErr" if is_err else "chipWarn"), 0, Qt.AlignTop); bl.addLayout(h)
            bl.addWidget(WrapLabel(why, "muted")); bl.addWidget(label(ptr or "/", "dim"))
            box.setToolTip(f"{field}\n{why}")
            box.clicked.connect(lambda p=ptr: self._go(p)); iv.addWidget(box); self.rows.append((box, ptr))
        iv.addStretch(1)
        sa = QScrollArea(); sa.setWidgetResizable(True); sa.setFrameShape(QFrame.NoFrame); sa.setWidget(inner)
        sa.setMinimumHeight(min(420, 90 * max(1, len(rows)))); self.add(sa)
        self.button("Schließen")

    def _go(self, ptr):
        self.close_accept(ptr); self.focus_requested.emit(ptr)
