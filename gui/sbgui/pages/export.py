import logging, os, shutil
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton, QListWidgetItem,
                               QCheckBox, QLineEdit, QTextBrowser, QFileDialog, QMessageBox)
from ..widgets import Card, label, chip, listw, Responsive, ElideLabel
from ..legacy import load_results
from ..model import host_label, compute_tests
from ..report import build_html
from .. import theme as T

log = logging.getLogger("sbench-gui")


class ExportPage(QWidget):
    support_requested = Signal(list, bool)   # results ([] = --all), anonymize

    def __init__(self, result_files, out_dir):
        super().__init__(); self.setObjectName("page")
        self.results = load_results(result_files)      # unreadable/legacy files: backed up + warning, never a crash
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
        self.fmt_desc = {}
        fmts = (("JSON", "sb-result/1 unverändert", True), ("CSV flat", "sbench export --style flat · inkl. CPU/RAM, partial/stopped_in", True),
                ("CSV Iometer", "sbench export --style iometer · nur Storage", False), ("HTML-Bericht", "eigenständig, Diagramme als PNG", True),
                ("PDF-Bericht", "A4 aus demselben HTML", False))
        self.fmt = {}
        for i, (n, d, on) in enumerate(fmts):
            cb = QCheckBox(n); cb.setChecked(on); g.addWidget(cb, i, 0); self.fmt[n] = cb
            dl = ElideLabel(d, "dim"); g.addWidget(dl, i, 1); self.fmt_desc[n] = dl
        g.setColumnStretch(1, 1)
        fc.add(g)
        # contract 1.3: the Iometer CSV omits cpu/memory tests by design (Iometer has no such columns). The hint replaces the
        # Iometer description in place: an extra line would squeeze the support card at 1280x720
        self.iom_hint = self.fmt_desc["CSV Iometer"]
        self.fmt["CSV Iometer"].toggled.connect(self._preview)
        mid.addWidget(fc)
        oc = Card("Optionen")
        g2 = QGridLayout(); g2.setHorizontalSpacing(16); g2.setVerticalSpacing(10)
        for i, (n, on) in enumerate((("Zeitreihen", True), ("Latenz-Histogramm", True),
                                     ("Systeminfo (meta)", True), ("Anonymisieren", False))):
            cb = QCheckBox(n); cb.setChecked(on); g2.addWidget(cb, i // 2, i % 2)
            if n == "Zeitreihen":
                self.ts_cb = cb
            if n == "Anonymisieren":
                cb.setToolTip("Hostnamen, Benutzer in Pfaden, IP-/MAC-Adressen, Seriennummern im Bericht durch Pseudonyme ersetzen")
        oc.add(g2)
        row = QHBoxLayout(); row.addWidget(label("Zielordner", "muted")); self.out = QLineEdit(out_dir); row.addWidget(self.out, 1)
        bdir = QPushButton("…"); bdir.setToolTip("Zielordner wählen"); bdir.clicked.connect(self.choose_dir); row.addWidget(bdir); oc.add(row)
        row2 = QHBoxLayout(); row2.addWidget(label("Dateiname", "muted")); self.name_tpl = n = QLineEdit("{host}_{run_id}_{datum}"); row2.addWidget(n, 1)
        row2.addStretch(0); b = QPushButton("Exportieren"); b.setObjectName("primary"); b.setMinimumWidth(140); b.clicked.connect(self.export); row2.addWidget(b)
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

    def choose_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Zielordner für den Export", self.out.text().strip() or os.path.expanduser("~"))
        if d:
            self.out.setText(os.path.normpath(d))
        return d

    def _name(self, f, r):
        import re
        m = r.get("meta", {})
        vals = {"host": m.get("host", "host"), "run_id": r.get("run_id") or os.path.splitext(os.path.basename(f))[0],
                "datum": (r.get("started_utc") or "")[:10] or "datum"}
        try:
            n = self.name_tpl.text().strip().format(**vals) or vals["run_id"]
        except (KeyError, ValueError, IndexError):
            n = "{host}_{run_id}_{datum}".format(**vals)
        return re.sub(r'[\\/:*?"<>|]+', "-", n)

    def export(self):
        """JSON copy, CSV via `sbench export`, HTML/PDF report from report.build_html; result always shown in a message."""
        sel = [(f, r) for i, (f, r) in enumerate(self.results) if self.list.item(i).checkState() == Qt.Checked]
        fmts = [n for n, cb in self.fmt.items() if cb.isChecked()]
        if not sel or not fmts:
            QMessageBox.information(self, "Export", "Bitte links mindestens ein Ergebnis und oben mindestens ein Format auswählen.")
            return []
        out = self.out.text().strip()
        try:
            os.makedirs(out, exist_ok=True)
        except OSError as e:
            QMessageBox.critical(self, "Export", f"Zielordner {out} nicht verwendbar: {e}"); return []
        done, failed = [], []
        from .. import sbench
        for f, r in sel:
            base = os.path.join(out, self._name(f, r))
            for n in fmts:
                try:
                    if n == "JSON":
                        shutil.copyfile(f, base + ".json"); done.append(base + ".json")
                    elif n.startswith("CSV"):
                        style = "iometer" if "Iometer" in n else "flat"; dst = f"{base}_{style}.csv"
                        code, _out = sbench.run_sync(["export", f, "--csv", dst, "--style", style], 30000)
                        (done.append(dst) if code == 0 and os.path.exists(dst) else failed.append(f"{os.path.basename(dst)}: sbench export Exit {code}"))
                    elif n == "HTML-Bericht":
                        with open(base + ".html", "w", encoding="utf-8") as fh:
                            fh.write(build_html([r])); done.append(base + ".html")
                    elif n == "PDF-Bericht":
                        from PySide6.QtGui import QPdfWriter, QTextDocument, QPageSize
                        doc = QTextDocument(); doc.setHtml(build_html([r]))
                        w = QPdfWriter(base + ".pdf"); w.setPageSize(QPageSize(QPageSize.A4)); doc.print_(w); del w; done.append(base + ".pdf")
                except Exception as e:  # noqa: BLE001 -- one failing file must not hide the others
                    log.error("export %s %s: %s", n, f, e); failed.append(f"{os.path.basename(base)} ({n}): {type(e).__name__}: {e}")
        msg = f"{len(done)} Datei(en) nach {out} geschrieben." + (("\n\nFehlgeschlagen:\n" + "\n".join(failed)) if failed else "")
        (QMessageBox.warning if failed else QMessageBox.information)(self, "Export", msg)
        return done

    def selected_files(self):
        return [self.results[i][0] for i in range(self.list.count()) if self.list.item(i).checkState() == Qt.Checked]

    def _preview(self, *_):
        sel = [r for i, (f, r) in enumerate(self.results) if self.list.item(i).checkState() == Qt.Checked]
        self.preview.setHtml(build_html(sel, compact=True))
        # contract 1.3: timeseries has one point per tick (--tick-ms, result field tick_ms), not a fixed 1 s
        ticks = sorted({r.get("tick_ms") for r in sel if r.get("tick_ms")})
        txt = "Zeitreihen (je Tick: " + ", ".join(f"{t} ms" for t in ticks) + ")" if ticks else "Zeitreihen (je Tick)"
        self.ts_cb.setText(txt)
        self.ts_cb.setToolTip("Ein Punkt je tick-Event (--tick-ms); Kerne < 1.3 ohne tick_ms: 1 s")
        ncomp = sum(len(compute_tests(r)) for r in sel)
        full = (f"Das Iometer-CSV enthält nur Storage-Tests: {ncomp} CPU/RAM-Test(s) der Auswahl fehlen dort (gewollt, Iometer kennt "
                "keine solchen Spalten). Für CPU/RAM CSV flat, JSON oder den HTML-Bericht nutzen.")
        on = self.fmt["CSV Iometer"].isChecked()
        self.iom_hint.setText(f"⚠ nur Storage · {ncomp} CPU/RAM-Test(s) fehlen (gewollt)" if ncomp and on else
                              ("sbench export --style iometer · nur Storage (CPU/RAM fehlen gewollt)" if ncomp else
                               "sbench export --style iometer · nur Storage"))
        self.iom_hint.setStyleSheet(f"color: {T.AMBER};" if ncomp and on else "")
        self.iom_hint.setToolTip(full if ncomp else "Iometer-kompatibles CSV; CPU/RAM-Tests sind dort nicht enthalten")
