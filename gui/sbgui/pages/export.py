import json, os
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton, QListWidgetItem,
                               QCheckBox, QLineEdit, QTextBrowser)
from ..widgets import Card, label, chip, listw, Responsive
from ..model import host_label, check_schema
from ..report import build_html


class ExportPage(QWidget):
    support_requested = Signal(list, bool)   # results ([] = --all), anonymize

    def __init__(self, result_files, out_dir):
        super().__init__(); self.setObjectName("page")
        self.results = [(f, check_schema(json.load(open(f, encoding="utf-8")), "sb-result/1")) for f in result_files]
        v = QVBoxLayout(self); v.setContentsMargins(28, 24, 28, 24); v.setSpacing(16)
        hdr = QHBoxLayout(); tv = QVBoxLayout(); tv.setSpacing(2)
        tv.addWidget(label("Export", "h1"))
        tv.addWidget(label("Ergebnisse als JSON, CSV (über sbench export) oder als HTML-/PDF-Bericht speichern", "muted"))
        hdr.addLayout(tv); hdr.addStretch(1)
        v.addLayout(hdr)

        left = Card("Ergebnisse", "Auswahl für Export und Bericht"); left.setMinimumHeight(170)
        self.list = listw(); left.add(self.list, 1)
        for i, (f, r) in enumerate(self.results):
            it = QListWidgetItem(f"{host_label(r['meta'])}\n{os.path.basename(f)}")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable); it.setCheckState(Qt.Checked if i < 2 else Qt.Unchecked); self.list.addItem(it)
        self.list.itemChanged.connect(self._preview)
        bsel = QPushButton("Auswahl → Support-Paket"); bsel.clicked.connect(lambda: self.support_requested.emit(self.selected_files(), self.anon.isChecked()))
        left.add(bsel)

        mid_w = QWidget(); mid = QVBoxLayout(mid_w); mid.setContentsMargins(0, 0, 0, 0); mid.setSpacing(14)
        fc = Card("Formate")
        g = QGridLayout(); g.setHorizontalSpacing(16); g.setVerticalSpacing(10)
        fmts = (("JSON", "sb-result/1 unverändert", True), ("CSV flat", "sbench export --style flat", True),
                ("CSV Iometer", "sbench export --style iometer", False), ("HTML-Bericht", "eigenständig, Diagramme als PNG", True),
                ("PDF-Bericht", "A4 aus demselben HTML", False))
        for i, (n, d, on) in enumerate(fmts):
            cb = QCheckBox(n); cb.setChecked(on); g.addWidget(cb, i, 0); g.addWidget(label(d, "dim"), i, 1)
        g.setColumnStretch(1, 1)
        fc.add(g); mid.addWidget(fc)
        oc = Card("Optionen")
        g2 = QGridLayout(); g2.setHorizontalSpacing(16); g2.setVerticalSpacing(10)
        for i, (n, on) in enumerate((("Zeitreihen (1 s)", True), ("Latenz-Histogramm", True),
                                     ("Systeminfo (meta)", True), ("Anonymisieren", False))):
            cb = QCheckBox(n); cb.setChecked(on); g2.addWidget(cb, i // 2, i % 2)
            if n == "Anonymisieren":
                cb.setToolTip("Hostnamen, Benutzer in Pfaden, IP-/MAC-Adressen, Seriennummern im Bericht durch Pseudonyme ersetzen")
        oc.add(g2)
        row = QHBoxLayout(); row.addWidget(label("Zielordner", "muted")); self.out = QLineEdit(out_dir); row.addWidget(self.out, 1)
        row.addWidget(QPushButton("…")); oc.add(row)
        row2 = QHBoxLayout(); row2.addWidget(label("Dateiname", "muted")); n = QLineEdit("{host}_{run_id}_{datum}"); row2.addWidget(n, 1)
        row2.addStretch(0); b = QPushButton("Exportieren"); b.setObjectName("primary"); b.setMinimumWidth(140); row2.addWidget(b)
        oc.add(row2); mid.addWidget(oc)

        sc = Card("Support-Paket", "Alles, was der Support zur Analyse braucht, in einer ZIP-Datei")
        sc.setStyleSheet("QFrame#card { border: 1px solid rgba(34,211,238,0.45); }")
        txt = label("ZIP mit manifest.json (SHA-256 je Datei), info.json, results/, runs/ und logs/ aus dem Datenverzeichnis "
                    "(sbench bundle --all) plus GUI-Logs und Profile unter extra/. Enthält keine Inhalte der Testziele.", "muted", wrap=True)
        sc.add(txt)
        self.anon = QCheckBox("Anonymisieren (Host, Benutzer, IP/MAC, Seriennr. → Pseudonyme)")
        sc.add(self.anon)
        bs = QPushButton("⇪  Ergebnisse für Support exportieren"); bs.setObjectName("primary"); bs.setMinimumHeight(40)
        bs.clicked.connect(lambda: self.support_requested.emit([], self.anon.isChecked())); sc.add(bs)
        r3 = QHBoxLayout(); r3.addWidget(label("Nur ausgewählte Läufe: Button links (--results)", "dim")); r3.addStretch(1)
        r3.addWidget(chip("sb-bundle/1", "chip")); sc.add(r3)
        mid.addWidget(sc); mid.addStretch(1)

        pc = Card("Vorschau HTML-Bericht", right=[chip("A4", "chip")]); pc.setMinimumHeight(260)
        self.preview = QTextBrowser(); self.preview.setStyleSheet("QTextBrowser { background: #f7f9fb; border-radius: 8px; border: none; color: #1d2733; padding: 8px; }")
        pc.add(self.preview, 1)
        inner = Responsive([(mid_w, 1, None), (pc, 0, 330)], breakpoint=880)
        v.addWidget(Responsive([(left, 0, 220), (inner, 1, None)], breakpoint=860), 1)
        self._preview()

    def selected_files(self):
        return [self.results[i][0] for i in range(self.list.count()) if self.list.item(i).checkState() == Qt.Checked]

    def _preview(self, *_):
        sel = [r for i, (f, r) in enumerate(self.results) if self.list.item(i).checkState() == Qt.Checked]
        self.preview.setHtml(build_html(sel, compact=True))
