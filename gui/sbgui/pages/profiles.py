import json, os, tempfile
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton, QListWidgetItem, QLineEdit, QSpinBox,
                               QComboBox, QPlainTextEdit, QAbstractItemView, QMenu, QFileDialog, QFrame, QStackedWidget, QCheckBox)
from PySide6.QtGui import QColor
from ..widgets import Card, label, chip, table, add_row, listw, short_target, Responsive, WrapLabel
from ..model import (derived_spec_label, derived_test_label, check_schema, destructive_targets, test_label, size_bytes, size_str,
                     human_bytes, WORKLOADS, MEM_OPS, SIMD)
from ..overlay import DestructiveConfirm, ElevationOffer, IcfImportResult
from .. import theme as T, sbench

SPEC_COLS = ["Name", "Gewicht", "Block", "% Lesen", "% Rand.", "Align", "Burst", "Delay ms"]
SPEC_KEYS = ["name", "weight", "block_size", "read_pct", "random_pct", "align", "burst_len", "delay_ms"]
KIND_TXT = {"file": "Datei", "volume": "Volume", "physical": "Physische Disk"}
DERIVED = Qt.UserRole + 1

# Contract 1.2 (sb-job/1 test_cpu / test_memory): presets = the typical M2 measurements (cf. jobs/abnahme-m2/*.json).
# Each preset is a complete set of job fields of that test type (name excluded); "needs_numa" presets need >= 2 nodes.
CPU_PRESETS = [
    ("CPU 1 Thread · int", {"threads": 1, "workload": "int", "warmup_s": 1, "duration_s": 10}),
    ("CPU alle Threads · mixed", {"threads": 0, "workload": "mixed", "warmup_s": 2, "duration_s": 30}),
    ("CPU alle Threads · float", {"threads": 0, "workload": "float", "warmup_s": 2, "duration_s": 30}),
    ("CPU alle Threads · crypto (SHA-256)", {"threads": 0, "workload": "crypto", "warmup_s": 2, "duration_s": 30}),
    ("CPU 1 Thread · mixed", {"threads": 1, "workload": "mixed", "warmup_s": 1, "duration_s": 10}),
]
MEM_PRESETS = [
    ("RAM-Bandbreite lesen · alle Threads", {"mode": "bandwidth", "op": "read", "threads": 0, "buffer_size": "512MiB", "warmup_s": 1, "duration_s": 10}),
    ("RAM-Bandbreite schreiben NT · alle Threads", {"mode": "bandwidth", "op": "write", "threads": 0, "buffer_size": "512MiB", "nontemporal": True,
                                                   "warmup_s": 1, "duration_s": 10}),
    ("RAM copy · alle Threads", {"mode": "bandwidth", "op": "copy", "threads": 0, "buffer_size": "512MiB", "warmup_s": 1, "duration_s": 10}),
    ("RAM-Bandbreite 1 Thread", {"mode": "bandwidth", "op": "read", "threads": 1, "buffer_size": "1GiB", "warmup_s": 1, "duration_s": 10}),
    ("RAM-Latenz (1 GiB)", {"mode": "latency", "buffer_size": "1GiB", "warmup_s": 1, "duration_s": 10}),
    ("Cache-Latenz L1 (16 KiB)", {"mode": "latency", "buffer_size": "16KiB", "warmup_s": 1, "duration_s": 5}),
    ("NUMA remote: Latenz Knoten 0 → RAM 1", {"mode": "latency", "buffer_size": "1GiB", "numa_node": 0, "mem_node": 1, "warmup_s": 1,
                                              "duration_s": 10, "needs_numa": True}),
    ("NUMA remote: Bandbreite Knoten 0 → RAM 1", {"mode": "bandwidth", "op": "read", "threads": 0, "buffer_size": "512MiB", "numa_node": 0,
                                                  "mem_node": 1, "warmup_s": 1, "duration_s": 10, "needs_numa": True}),
]
BUFFER_SIZES = ["16KiB", "256KiB", "1MiB", "32MiB", "256MiB", "512MiB", "1GiB", "2GiB", "4GiB"]
COMPUTE_KEYS = ("threads", "workload", "mode", "op", "buffer_size", "simd", "nontemporal", "numa_node", "mem_node")


