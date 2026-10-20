"""In-window modal overlays (child widgets: they show up in widget.grab() screenshots and scale with the window)."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget, QFrame, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit, QCheckBox, QRadioButton
from .widgets import label, chip, short_target
from .model import spec_label
from . import theme as T


class Overlay(QWidget):
    accepted = Signal(object)
    rejected = Signal()

    def __init__(self, parent, title, subtitle=None, kind="info", width=560):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.card = QFrame(self); self.card.setObjectName({"danger": "dlgDanger", "warn": "dlgWarn"}.get(kind, "dlg"))
        self.card.setFixedWidth(width)
        self.v = QVBoxLayout(self.card); self.v.setContentsMargins(24, 20, 24, 20); self.v.setSpacing(12)
        h = QHBoxLayout(); h.setSpacing(10)
        ic = label({"danger": "⚠", "warn": "⛨", "info": "↻"}.get(kind, "•"))
        ic.setStyleSheet(f"color:{ {'danger': T.RED, 'warn': T.AMBER}.get(kind, T.ACCENT)}; font-size:20px; font-weight:700;")
        h.addWidget(ic, 0, Qt.AlignTop)
        tv = QVBoxLayout(); tv.setSpacing(2); tv.addWidget(label(title, "h2"))
        if subtitle:
            tv.addWidget(label(subtitle, "muted", wrap=True))
        h.addLayout(tv, 1); self.v.addLayout(h)
        self.buttons = QHBoxLayout(); self.buttons.setSpacing(8); self.buttons.addStretch(1)
        self.hide()

    def add(self, w):
        (self.v.addWidget if isinstance(w, QWidget) else self.v.addLayout)(w); return w

    def button(self, text, kind=None, slot=None):
        b = QPushButton(text)
        if kind:
            b.setObjectName(kind)
        b.clicked.connect(slot or self.close_reject); self.buttons.addWidget(b); return b

    def open(self):
        self.v.addLayout(self.buttons)
        self.setGeometry(self.parentWidget().rect()); self.card.adjustSize(); self._center(); self.show(); self.raise_()

    def _center(self):
        self.card.move((self.width() - self.card.width()) // 2, max(16, (self.height() - self.card.height()) // 2))

    def resizeEvent(self, e):
        self._center(); super().resizeEvent(e)

    def paintEvent(self, e):
        p = QPainter(self); p.fillRect(self.rect(), QColor(4, 8, 12, 170))

    def close_reject(self):
        self.hide(); self.rejected.emit(); self.deleteLater()

    def close_accept(self, value=None):
        self.hide(); self.accepted.emit(value); self.deleteLater()


class DestructiveConfirm(Overlay):
    """Contract 1.1: write tests on volume/physical need allow_destructive in the target AND run --allow-destructive.
    The GUI sets both only after the user typed each target's name."""

    def __init__(self, parent, job, targets):
        super().__init__(parent, "Schreibtest zerstört Daten",
                         "Diese Ziele werden direkt (ohne Dateisystem) beschrieben. Alle Daten darauf gehen verloren, "
                         "Windows-Volumes können unbrauchbar werden. Zum Bestätigen den Namen jedes Ziels eintippen.", "danger", 600)
        self.edits = []
        for ti, k, tg in targets:
            t = job["tests"][ti]
            box = QFrame(); box.setObjectName("dangerBox"); bv = QVBoxLayout(box); bv.setContentsMargins(12, 10, 12, 10); bv.setSpacing(6)
            r = QHBoxLayout(); r.addWidget(label(tg["path"], "mono")); r.addStretch(1)
            r.addWidget(chip({"volume": "Volume", "physical": "Physische Disk"}[tg["kind"]], "chipErr")); bv.addLayout(r)
            wr = [s for s in t["access_specs"] if s["read_pct"] < 100]
            bv.addWidget(label(f"Test {ti + 1}: " + ", ".join(f"{spec_label(s)} ({100 - s['read_pct']} % Schreiben, {s['weight']} %)" for s in wr), "muted", wrap=True))
            name = short_target(tg["path"])
            e = QLineEdit(); e.setPlaceholderText(f"{name} eintippen"); bv.addWidget(e)
            e.textChanged.connect(self._check); self.edits.append((e, name, (ti, k)))
            self.add(box)
        self.add(label("Setzt im Job allow_destructive: true für diese Ziele und startet sbench mit --allow-destructive.", "dim", wrap=True))
        self.button("Abbrechen")
        self.ok = self.button("Daten zerstören und starten", "dangerSolid", lambda: self.close_accept([x[2] for x in self.edits]))
        self.ok.setEnabled(False)

    def _check(self):
        self.ok.setEnabled(all(e.text().strip().lower() == n.lower() for e, n, _ in self.edits))


