import json
import numpy as np, pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QListWidgetItem, QComboBox, QPushButton
from ..widgets import Card, label, table, add_row, plot, fmt_num, listw, Responsive
from .. import theme as T
from ..model import (match_key, host_label, storage_tests, check_schema, compute_tests, summary_metric, fmt_metric, lower_is_better,
                     compute_details)

PCTS = ["p50", "p90", "p95", "p99", "p99_9", "p99_99"]
PCT_TXT = ["p50", "p90", "p95", "p99", "p99.9", "p99.99"]



def run_label(res, others=()):
    """Host label; if several selected runs come from the same host, add the start time to tell them apart."""
    lab = host_label(res["meta"])
    same = [o for o in others if o is not res and host_label(o["meta"]) == lab]
    if same:
        cfg = _load_cfg(res)
        lab += f" · {cfg}" if cfg and all(_load_cfg(o) != cfg for o in same) else f" · {_local(res['started_utc'])}"
    return lab


def _load_cfg(res):
    """'2 W × QD 8' from the first storage test (distinguishes runs of the same host with different load)."""
    st = storage_tests(res)
    if not st:
        return ""
    c = st[0]["config"]
    return f"{c.get('workers', 1)} W × QD {c.get('queue_depth', 1)}"


def by_name(res):
    """Matching across runs: test name (contract 1.1) if present, else derived label."""
    return {match_key(t["config"]): t for t in storage_tests(res)}


COMPUTE_UNIT = {"score": "Score", "gbps": "GB/s", "latency_ns": "ns"}


def compute_rows(runs):
    """cpu/memory comparison (contract 1.2), matched by test name like storage: [(name, key, [value per run], details)].
    cpu -> summary.score (higher = better), memory bandwidth -> gbps, latency -> latency_ns (lower = better).
    Order: tests of the first run that has cpu/memory tests, then tests only present in later runs."""
    names, rows = [], {}
    for k, r in enumerate(runs):
        for t in compute_tests(r):
            nm = match_key(t["config"])
            key, val = ("score", t["summary"].get("score")) if t["type"] == "cpu" else summary_metric(t)
            if nm not in rows:
                names.append(nm); rows[nm] = [key, [None] * len(runs), " · ".join(compute_details(t))]
            if rows[nm][0] == key:
                rows[nm][1][k] = val
    return [(nm, rows[nm][0], rows[nm][1], rows[nm][2]) for nm in names]


def rel_delta(val, base, key):
    """Percent vs. base; positive = better for every metric (latency inverted)."""
    if val is None or not base:
        return None
    d = (val - base) / base * 100
    return -d if lower_is_better(key) else d


def focus_test(tests):
    """Latency-percentile focus: first mixed random test (e.g. 4K 70/30), else first."""
    for k, t in tests.items():
        specs = t["config"]["access_specs"]
        if any(0 < s["read_pct"] < 100 and s["random_pct"] >= 50 for s in specs):
            return k, t
    return next(iter(tests.items()))