def preset_test(kind, idx=0):
    """New cpu/memory test from a preset (job fields only)."""
    name, fields = (CPU_PRESETS if kind == "cpu" else MEM_PRESETS)[idx]
    return {"type": kind, "name": name, **{k: v for k, v in fields.items() if k != "needs_numa"}}


class ProfileEditor(QWidget):
    run_requested = Signal(str, dict)  # job path, {"elevated": bool, "allow_destructive": bool}

    def __init__(self, profiles_dirs, elevated=False, numa_nodes=None):
        super().__init__(); self.setObjectName("page")
        self.dirs = profiles_dirs; self.user_dir = profiles_dirs[-1]; self.job = None; self.elevated = elevated; self.last = None
        self.numa_nodes = [n for n in (numa_nodes or []) if isinstance(n, dict)]; self.ti = 0
        v = QVBoxLayout(self); v.setContentsMargins(28, 24, 28, 24); v.setSpacing(16)
        hdr = QHBoxLayout(); tv = QVBoxLayout(); tv.setSpacing(2)
        self.title = label("Testprofil: Storage", "h1", wrap=True); tv.addWidget(self.title)
        self.title_sub = label("Iometer-artig: gewichtete Access Specs, Worker × Queue Depth, Warmup und Messdauer (sb-job/1)", "muted", wrap=True)
        tv.addWidget(self.title_sub)
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
        # tests of the profile (a job can mix storage, cpu and memory tests; contract 1.2)
        self.test_box = QComboBox(); self.test_box.setMinimumWidth(160)
        self.test_box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon); self.test_box.setMinimumContentsLength(16)
        self.test_box.currentIndexChanged.connect(self._select_test)
        b_cpu = QPushButton("+ CPU"); b_cpu.setToolTip("CPU-Test hinzufügen (Vorlage wählbar)"); b_cpu.clicked.connect(lambda: self.add_test("cpu"))
        b_mem = QPushButton("+ RAM"); b_mem.setToolTip("Arbeitsspeicher-Test hinzufügen (Bandbreite oder Latenz)"); b_mem.clicked.connect(lambda: self.add_test("memory"))
        self.b_del = QPushButton("Entfernen"); self.b_del.clicked.connect(self.remove_test)
        tsc = Card(margins=12); th = QHBoxLayout(); th.setSpacing(8)
        th.addWidget(label("Test", "muted")); th.addWidget(self.test_box, 1); th.addWidget(b_cpu); th.addWidget(b_mem); th.addWidget(self.b_del)
        tsc.add(th); cv.addWidget(tsc)
        self.editor_stack = QStackedWidget(); cv.addWidget(self.editor_stack, 1)
        storage_w = QWidget(); sv_ = QVBoxLayout(storage_w); sv_.setContentsMargins(0, 0, 0, 0); sv_.setSpacing(14)
        self.editor_stack.addWidget(storage_w)
        self.editor_stack.addWidget(self._build_compute_form())
        cv = sv_
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
        self.f_lbl = {}
        for i, (k, lab) in enumerate((("workers", "Worker"), ("queue_depth", "Queue Depth/Worker"), ("cache", "Cache-Modus"),
                                      ("warmup_s", "Warmup (s)"), ("duration_s", "Messdauer (s)"), ("tick", "Tick-Intervall (ms)")), 1):
            if k == "cache":
                w = QComboBox(); w.addItems(["unbuffered", "buffered", "writethrough"]); w.currentIndexChanged.connect(self._changed)
            else:
                w = QSpinBox(); w.setRange(1 if k != "warmup_s" else 0, 100000); w.setAlignment(Qt.AlignRight); w.valueChanged.connect(self._changed)
            self.f[k] = w; self.f_lbl[k] = label(lab, "muted"); g.addWidget(self.f_lbl[k], i, 0); g.addWidget(w, i, 1)
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
        """M2: `sbench import-icf <f> --out <job>`; shows W_ICF_* warnings (German texts: model.WARN_TXT), user decides to keep it."""
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
    def load(self, path, ti=None):
        self.job = check_schema(json.load(open(path, encoding="utf-8")), "sb-job/1")
        tests = self.job["tests"]
        self.ti = ti if ti is not None else next((i for i, t in enumerate(tests) if t["type"] == "storage"), 0)
        self.title.setText(f"Testprofil: {self.job.get('name', os.path.basename(path))}")
        self._load_test()

    def _fill_test_box(self):
        self.test_box.blockSignals(True); self.test_box.clear()
        for i, t in enumerate(self.job["tests"]):
            kind = {"storage": "Storage", "cpu": "CPU", "memory": "RAM"}.get(t["type"], t["type"])
            self.test_box.addItem(f"{i + 1}. {kind} · {test_label(t)}")
        self.test_box.setCurrentIndex(self.ti); self.test_box.blockSignals(False)
        self.b_del.setEnabled(len(self.job["tests"]) > 1)

    def _select_test(self, i):
        if not self.job or i < 0 or i == self.ti:
            return
        self.job = self.to_job()          # keep the edits of the current test
        self.ti = i; self._load_test()

    def add_test(self, kind, preset=0):
        """+ CPU / + RAM: append a contract-1.2 test from a preset and edit it."""
        if not self.job:
            return
        self.job = self.to_job(); self.job["tests"].append(preset_test(kind, preset))
        self.ti = len(self.job["tests"]) - 1; self._load_test()

    def remove_test(self):
        if self.job and len(self.job["tests"]) > 1:
            self.job = self.to_job(); del self.job["tests"][self.ti]
            self.ti = min(self.ti, len(self.job["tests"]) - 1); self._load_test()

    def open_quick(self, kind):
        """Dashboard quick start CPU/RAM: open the M2 profile on its first test of that type."""
        for i in range(self.list.count()):
            path = self.list.item(i).data(Qt.UserRole)
            if path and os.path.basename(path) == "cpu-ram.json":
                self.list.blockSignals(True); self.list.setCurrentRow(i); self.list.blockSignals(False)
                self.load(path)
                ti = next((j for j, t in enumerate(self.job["tests"]) if t["type"] == kind), None)
                if ti is not None:
                    self.test_box.setCurrentIndex(ti)
                return True
        return False

    def is_compute(self):
        return bool(self.job) and self.job["tests"][self.ti]["type"] in ("cpu", "memory")

    def _load_test(self):
        self._fill_test_box()
        t = self.job["tests"][self.ti]
        comp = t["type"] in ("cpu", "memory")
        if t["type"] not in ("storage", "cpu", "memory"):
            comp = True     # net_* / hyperv_info: not editable here, shown read-only in the JSON
        self.editor_stack.setCurrentIndex(1 if comp else 0)
        for k in ("workers", "queue_depth", "cache"):
            self.f[k].setVisible(not comp); self.f_lbl[k].setVisible(not comp)
        self.title_sub.setText("Iometer-artig: gewichtete Access Specs, Worker × Queue Depth, Warmup und Messdauer (sb-job/1)" if not comp else
                               "CPU/RAM (Vertrag 1.2): Threads fest auf logische Prozessoren gebunden, NUMA-Knoten wählbar (sb-job/1)")
        for w in list(self.f.values()) + [self.name_edit]:
            w.blockSignals(True)
        self.name_edit.setText(t.get("name", "")); self.name_edit.setCursorPosition(0); self.name_edit.setPlaceholderText(derived_test_label({**t, "name": None}))
        self.f["warmup_s"].setValue(int(t.get("warmup_s", 1 if comp else 0))); self.f["duration_s"].setValue(int(t.get("duration_s", 1) or 1))
        for w in list(self.f.values()) + [self.name_edit]:
            w.blockSignals(False)
        if comp:
            self._fill_compute(t)
            self.last = None; self._show_issues(None); self._changed()
            return
        self._fill_targets(t)
        self.specs.blockSignals(True); self.specs.setRowCount(0)
        for s in t["access_specs"]:
            r = add_row(self.specs, [s.get("name") or derived_spec_label(s)] + [s.get(k, "") for k in SPEC_KEYS[1:]])
            self._style_name(r, "name" not in s)
        self.specs.blockSignals(False)
        for w in list(self.f.values()) + [self.name_edit]:
            w.blockSignals(True)
        for k in ("workers", "queue_depth", "warmup_s", "duration_s"):
            self.f[k].setValue(int(t.get(k, 0 if k == "warmup_s" else 1)))
        self.f["cache"].setCurrentText(t.get("cache", "unbuffered"))
        for w in list(self.f.values()) + [self.name_edit]:
            w.blockSignals(False)
        self.last = None; self._show_issues(None)
        self._changed()

    # ------------------------------------------------------------------ cpu / memory form (contract 1.2)
    def _build_compute_form(self):
        self.cc = Card("CPU-Test", "sb-job/1 · test_cpu", margins=14)
        g = QGridLayout(); g.setHorizontalSpacing(12); g.setVerticalSpacing(8); g.setColumnStretch(1, 1); g.setColumnStretch(3, 1)
        self.cf, self.cf_lbl = {}, {}
        self.preset = QComboBox(); self.preset.activated.connect(self._apply_preset)
        g.addWidget(label("Vorlage", "muted"), 0, 0); g.addWidget(self.preset, 0, 1, 1, 3)

        def add(key, text, w, row, col):
            self.cf[key] = w; self.cf_lbl[key] = label(text, "muted")
            g.addWidget(self.cf_lbl[key], row, col * 2); g.addWidget(w, row, col * 2 + 1)
        wl = QComboBox(); wl.addItems(list(WORKLOADS)); add("workload", "Workload", wl, 1, 0)
        md = QComboBox(); md.addItem("Bandbreite", "bandwidth"); md.addItem("Latenz", "latency"); add("mode", "Modus", md, 1, 0)
        op = QComboBox(); op.addItems(list(MEM_OPS)); add("op", "Operation", op, 1, 1)
        th = QSpinBox(); th.setRange(0, 4096); th.setSpecialValueText("alle"); th.setAlignment(Qt.AlignRight); add("threads", "Threads", th, 2, 0)
        bs = QComboBox(); bs.setEditable(True); bs.addItems(BUFFER_SIZES); add("buffer_size", "Puffer/Thread", bs, 2, 1)
        sm = QComboBox(); sm.addItems(list(SIMD)); add("simd", "Befehlssatz", sm, 3, 0)
        nt = QCheckBox("Non-temporal Stores"); nt.setToolTip("Streaming Stores an den Caches vorbei (nur write/copy, nur x64)"); add("nontemporal", "", nt, 3, 1)
        nn = QComboBox(); nn.addItem("alle Knoten", None)
        mn = QComboBox(); mn.addItem("lokal (Knoten des Threads)", None)
        for n in self.numa_nodes:
            nn.addItem(f"Knoten {n['id']} · {len(n.get('cpus') or [])} CPUs", n["id"])
            mn.addItem(f"Knoten {n['id']} · {human_bytes(n.get('ram_bytes', 0))}", n["id"])
        add("numa_node", "NUMA (Threads)", nn, 4, 0); add("mem_node", "NUMA (Puffer)", mn, 4, 1)
        for w in self.cf.values():
            (w.toggled if isinstance(w, QCheckBox) else w.valueChanged if isinstance(w, QSpinBox) else w.currentIndexChanged).connect(self._changed)
        bs.editTextChanged.connect(self._changed)
        self.cc.add(g)
        self.c_hint = WrapLabel("", "dim"); self.cc.add(self.c_hint)
        self.cc.lay.addStretch(1)
        return self.cc

    def _fill_compute(self, t):
        cpu = t["type"] == "cpu"
        self.cc.title_label.setText("CPU-Test" if cpu else "Arbeitsspeicher-Test")
        self.cc.subtitle_label.setText("sb-job/1 · test_cpu · Score = ops/s ÷ Referenz-Thread × 1000" if cpu
                                       else "sb-job/1 · test_memory · Bandbreite (GB/s) oder Latenz (ns)")
        for w in self.cf.values():
            w.blockSignals(True)
        self.preset.clear(); self.preset.addItem("– Vorlage übernehmen –", None)
        for i, (n, f) in enumerate(CPU_PRESETS if cpu else MEM_PRESETS):
            if not f.get("needs_numa") or len(self.numa_nodes) > 1:
                self.preset.addItem(n, i)
        c = self.cf
        if t["type"] not in ("cpu", "memory"):
            for w in c.values():
                w.blockSignals(False)
            return
        c["workload"].setCurrentText(t.get("workload", "mixed"))
        c["mode"].setCurrentIndex(1 if t.get("mode") == "latency" else 0)
        c["op"].setCurrentText(t.get("op", "read"))
        c["threads"].setValue(int(t.get("threads", 0)))
        c["buffer_size"].setEditText(str(t.get("buffer_size", "512MiB")))
        c["simd"].setCurrentText(t.get("simd", "auto"))
        c["nontemporal"].setChecked(bool(t.get("nontemporal")))
        for k in ("numa_node", "mem_node"):
            i = c[k].findData(t.get(k)) if k in t else 0
            if i < 0:            # node from the profile is not on this machine: keep it (validate reports E_NUMA_NODE)
                c[k].addItem(f"Knoten {t[k]} (nicht vorhanden)", t[k]); i = c[k].count() - 1
            c[k].setCurrentIndex(i)
        for w in c.values():
            w.blockSignals(False)
        for k in ("workload",):
            c[k].setVisible(cpu); self.cf_lbl[k].setVisible(cpu)
        for k in ("mode", "op", "buffer_size", "simd", "nontemporal", "mem_node"):
            c[k].setVisible(not cpu); self.cf_lbl[k].setVisible(not cpu)
        self._compute_enable()

    def _compute_enable(self):
        c = self.cf; lat = c["mode"].currentData() == "latency"
        for k in ("op", "simd", "threads"):
            c[k].setEnabled(not lat)
        c["nontemporal"].setEnabled(not lat and c["op"].currentText() != "read")
        if c["mem_node"].count() < 2 and c["numa_node"].count() < 2:
            c["numa_node"].setToolTip("nur ein NUMA-Knoten"); c["numa_node"].setEnabled(False); c["mem_node"].setEnabled(False)

    def _apply_preset(self, i):
        idx = self.preset.itemData(i)
        if idx is None or not self.job:
            return
        t = self.job["tests"][self.ti]
        new = preset_test(t["type"], idx)
        self.job = self.to_job(); self.job["tests"][self.ti] = new
        self._load_test()

    def _compute_test(self, t):
        """Form -> cpu/memory test dict (only fields the 1.2 schema allows; defaults omitted where optional)."""
        c = self.cf; out = {"type": t["type"]}
        if self.name_edit.text().strip():
            out["name"] = self.name_edit.text().strip()
        lat = t["type"] == "memory" and c["mode"].currentData() == "latency"
        if t["type"] == "cpu":
            out.update(threads=c["threads"].value(), workload=c["workload"].currentText())
        else:
            out["mode"] = c["mode"].currentData()
            if not lat:
                out.update(op=c["op"].currentText(), threads=c["threads"].value())
            txt = c["buffer_size"].currentText().strip()
            try:
                out["buffer_size"] = size_str(size_bytes(int(txt) if txt.isdigit() else txt))
            except ValueError:
                out["buffer_size"] = txt                 # invalid -> sbench validate reports E_SCHEMA at /buffer_size
            if not lat and c["simd"].currentText() != "auto":
                out["simd"] = c["simd"].currentText()
            if not lat and c["nontemporal"].isChecked() and c["op"].currentText() != "read":
                out["nontemporal"] = True
        out["warmup_s"] = self.f["warmup_s"].value(); out["duration_s"] = self.f["duration_s"].value()
        if c["numa_node"].currentData() is not None:
            out["numa_node"] = c["numa_node"].currentData()
        if t["type"] == "memory" and c["mem_node"].currentData() is not None:
            out["mem_node"] = c["mem_node"].currentData()
        return out

    def _logical_cpus(self, node=None):
        nodes = [n for n in self.numa_nodes if node is None or n["id"] == node]
        return sum(len(n.get("cpus") or []) for n in nodes) or None

    def _compute_summary(self, t):
        if t["type"] == "cpu":
            n = t.get("threads", 0) or self._logical_cpus(t.get("numa_node"))
            return (f"= {n or 'alle'} Thread(s), je einer pro logischem Prozessor (erst ein Thread je Kern, Knoten abwechselnd)  ·  "
                    f"Gesamtdauer ca. {t.get('warmup_s', 1) + t['duration_s']} s")
        try:
            bs = size_bytes(t["buffer_size"])
        except ValueError:
            return "Puffergröße ungültig (z. B. 512MiB, 1GiB)"
        n = 1 if t["mode"] == "latency" else (t.get("threads", 0) or self._logical_cpus(t.get("numa_node")))

        def hb(b):
            return human_bytes(b, "GiB" if b >= 1 << 30 else "MiB" if b >= 1 << 20 else "KiB")
        tot = f"{hb(bs * n)} Puffer gesamt" if n else "Puffer gesamt: Threads × Puffer"
        return f"= {n or 'alle'} Thread(s) × {hb(bs)}  =  {tot}  ·  Gesamtdauer ca. {t.get('warmup_s', 1) + t['duration_s']} s"

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
        if self.is_compute():
            self._compute_enable()
            job = self.to_job(); t = job["tests"][self.ti]
            if t["type"] in ("cpu", "memory"):
                self.outstanding.setText(self._compute_summary(t))
                lat = t["type"] == "memory" and t.get("mode") == "latency"
                self.c_hint.setText(
                    "Workload: int = Ganzzahl, float = Gleitkomma, crypto = SHA-256, mixed = alles gemischt. 0 Threads = alle "
                    "logischen Prozessoren (des gewählten Knotens)." if t["type"] == "cpu" else
                    ("Latenz misst immer mit 1 Thread (Pointer Chasing). Puffer ≫ L3-Cache = RAM-Latenz, 16 KiB = L1." if lat else
                     "Puffer pro Thread; für RAM statt Cache deutlich größer als den L3-Cache wählen (256 MiB – 1 GiB). "
                     "copy zählt gelesene + geschriebene Bytes. NUMA (Puffer) ≠ NUMA (Threads) misst remote."))
                if self.test_box.currentIndex() >= 0:
                    kind = "CPU" if t["type"] == "cpu" else "RAM"
                    self.test_box.setItemText(self.ti, f"{self.ti + 1}. {kind} · {test_label(t)}")
            self.json.setPlainText(json.dumps(job, indent=1, ensure_ascii=False))
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
        if t["type"] in ("cpu", "memory"):
            job["tests"][self.ti] = self._compute_test(t); return job
        if t["type"] != "storage":
            return job
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
        for w in list(self.f.values()) + list(self.cf.values()):
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
                elif len(p) >= 4 and p[2] == str(self.ti) and p[3] in self.cf:     # contract 1.2: E_NUMA_NODE, E_BUFFER_SIZE, ...
                    w = self.cf[p[3]]; w.setProperty("invalid", True); w.setToolTip(e["msg"]); w.style().polish(w)
        self.valid_chip.style().unpolish(self.valid_chip); self.valid_chip.style().polish(self.valid_chip)
        self.specs.blockSignals(False)
        if not self.is_compute():
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