class ElevationOffer(Overlay):
    """validate warnings W_NEEDS_ADMIN -> offer UAC elevation of sbench.exe (only sbench, not the GUI)."""

    def __init__(self, parent, warnings, raw_needed):
        super().__init__(parent, "Administratorrechte erforderlich",
                         "sbench.exe wird per UAC (runas) erhöht gestartet; die GUI bleibt normal. Der Fortschritt kommt über "
                         "eine Named Pipe, die nur für Ihr Konto und Administratoren freigegeben ist.", "warn", 580)
        seen = set()
        for w in warnings:
            if w["msg"] in seen:
                continue
            seen.add(w["msg"])
            r = QHBoxLayout(); r.setSpacing(8); r.addWidget(chip(w["code"], "chipWarn"), 0, Qt.AlignTop)
            r.addWidget(label(w["msg"], "muted", wrap=True), 1); self.add(r)
        self.button("Abbrechen")
        nb = self.button("Ohne Rechte starten", None, lambda: self.close_accept(False))
        if raw_needed:
            nb.setEnabled(False); nb.setToolTip("Raw-Ziele (Volume/Disk) enden ohne Rechte mit Exit-Code 4.")
        self.button("Als Administrator starten", "primary", lambda: self.close_accept(True))


class ReattachOffer(Overlay):
    """GUI start: `sbench list --running --json` found runs -> re-attach by tailing runs/<id>.jsonl."""

    def __init__(self, parent, runs):
        super().__init__(parent, "Laufender Test gefunden",
                         "sbench läuft weiter, auch wenn die GUI beendet wurde. Wieder verbinden liest die Fortschrittsdatei von "
                         "Anfang an und folgt ihr bis zum Ende.", "info", 560)
        self.radios = []
        for i, r in enumerate(runs):
            rb = QRadioButton(f"{r['run_id']}   ·   PID {r['pid']}   ·   gestartet {_local(r['started_utc'])}")
            rb.setChecked(i == 0); self.radios.append((rb, r)); self.add(rb)
            self.add(label(f"Ergebnis → {r['out']}", "dim"))
        self.button("Ignorieren")
        self.button("Stoppen", "danger", lambda: self.close_accept(("stop", self._sel())))
        self.button("Wieder verbinden", "primary", lambda: self.close_accept(("attach", self._sel())))

    def _sel(self):
        return next(r for rb, r in self.radios if rb.isChecked())


class SupportDialog(Overlay):
    """'Ergebnisse für Support exportieren': sbench bundle [--all|--results] --include <gui logs> --include <profiles> [--anonymize]."""

    def __init__(self, parent, n_selected=0, anonymize=False):
        super().__init__(parent, "Ergebnisse für Support exportieren",
                         "Erstellt ein ZIP mit manifest.json (SHA-256 je Datei), info.json, Ergebnissen, Fortschrittsdateien und "
                         "Logs. Inhalte der Testziele werden nie aufgenommen.", "info", 560)
        self.r_all = QRadioButton("Alle beendeten Läufe (--all)"); self.r_all.setChecked(not n_selected); self.add(self.r_all)
        self.r_sel = QRadioButton(f"Nur ausgewählte Ergebnisse ({n_selected}) (--results)"); self.r_sel.setChecked(bool(n_selected))
        self.r_sel.setEnabled(bool(n_selected)); self.add(self.r_sel)
        self.c_logs = QCheckBox("GUI-Logs beilegen (extra/logs/)"); self.c_logs.setChecked(True); self.add(self.c_logs)
        self.c_prof = QCheckBox("Testprofile beilegen (extra/profiles/)"); self.c_prof.setChecked(True); self.add(self.c_prof)
        self.c_anon = QCheckBox("Anonymisieren (Host, Benutzer, IP/MAC, Seriennr. → Pseudonyme)")
        self.c_anon.setChecked(anonymize); self.add(self.c_anon)
        self.button("Abbrechen")
        self.button("Speichern unter …", "primary", lambda: self.close_accept(
            {"selected": self.r_sel.isChecked(), "logs": self.c_logs.isChecked(), "profiles": self.c_prof.isChecked(),
             "anonymize": self.c_anon.isChecked()}))


class IcfImportResult(Overlay):
    def __init__(self, parent, icf_name, res, job):
        ok = res and res.get("ok")
        super().__init__(parent, f"Iometer-Profil importiert: {icf_name}" if ok else f"Import fehlgeschlagen: {icf_name}",
                         "sbench import-icf hat das Profil in sb-job/1 umgewandelt. Nicht übertragbare Einstellungen:" if ok else
                         "sbench import-icf konnte die Datei nicht umwandeln.", "warn", 600)
        if job:
            self.add(label(" · ".join(f"{t.get('name', t['type'])} ({len(t.get('access_specs', []))} Specs)" for t in job["tests"]), "muted", wrap=True))
        for w in (res or {}).get("warnings", []) + (res or {}).get("errors", []):
            r = QHBoxLayout(); r.setSpacing(8)
            r.addWidget(chip(w["code"], "chipErr" if w["code"].startswith("E_") else "chipWarn"), 0, Qt.AlignTop)
            r.addWidget(label(f"{w['path'] or '/'} · {w['msg']}", "muted", wrap=True), 1); self.add(r)
        self.button("Verwerfen")
        b = self.button("Als Profil übernehmen", "primary", lambda: self.close_accept(job)); b.setEnabled(bool(ok))


def _local(utc):
    import datetime as dt
    return dt.datetime.fromisoformat(utc.replace("Z", "+00:00")).astimezone().strftime("%d.%m. %H:%M:%S")
