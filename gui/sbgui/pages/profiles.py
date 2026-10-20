import json, os, tempfile
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton, QListWidgetItem, QLineEdit, QSpinBox,
                               QComboBox, QPlainTextEdit, QAbstractItemView, QMenu, QFileDialog, QFrame)
from PySide6.QtGui import QColor
from ..widgets import Card, label, chip, table, add_row, listw, short_target, Responsive, WrapLabel
from ..model import spec_label, derived_spec_label, derived_test_label, check_schema, destructive_targets
from ..overlay import DestructiveConfirm, ElevationOffer, IcfImportResult
from .. import theme as T, sbench

SPEC_COLS = ["Name", "Gewicht", "Block", "% Lesen", "% Rand.", "Align", "Burst", "Delay ms"]
SPEC_KEYS = ["name", "weight", "block_size", "read_pct", "random_pct", "align", "burst_len", "delay_ms"]
KIND_TXT = {"file": "Datei", "volume": "Volume", "physical": "Physische Disk"}
DERIVED = Qt.UserRole + 1


class ProfileEditor(QWidget):
    run_requested = Signal(str, dict)  # job path, {"elevated": bool, "allow_destructive": bool}

    def __init__(self, profiles_dirs, elevated=False):
        super().__init__(); self.setObjectName("page")
        self.dirs = profiles_dirs; self.user_dir = profiles_dirs[-1]; self.job = None; self.elevated = elevated; self.last = None
        v = QVBoxLayout(self); v.setContentsMargins(28, 24, 28, 24); v.setSpacing(16)
        hdr = QHBoxLayout(); tv = QVBoxLayout(); tv.setSpacing(2)
        self.title = label("Testprofil: Storage", "h1", wrap=True); tv.addWidget(self.title)
        tv.addWidget(label("Iometer-artig: gewichtete Access Specs, Worker × Queue Depth, Warmup und Messdauer (sb-job/1)", "muted", wrap=True))
        hdr.addLayout(tv, 1)
        self.valid_chip = chip("nicht geprüft"); hdr.addWidget(self.valid_chip); hdr.addSpacing(6)
        bv = QPushButton("Validieren"); bv.clicked.connect(self.validate); hdr.addWidget(bv)
        bsave = QPushButton("Speichern"); bsave.clicked.connect(self.save); hdr.addWidget(bsave)
        br = QPushButton("▶  Test starten"); br.setObjectName("primary"); br.clicked.connect(self.start_flow); hdr.addWidget(br)
        v.addLayout(hdr)

        left = Card("Gespeicherte Profile"); left.setMinimumHeight(150)
        self.list = listw(); left.add(self.list, 1)
        lb = QHBoxLayout(); bn = QPushButton("+ Neu"); lb.addWidget(bn)
        bi = QPushButton("Import ▾"); m = QMenu(bi)
        m.addAction("Iometer-Profil importieren (.icf) …", self.import_icf_dialog)
        m.addAction("sb-job/1 importieren (.json) …", self.import_json_dialog)
        bi.setMenu(m); lb.addWidget(bi); left.add(lb)

        center = QWidget(); cv = QVBoxLayout(center); cv.setContentsMargins(0, 0, 0, 0); cv.setSpacing(14)
        tc = Card("Ziele", "Worker i → Ziel (i mod Anzahl)", right=[QPushButton("+ Datei"), QPushButton("+ Volume"), QPushButton("+ Disk")])
        self.targets = table(["Pfad", "Art", "Größe", "Worker", "Hinweis"], stretch_col=0)
        self.targets.setFixedHeight(32 * 3 + 36); tc.add(self.targets); cv.addWidget(tc)

        self.sum_chip = chip("Σ 100 %", "chipOk")
        sc = Card("Access Specifications", "Gewicht = Anteil an allen I/Os · Name optional",
                  right=[self.sum_chip, QPushButton("+ Spec"), QPushButton("Duplizieren")])
        self.specs = table(SPEC_COLS, stretch_col=0, mono_cols=(1, 2, 3, 4, 5, 6, 7)); self.specs.setMinimumHeight(130)
        self.specs.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.specs.itemChanged.connect(self._item_changed)
        sc.add(self.specs, 1)
        sc.add(label("Verteilung der I/Os", "dim"))
        self.mixbar = QHBoxLayout(); self.mixbar.setSpacing(3); sc.add(self.mixbar)
        cv.addWidget(sc, 1)

        right = QWidget(); rv = QVBoxLayout(right); rv.setContentsMargins(0, 0, 0, 0); rv.setSpacing(14)
        rc = Card("Ausführung")
        g = QGridLayout(); g.setHorizontalSpacing(12); g.setVerticalSpacing(8)
        self.name_edit = QLineEdit(); self.name_edit.textChanged.connect(self._changed)
        g.addWidget(label("Testname", "muted"), 0, 0); g.addWidget(self.name_edit, 0, 1)
        self.f = {}
        for i, (k, lab) in enumerate((("workers", "Worker"), ("queue_depth", "Queue Depth/Worker"), ("cache", "Cache-Modus"),
                                      ("warmup_s", "Warmup (s)"), ("duration_s", "Messdauer (s)"), ("tick", "Tick-Intervall (ms)")), 1):
            if k == "cache":
                w = QComboBox(); w.addItems(["unbuffered", "buffered", "writethrough"]); w.currentIndexChanged.connect(self._changed)
            else:
                w = QSpinBox(); w.setRange(1 if k != "warmup_s" else 0, 100000); w.setAlignment(Qt.AlignRight); w.valueChanged.connect(self._changed)
            self.f[k] = w; g.addWidget(label(lab, "muted"), i, 0); g.addWidget(w, i, 1)
        self.f["queue_depth"].setMaximum(1024); self.f["tick"].setRange(100, 10000); self.f["tick"].setValue(250)
        rc.add(g)
        self.outstanding = label("", "dim", wrap=True); rc.add(self.outstanding)
        rv.addWidget(rc)
        self.chk = Card("Prüfung (sbench validate)")
        self.issues = QVBoxLayout(); self.issues.setSpacing(6); self.chk.add(self.issues)
        self.issues.addWidget(label("Noch nicht geprüft.", "dim"))
        rv.addWidget(self.chk)
        jc = Card("Job-JSON (sb-job/1)")
        self.json = QPlainTextEdit(); self.json.setReadOnly(True); self.json.setMinimumHeight(90); jc.add(self.json, 1)
        rv.addWidget(jc, 1)

        inner = Responsive([(center, 1, None), (right, 0, 290)], breakpoint=880)
        v.addWidget(Responsive([(left, 0, 200), (inner, 1, None)], breakpoint=860), 1)
        self._load_list()

    # ------------------------------------------------------------------ profile list / import
    def _load_list(self, select=None):
        self.list.blockSignals(True); self.list.clear()
        sel = 0; n = 0
        for d in self.dirs:
            for f in sorted(x for x in os.listdir(d) if x.endswith(".json")) if os.path.isdir(d) else []:
                p = os.path.join(d, f)
                try:
                    job = json.load(open(p, encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                it = QListWidgetItem(job.get("name", f)); it.setData(Qt.UserRole, p); self.list.addItem(it)
                if (select and p == select) or (not select and "mix-60-40" in f):
                    sel = n
                n += 1
        self.list.blockSignals(False)
        if not getattr(self, "_list_connected", False):
            self._list_connected = True
            self.list.currentItemChanged.connect(lambda it, _: it and it.data(Qt.UserRole) and self.load(it.data(Qt.UserRole)))
        self.list.setCurrentRow(sel)
        if self.list.currentItem():
            self.load(self.list.currentItem().data(Qt.UserRole))

    def import_icf_dialog(self):
        p, _ = QFileDialog.getOpenFileName(self, "Iometer-Profil importieren", "", "Iometer-Konfiguration (*.icf)")
        if p:
            self.import_icf(p)

    def import_icf(self, icf_path):
        """M2: `sbench import-icf <f> --out <job>`; shows W_ICF_UNSUPPORTED warnings, user decides to keep it."""
        out = os.path.join(tempfile.gettempdir(), "sbench-gui-import.json")
        code, res = sbench.import_icf(icf_path, out)
        job = json.load(open(out, encoding="utf-8")) if code == 0 and os.path.exists(out) else None
        if res is None:
            res = {"ok": False, "errors": [{"path": "", "code": "E_IMPORT", "msg": f"sbench import-icf Exit {code} "
                                            f"({'Kern ohne import-icf? kommt in M2' if code == 2 else sbench.EXIT_TEXT.get(code, '?')})"}],
                   "warnings": []}
        dlg = IcfImportResult(self.window().centralWidget(), os.path.basename(icf_path), res, job)
        dlg.accepted.connect(lambda j: self._save_new(j)); dlg.open(); return dlg

    def import_json_dialog(self):
        p, _ = QFileDialog.getOpenFileName(self, "Profil importieren", "", "sb-job/1 (*.json)")
        if p:
            self._save_new(check_schema(json.load(open(p, encoding="utf-8")), "sb-job/1"))

    def _save_new(self, job):
        if not job:
            return
        base = "".join(c if c.isalnum() or c in "-_" else "-" for c in job.get("name", "profil")).strip("-").lower() or "profil"
        p = os.path.join(self.user_dir, base + ".json")
        json.dump(job, open(p, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        self._load_list(select=p)

    def save(self):
        if self.job:
            self._save_new(self.to_job())

    # ------------------------------------------------------------------ editor
    def load(self, path):
        self.job = check_schema(json.load(open(path, encoding="utf-8")), "sb-job/1")
        self.ti = next(i for i, t in enumerate(self.job["tests"]) if t["type"] == "storage")
        t = self.job["tests"][self.ti]
        self.title.setText(f"Testprofil: {self.job['name']}")
        self._fill_targets(t)
        self.specs.blockSignals(True); self.specs.setRowCount(0)
        for s in t["access_specs"]:
            r = add_row(self.specs, [s.get("name") or derived_spec_label(s)] + [s.get(k, "") for k in SPEC_KEYS[1:]])
            self._style_name(r, "name" not in s)
        self.specs.blockSignals(False)
        for w in list(self.f.values()) + [self.name_edit]:
            w.blockSignals(True)
        self.name_edit.setText(t.get("name", "")); self.name_edit.setCursorPosition(0); self.name_edit.setPlaceholderText(derived_test_label({**t, "name": None}))
        for k in ("workers", "queue_depth", "warmup_s", "duration_s"):
            self.f[k].setValue(int(t.get(k, 0 if k == "warmup_s" else 1)))
        self.f["cache"].setCurrentText(t.get("cache", "unbuffered"))
        for w in list(self.f.values()) + [self.name_edit]:
            w.blockSignals(False)
        self.last = None; self._show_issues(None)
        self._changed()

    def _fill_targets(self, t, issues=None):
        self.targets.setRowCount(0)
        nw = t.get("workers", 1)
        for k, tg in enumerate(t["targets"]):
            ws = ", ".join(str(i) for i in range(nw) if i % len(t["targets"]) == k) or "–"
            codes = {c for p, c in (issues or []) if p.startswith(f"/tests/{self.ti}/targets/{k}")}
            if "E_DESTRUCTIVE_NOT_ALLOWED" in codes:
                hint, col = "Freigabe fehlt", T.RED
            elif tg.get("allow_destructive"):
                hint, col = "destruktiv freigegeben", T.RED
            elif "W_NEEDS_ADMIN" in codes or tg["kind"] != "file":
                hint, col = ("Admin nötig" if tg["kind"] != "file" else "langsames Anlegen ohne Admin"), T.AMBER
            elif "W_LOW_DISK_SPACE" in codes:
                hint, col = "wenig Platz", T.AMBER
            else:
                hint, col = "bereit", T.GREEN
            r = add_row(self.targets, [short_target(tg["path"]), KIND_TXT[tg["kind"]], tg.get("file_size", "–"), ws, hint],
                        {4: col, 1: T.AMBER if tg["kind"] != "file" else None})
            self.targets.item(r, 0).setToolTip(tg["path"])

    def _style_name(self, r, derived):
        it = self.specs.item(r, 0); it.setData(DERIVED, derived)
        it.setForeground(QColor(T.DIM if derived else T.ACCENT))
        f = it.font(); f.setItalic(derived); it.setFont(f)
        it.setToolTip("abgeleitet – doppelklicken zum Benennen" if derived else "access_specs[].name")

    def _item_changed(self, it):
        if it.column() == 0 and it.data(DERIVED):
            self.specs.blockSignals(True); self._style_name(it.row(), False); self.specs.blockSignals(False)
        self._changed()

    def _changed(self, *_):
        if not self.job:
            return
        tot, parts = 0, []
        job = self.to_job()
        specs = job["tests"][self.ti]["access_specs"]
        self.specs.blockSignals(True)
        for r in range(self.specs.rowCount()):  # refresh derived names (block size etc. may have changed)
            it = self.specs.item(r, 0)
            if it.data(DERIVED):
                try:
                    it.setText(derived_spec_label(specs[r]))
                except (KeyError, ValueError, TypeError):
                    pass
        self.specs.blockSignals(False)
        for r in range(self.specs.rowCount()):
            try:
                w = int(self.specs.item(r, 1).text())
            except (ValueError, AttributeError):
                w = 0
            tot += w; parts.append((self.specs.item(r, 0).text(), w))
        self.sum_chip.setText(f"Σ {tot} %"); self.sum_chip.setObjectName("chipOk" if tot == 100 else "chipErr")
        self.sum_chip.style().unpolish(self.sum_chip); self.sum_chip.style().polish(self.sum_chip)
        while self.mixbar.count():
            it = self.mixbar.takeAt(0)
            if it.widget():
                wd = it.widget(); wd.setParent(None); wd.deleteLater()
        for i, (n, w) in enumerate(parts):
            c = T.SERIES[i % len(T.SERIES)]
            l = label(f"{n} · {w} %"); l.setAlignment(Qt.AlignCenter); l.setFixedHeight(26); l.setMinimumWidth(10)
            l.setStyleSheet(f"background: {T.rgba(c, .16)}; border: 1px solid {T.rgba(c, .5)}; border-radius: 6px; color: {c}; font-size: 11px; font-weight: 600;")
            self.mixbar.addWidget(l, max(w, 1))
        nw, qd = self.f["workers"].value(), self.f["queue_depth"].value()
        self.outstanding.setText(f"= {nw * qd} ausstehende I/Os gesamt ({nw} × {qd})  ·  Gesamtdauer ca. {self.f['warmup_s'].value() + self.f['duration_s'].value()} s")
        self.json.setPlainText(json.dumps(job, indent=1, ensure_ascii=False))

    def to_job(self):
        job = json.loads(json.dumps(self.job)); t = job["tests"][self.ti]
        specs = []
        for r in range(self.specs.rowCount()):
            d = {}
            for c, k in enumerate(SPEC_KEYS):
                it = self.specs.item(r, c)
                txt = it.text().strip() if it else ""
                if txt == "" or (c == 0 and it.data(DERIVED)):
                    continue
                d[k] = txt if c == 0 else (int(txt) if txt.isdigit() else txt)
            specs.append(d)
        t["access_specs"] = specs
        if self.name_edit.text().strip():
            t["name"] = self.name_edit.text().strip()
        else:
            t.pop("name", None)
        for k in ("workers", "queue_depth", "warmup_s", "duration_s"):
            t[k] = self.f[k].value()
        t["cache"] = self.f["cache"].currentText()
        return job

    def write_job(self, job=None):
        p = os.path.join(tempfile.gettempdir(), "sbench-gui-job.json")
        json.dump(job or self.to_job(), open(p, "w", encoding="utf-8"), indent=1, ensure_ascii=False); return p

    # ------------------------------------------------------------------ validation
    def validate(self):
        res = check_schema(sbench.validate(self.write_job()), "sb-validate/1")
        self.show_validation(res); return res

    def _show_issues(self, res):
        while self.issues.count():
            it = self.issues.takeAt(0)
            if it.widget():
                w = it.widget(); w.hide(); w.setParent(None); w.deleteLater()
        if res is None:
            self.issues.addWidget(label("Noch nicht geprüft.", "dim")); return
        rows = [(e, True) for e in res["errors"]] + [(w, False) for w in res.get("warnings", [])]
        if not rows:
            self.issues.addWidget(label("Keine Fehler, keine Warnungen.", "muted"))
        for e, is_err in rows[:6]:
            box = QFrame(); box.setObjectName("dangerBox" if is_err else "warnBox")
            bl = QVBoxLayout(box); bl.setContentsMargins(10, 7, 10, 7); bl.setSpacing(2)
            h = QHBoxLayout(); h.addWidget(chip(e["code"], "chipErr" if is_err else "chipWarn")); h.addStretch(1)
            h.addWidget(label(e["path"] or "/", "dim")); bl.addLayout(h)
            bl.addWidget(WrapLabel(e["msg"], "muted")); self.issues.addWidget(box)
        if len(rows) > 6:
            self.issues.addWidget(label(f"+ {len(rows) - 6} weitere", "dim"))

    def show_validation(self, res):
        """sb-validate/1 JSON pointers -> highlight table cells / fields; warnings (1.1) shown amber, ok stays true."""
        self.last = res
        self.specs.blockSignals(True)
        for r in range(self.specs.rowCount()):
            for c in range(1, self.specs.columnCount()):
                it = self.specs.item(r, c)
                if it:
                    it.setForeground(QColor(T.TEXT)); it.setToolTip(""); f = it.font(); f.setBold(False); it.setFont(f)
        for w in self.f.values():
            w.setProperty("invalid", False); w.style().polish(w)
        warns = res.get("warnings", [])
        if res["ok"]:
            self.valid_chip.setText(f"✓ gültig · {len(warns)} Warnung{'en' if len(warns) != 1 else ''}" if warns else "✓ gültig (sb-validate/1)")
            self.valid_chip.setObjectName("chipWarn" if warns else "chipOk")
        else:
            self.valid_chip.setText(f"{len(res['errors'])} Fehler" + (f" · {len(warns)} Warnungen" if warns else "")); self.valid_chip.setObjectName("chipErr")
            for e in res["errors"]:
                p = e["path"].split("/")
                if len(p) >= 6 and p[3] == "access_specs" and p[5] in SPEC_KEYS:
                    it = self.specs.item(int(p[4]), SPEC_KEYS.index(p[5]))
                    if it:
                        it.setForeground(QColor(T.RED)); f = it.font(); f.setBold(True); it.setFont(f); it.setToolTip(e["msg"])
                elif len(p) == 4 and p[3] == "access_specs":
                    self.sum_chip.setObjectName("chipErr"); self.sum_chip.style().polish(self.sum_chip)
                elif len(p) >= 4 and p[3] in self.f:
                    self.f[p[3]].setProperty("invalid", True); self.f[p[3]].style().polish(self.f[p[3]])
        self.valid_chip.style().unpolish(self.valid_chip); self.valid_chip.style().polish(self.valid_chip)
        self.specs.blockSignals(False)
        self._fill_targets(self.to_job()["tests"][self.ti], [(e["path"], e["code"]) for e in res["errors"] + warns])
        self._show_issues(res)

    # ------------------------------------------------------------------ start flow
    def start_flow(self):
        """validate -> (destructive confirm: sets allow_destructive + --allow-destructive) -> (elevation offer) -> run."""
        job = self.to_job(); res = self.validate()
        other = [e for e in res["errors"] if e["code"] != "E_DESTRUCTIVE_NOT_ALLOWED"]
        if other:
            return None
        dest = destructive_targets(job)
        if dest:
            dlg = DestructiveConfirm(self.window().centralWidget(), job, dest)
            dlg.accepted.connect(lambda keys: self._after_destructive(keys)); dlg.open(); return dlg
        return self._after_destructive(None)

    def _after_destructive(self, keys):
        allow = bool(keys)
        if keys:
            for ti, k in keys:
                self.job["tests"][ti]["targets"][k]["allow_destructive"] = True
            res = self.validate()
            if not res["ok"]:
                return None
        res = self.last
        need = [w for w in res.get("warnings", []) if w["code"] == "W_NEEDS_ADMIN"]
        if need and not self.elevated:
            raw = any(tg["kind"] != "file" for t in self.job["tests"] for tg in t.get("targets", []))
            dlg = ElevationOffer(self.window().centralWidget(), need, raw)
            dlg.accepted.connect(lambda elev: self.run_requested.emit(self.write_job(), {"elevated": bool(elev), "allow_destructive": allow}))
            dlg.open(); return dlg
        self.run_requested.emit(self.write_job(), {"elevated": False, "allow_destructive": allow})
        return None