class Compare(QWidget):
    export_selected = Signal(list)

    def __init__(self, result_files):
        super().__init__(); self.setObjectName("page")
        self.results = [(f, check_schema(json.load(open(f, encoding="utf-8")), "sb-result/1")) for f in result_files]
        self.results = [(f, r) for f, r in self.results if len([t for t in r["tests"] if t["type"] == "storage"]) > 1] + \
                       [(f, r) for f, r in self.results if len([t for t in r["tests"] if t["type"] == "storage"]) <= 1]
        v = QVBoxLayout(self); v.setContentsMargins(28, 24, 28, 24); v.setSpacing(14)
        hdr = QHBoxLayout(); tv = QVBoxLayout(); tv.setSpacing(2)
        tv.addWidget(label("Ergebnisvergleich", "h1"))
        tv.addWidget(label("2–3 Läufe aus sb-result/1-Dateien, z. B. Host vs. VM. Basis = erster ausgewählter Lauf.", "muted"))
        hdr.addLayout(tv); hdr.addStretch(1)
        self.metric_lbl = label("Metrik", "muted"); hdr.addWidget(self.metric_lbl)
        self.metric_box = QComboBox(); self.metric_box.addItems(["IOPS", "MB/s", "Latenz Ø (µs)", "Latenz p99 (µs)"]); self.metric_box.setMinimumWidth(150)
        self.metric_box.currentIndexChanged.connect(self.update); hdr.addWidget(self.metric_box)
        v.addLayout(hdr)

        left = Card("Ergebnisse", f"{len(self.results)} Läufe"); left.setMinimumHeight(170)
        self.list = listw(); left.add(self.list, 1)
        for i, (f, r) in enumerate(self.results):
            nst, ncp = len(storage_tests(r)), len(compute_tests(r))
            cnt = " · ".join(x for x in (f"{nst} Storage" if nst else "", f"{ncp} CPU/RAM" if ncp else "") if x) or "0 Tests"
            it = QListWidgetItem(f"{run_label(r)}{'  · Beispiel' if r.get('_example') else ''}\n"
                                 f"{cnt} · {_load_cfg(r) + ' · ' if _load_cfg(r) else ''}{_local(r['started_utc'])}")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable); it.setCheckState(Qt.Checked if i < 3 else Qt.Unchecked)
            it.setData(Qt.UserRole, i); self.list.addItem(it)
        self.list.itemChanged.connect(self.update)
        b = QPushButton("Auswahl für Support exportieren"); b.clicked.connect(lambda: self.export_selected.emit(self.selected_files()))
        left.add(b)

        center_w = QWidget(); center = QVBoxLayout(center_w); center.setContentsMargins(0, 0, 0, 0); center.setSpacing(14)
        self.legend = QHBoxLayout(); self.legend.setSpacing(12); lw = QWidget(); lw.setLayout(self.legend); self.legend.setContentsMargins(0, 0, 0, 0)
        self.bar_card = Card("IOPS je Test", "gruppiert nach Lauf", right=[lw], margins=14)
        self.p_bar = plot(left_width=62); self.bar_card.add(self.p_bar, 1); self.bar_card.setMinimumHeight(240)
        self.lc = lc = Card("Latenz-Perzentile", "µs, log. Skala", margins=14); lc.setMinimumHeight(240)
        self.p_pct = plot(left_width=50); lc.add(self.p_pct, 1)
        self.st_row = Responsive([(self.bar_card, 3, None), (lc, 2, None)], breakpoint=820)
        center.addWidget(self.st_row, 5)
        self.dc = Card("Delta-Tabelle", "Abweichung gegenüber Basislauf", margins=14); self.dc.setMinimumHeight(200)
        self.delta = table([], stretch_col=None); self.dc.add(self.delta, 1)
        center.addWidget(self.dc, 4)
        # contract 1.2: cpu / memory (score, GB/s, latency) relative to the base run, e.g. host vs. VM
        self.cleg = QHBoxLayout(); self.cleg.setSpacing(12); clw = QWidget(); clw.setLayout(self.cleg); self.cleg.setContentsMargins(0, 0, 0, 0)
        self.cc = Card("CPU & Arbeitsspeicher", "relativ zum Basislauf A = 100 % · höher = besser (Latenz invertiert)", right=[clw], margins=14)
        self.cc.setMinimumHeight(380)
        self.p_cmp = plot(left_width=50); self.p_cmp.setMinimumHeight(150); self.cc.add(self.p_cmp, 2)
        self.ctab = table([], stretch_col=None); self.ctab.setMinimumHeight(120); self.cc.add(self.ctab, 3)
        center.addWidget(self.cc, 5)
        v.addWidget(Responsive([(left, 0, 270), (center_w, 1, None)], breakpoint=900), 1)
        self.update()

    def selected(self):
        return [self.results[self.list.item(i).data(Qt.UserRole)] for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked][:3]

    def selected_files(self):
        return [f for f, _ in self.selected()]

    def _val(self, t):
        s = t["summary"]; i = self.metric_box.currentIndex()
        return [s["iops"], s["mbps"], s["lat_us"]["mean"], s["lat_us"]["p99"]][i]

    def update(self, *_):
        self._update_compute([r for _, r in self.selected() if compute_tests(r)])
        sel = [(f, r) for f, r in self.selected() if storage_tests(r)]  # results without storage tests (all skipped) have nothing to plot
        for w in (self.st_row, self.dc, self.metric_lbl, self.metric_box):
            w.setVisible(bool(sel) or not any(compute_tests(r) for _, r in self.selected()))
        self.p_bar.clear(); self.p_pct.clear()
        while self.legend.count():
            wd = self.legend.takeAt(0).widget()
            if wd:
                wd.setParent(None); wd.deleteLater()
        if not sel:
            return
        names = list(by_name(sel[0][1]))
        n = len(sel); w = 0.8 / n; lo = hi = 0
        mname = self.metric_box.currentText()
        self.bar_card.title_label.setText(f"{mname} je Test")
        for k, (f, r) in enumerate(sel):
            col = T.SERIES[k]
            tests = by_name(r)
            ys = [self._val(tests[nm]) if nm in tests else 0 for nm in names]
            xs = np.arange(len(names)) - 0.4 + w * (k + 0.5)
            self.p_bar.addItem(pg.BarGraphItem(x=xs, height=ys, width=w * 0.86, brush=pg.mkBrush(col), pen=pg.mkPen(None)))
            l = label(f"■  {run_label(r, [x for _, x in sel])}"); l.setStyleSheet(f"color:{col}; font-size:11px;"); self.legend.addWidget(l)
            if k == 0:  # focus test of the first run; the other runs show the same test (by name) if present
                fkey, focus = focus_test(tests)
                self.lc.title_label.setText(f"Latenz-Perzentile · {fkey}")
            else:
                focus = tests.get(fkey) or next(iter(tests.values()))
            py = [np.log10(focus["summary"]["lat_us"][p]) for p in PCTS]  # manual log scale -> clean tick labels
            self.p_pct.plot(range(len(PCTS)), py, pen=pg.mkPen(col, width=2), symbol="o", symbolSize=7, symbolBrush=col, symbolPen=None)
            lo = min(lo, min(py)) if k else min(py); hi = max(hi, max(py)) if k else max(py)
        ax = self.p_bar.getAxis("bottom"); ax.setTicks([[(i, _wrap(nm)) for i, nm in enumerate(names)]]); ax.setHeight(34)
        self.p_bar.setXRange(-0.55, len(names) - 0.45, padding=0)
        ymax = max((self._val(t) for _, r in sel for t in storage_tests(r)), default=0) or 1
        self.p_bar.setYRange(0, ymax * 1.1, padding=0)
        self.p_bar.getAxis("left").setTicks(None)
        self.p_pct.getAxis("bottom").setTicks([list(enumerate(PCT_TXT))]); self.p_pct.setXRange(-0.35, len(PCTS) - 0.55, padding=0)
        nice = [v * 10 ** e for e in range(0, 7) for v in (1, 2, 5)]
        self.p_pct.getAxis("left").setTicks([[(np.log10(v), _us(v)) for v in nice if lo - 0.15 <= np.log10(v) <= hi + 0.15]])
        self.p_pct.setYRange(lo - 0.12, hi + 0.12, padding=0)

        # delta table: one row per test; IOPS and p99 per run with delta vs. base
        runs = [r for _, r in sel]
        letters = "ABC"
        hdrs = ["Test"]
        for metric in ("IOPS", "p99 µs"):
            for k in range(len(runs)):
                hdrs.append(f"{metric} {letters[k]}")
                if k:
                    hdrs.append(f"Δ {letters[k]}")
        self.delta.clear(); self.delta.setRowCount(0); self.delta.setColumnCount(len(hdrs)); self.delta.setHorizontalHeaderLabels(hdrs)
        self.delta._mono = set(range(1, len(hdrs)))
        from PySide6.QtWidgets import QHeaderView
        hh = self.delta.horizontalHeader(); hh.setSectionResizeMode(QHeaderView.Stretch)
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.dc.title_label.setText("Delta-Tabelle  ·  " + "   ".join(f"{letters[k]} = {run_label(r, runs)}" for k, r in enumerate(runs)))
        for nm in names:
            row, colors = [nm], {}
            for getter, lower_better in ((lambda s: s["iops"], False), (lambda s: s["lat_us"]["p99"], True)):
                vals = []
                for r in runs:
                    o = by_name(r).get(nm)
                    vals.append(getter(o["summary"]) if o else None)
                for k, val in enumerate(vals):
                    row.append(fmt_num(val))
                    if k:
                        d = (val - vals[0]) / vals[0] * 100 if val and vals[0] else 0
                        row.append("±0 %" if abs(d) < 0.5 else f"{d:+.0f} %")
                        good = (d < 0) if lower_better else (d > 0)
                        colors[len(row) - 1] = T.GREEN if good or abs(d) < 2 else (T.AMBER if abs(d) < 25 else T.RED)
            add_row(self.delta, row, colors)


    def _update_compute(self, runs):
        self.cc.setVisible(bool(runs)); self.p_cmp.clear()
        while self.cleg.count():
            wd = self.cleg.takeAt(0).widget()
            if wd:
                wd.setParent(None); wd.deleteLater()
        if not runs:
            return
        rows = compute_rows(runs); letters = "ABC"; n = len(runs); w = 0.8 / n; top = 100.0
        for k, r in enumerate(runs):
            col = T.SERIES[k]
            ys = []
            for _, key, vals, _ in rows:
                d = rel_delta(vals[k], vals[0], key)
                ys.append(100 + d if d is not None else 0)
            top = max([top] + ys)
            xs = np.arange(len(rows)) - 0.4 + w * (k + 0.5)
            self.p_cmp.addItem(pg.BarGraphItem(x=xs, height=ys, width=w * 0.86, brush=pg.mkBrush(col), pen=pg.mkPen(None)))
            lab = label(f"■  {letters[k]} = {run_label(r, runs)}"); lab.setStyleSheet(f"color:{col}; font-size:11px;"); self.cleg.addWidget(lab)
        self.p_cmp.addItem(pg.InfiniteLine(pos=100, angle=0, pen=pg.mkPen(T.DIM, width=1, style=Qt.DashLine)))
        ax = self.p_cmp.getAxis("bottom"); ax.setTicks([[(i, _wrap(nm, 14)) for i, (nm, *_r) in enumerate(rows)]]); ax.setHeight(34)
        self.p_cmp.setXRange(-0.55, len(rows) - 0.45, padding=0)
        self.p_cmp.setYRange(0, top * 1.12, padding=0)
        self.p_cmp.getAxis("left").setTicks([[(v, f"{v} %") for v in range(0, int(top * 1.12) + 1, 25 if top < 200 else 50)]])
        hdrs = ["Test", "Metrik"]
        for k in range(n):
            hdrs.append(letters[k])
            if k:
                hdrs.append(f"Δ {letters[k]}")
        self.ctab.clear(); self.ctab.setRowCount(0); self.ctab.setColumnCount(len(hdrs)); self.ctab.setHorizontalHeaderLabels(hdrs)
        self.ctab._mono = set(range(2, len(hdrs)))
        from PySide6.QtWidgets import QHeaderView
        hh = self.ctab.horizontalHeader(); hh.setSectionResizeMode(QHeaderView.Stretch); hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for nm, key, vals, det in rows:
            row, colors = [nm, COMPUTE_UNIT[key]], {}
            for k, val in enumerate(vals):
                row.append(fmt_metric(key, val))
                if k:
                    d = rel_delta(val, vals[0], key)
                    row.append("–" if d is None else f"{d:+.0f} %")
                    if d is not None:
                        colors[len(row) - 1] = T.GREEN if d >= 0 or abs(d) < 2 else (T.AMBER if abs(d) < 25 else T.RED)
            r_ = add_row(self.ctab, row, colors); self.ctab.item(r_, 0).setToolTip(det)
        self.ctab.setMinimumHeight(min(32 * len(rows) + 38, 260))


def _wrap(name, n=11):
    """Two-line tick labels for long test names (pyqtgraph draws '\n' as a line break)."""
    if len(name) <= n or " " not in name:
        return name
    cut = min((i for i, c in enumerate(name) if c == " "), key=lambda i: abs(i - len(name) / 2))
    return name[:cut] + "\n" + name[cut + 1:]


def _us(v):
    return f"{v / 1000:g} ms" if v >= 1000 else f"{v:g} µs"


def _local(utc):
    import datetime as dt
    d = dt.datetime.fromisoformat(utc.replace("Z", "+00:00")).astimezone()
    return d.strftime("%d.%m. %H:%M")
