"""Ergebnisse: one sb-result/1 in detail. Contract 1.2 (M2): cpu score per workload + threads, memory GB/s per mode
(read/write/copy, NT) + instruction set, latency in ns, NUMA placement (numa_node -> mem_node) and RAM per node with
its source (meta.numa_nodes[].ram_method). Storage tests as a compact summary table."""
import json, os
import numpy as np, pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QComboBox
from ..widgets import Card, label, chip, table, add_row, plot, fmt_num, Responsive
from ..model import (check_schema, host_label, human_bytes, numa_cpus, storage_tests, test_label, summary_metric, fmt_metric,
                     compute_details, numa_placement, RAM_METHOD_TXT, METRIC_TXT, short_size)
from .. import theme as T


def _local(utc):
    import datetime as dt
    return dt.datetime.fromisoformat(utc.replace("Z", "+00:00")).astimezone().strftime("%d.%m.%Y %H:%M")


class ResultsPage(QWidget):
    def __init__(self, result_files):
        super().__init__(); self.setObjectName("page")
        self.results = []
        for f in result_files:
            try:
                self.results.append((f, check_schema(json.load(open(f, encoding="utf-8")), "sb-result/1")))
            except (OSError, ValueError):
                pass
        v = QVBoxLayout(self); v.setContentsMargins(28, 24, 28, 24); v.setSpacing(14)
        hdr = QHBoxLayout(); tv = QVBoxLayout(); tv.setSpacing(2)
        tv.addWidget(label("Ergebnis", "h1"))
        self.sub = label("sb-result/1 · Storage, CPU und Arbeitsspeicher", "muted"); tv.addWidget(self.sub)
        hdr.addLayout(tv, 1)
        self.state = chip("–"); hdr.addWidget(self.state, 0, Qt.AlignTop); hdr.addSpacing(6)
        self.box = QComboBox(); self.box.setMinimumWidth(260); self.box.setMaximumWidth(420)
        self.box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon); self.box.setMinimumContentsLength(28)
        for f, r in self.results:
            self.box.addItem(f"{host_label(r['meta'])} · {_local(r['started_utc'])} · {os.path.basename(f)}", f)
        hdr.addWidget(self.box, 0, Qt.AlignTop)
        v.addLayout(hdr)

        # system / NUMA
        self.sys_card = Card("System", "meta · RAM pro NUMA-Knoten (Quelle: ram_method)", margins=14)
        self.sys_line = label("", "muted", wrap=True); self.sys_card.add(self.sys_line)
        self.numa = table(["Knoten", "CPUs", "RAM", "frei", "Quelle"], stretch_col=4, mono_cols=(1, 2, 3))
        self.numa.setMinimumHeight(32 * 2 + 36); self.sys_card.add(self.numa)
        # cpu
        self.cpu_card = Card("CPU", "Score = ops/s ÷ Referenz-Thread × 1000 (höher = besser)", margins=14)
        self.cpu_tab = table(["Test", "Workload", "Threads", "NUMA", "ops/s", "Score"], stretch_col=0, mono_cols=(2, 4, 5))
        self.cpu_tab.setMinimumHeight(32 * 2 + 36)
        self.p_cpu = plot(left_width=54); self.p_cpu.setMinimumHeight(150)
        self.cpu_card.add(Responsive([(self.cpu_tab, 3, None), (self.p_cpu, 2, None)], breakpoint=760))
        # memory
        self.mem_card = Card("Arbeitsspeicher", "GB/s = 10⁹ Byte/s, Summe aller Threads (copy: Lesen + Schreiben) · Latenz in ns", margins=14)
        self.mem_tab = table(["Test", "Modus", "SIMD", "Threads", "Puffer", "NUMA", "GB/s", "Latenz ns"], stretch_col=0, mono_cols=(3, 4, 6, 7))
        self.mem_tab.setMinimumHeight(32 * 2 + 36)
        self.p_mem = plot(left_width=54); self.p_mem.setMinimumHeight(150)
        self.mem_card.add(Responsive([(self.mem_tab, 3, None), (self.p_mem, 2, None)], breakpoint=760))
        # per thread / time series of one cpu/memory test
        self.tsel = QComboBox(); self.tsel.setMinimumWidth(200); self.tsel.setMaximumWidth(320)
        self.tsel.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon); self.tsel.setMinimumContentsLength(20)
        self.th_card = Card("Pro Thread", "per_worker · Thread → logischer Prozessor / Knoten", right=[self.tsel], margins=14)
        self.p_thr = plot(left_width=54); self.p_thr.setMinimumHeight(170)
        self.p_ts = plot(left_width=54); self.p_ts.setMinimumHeight(170)
        self.th_info = label("", "dim", wrap=True)
        self.th_card.add(Responsive([(self.p_thr, 3, None), (self.p_ts, 2, None)], breakpoint=760)); self.th_card.add(self.th_info)
        # storage
        self.st_card = Card("Storage", "Zusammenfassung je Test", margins=14)
        self.st_tab = table(["Test", "IOPS", "MB/s", "Ø µs", "p99 µs", "Fehler"], stretch_col=0, mono_cols=(1, 2, 3, 4, 5))
        self.st_tab.setMinimumHeight(32 * 2 + 36); self.st_card.add(self.st_tab)
        self.empty = label("Keine Ergebnisse vorhanden. Nach einem Lauf erscheint das Ergebnis hier.", "muted")
        for w in (self.sys_card, self.cpu_card, self.mem_card, self.th_card, self.st_card, self.empty):
            v.addWidget(w)
        v.addStretch(1)
        self.box.currentIndexChanged.connect(self.show_index)
        self.tsel.currentIndexChanged.connect(self._show_thread)
        self.show_index(0)

    # ------------------------------------------------------------------
    def select_file(self, path):
        for i in range(self.box.count()):
            if os.path.normcase(os.path.abspath(self.box.itemData(i))) == os.path.normcase(os.path.abspath(path)):
                self.box.setCurrentIndex(i); self.show_index(i); return True
        return False

    def current(self):
        i = self.box.currentIndex()
        return self.results[i][1] if 0 <= i < len(self.results) else None

    def show_index(self, i):
        r = self.results[i][1] if 0 <= i < len(self.results) else None
        for w in (self.sys_card, self.cpu_card, self.mem_card, self.th_card, self.st_card):
            w.setVisible(r is not None)
        self.empty.setVisible(r is None)
        if r is None:
            self.state.setText("kein Ergebnis"); return
        m = r["meta"]
        self.sub.setText(f"{host_label(m)} · Kern {r['kern_version']} · Lauf {r.get('run_id', '–')} · {_local(r['started_utc'])}")
        aborted = r.get("aborted")
        self.state.setText("abgebrochen · Teilergebnis" if aborted else "vollständig"); self.state.setObjectName("chipWarn" if aborted else "chipOk")
        self.state.style().unpolish(self.state); self.state.style().polish(self.state)
        cpu = m["cpu"]
        self.sys_line.setText(f"{cpu['model']} · {cpu['sockets']} Sockel · {cpu['cores']}C/{cpu['threads']}T · {human_bytes(m['ram_bytes'])} RAM"
                              + (f" ({human_bytes(m['ram_free_bytes'])} frei)" if m.get("ram_free_bytes") is not None else "")
                              + f" · {m['os']['name']} {m['os']['build']}")
        self.numa.setRowCount(0)
        for n in m.get("numa_nodes") or []:
            meth = n.get("ram_method")
            add_row(self.numa, [n["id"], numa_cpus(n), human_bytes(n["ram_bytes"]), human_bytes(n["ram_free_bytes"]),
                                RAM_METHOD_TXT.get(meth, meth) if meth else "– (Kern < 1.2)"],
                    {4: T.AMBER if meth == "estimate" else (T.DIM if not meth else None)})
            if meth == "estimate":
                self.numa.item(self.numa.rowCount() - 1, 4).setToolTip("SRAT nicht lesbar: Aufteilung des Gesamt-RAM im Verhältnis des freien Speichers (ungenau)")
        self.numa.setFixedHeight(32 * max(1, self.numa.rowCount()) + 38)

        cpu_t = [t for t in r["tests"] if t["type"] == "cpu"]
        mem_t = [t for t in r["tests"] if t["type"] == "memory"]
        self.cpu_card.setVisible(bool(cpu_t)); self.mem_card.setVisible(bool(mem_t)); self.th_card.setVisible(bool(cpu_t or mem_t))
        self.cpu_tab.setRowCount(0)
        for t in cpu_t:
            s = t["summary"]
            add_row(self.cpu_tab, [test_label(t["config"]), s["workload"], s["threads"], numa_placement(t) or "alle",
                                   fmt_metric("ops_per_s", s["ops_per_s"]), fmt_metric("score", s["score"])], {5: T.ACCENT})
        self._fit(self.cpu_tab)
        self._bars(self.p_cpu, [test_label(t["config"]) for t in cpu_t], [t["summary"]["score"] for t in cpu_t], T.ACCENT, "Score")
        self.mem_tab.setRowCount(0)
        for t in mem_t:
            s = t["summary"]; bw = s.get("mode") == "bandwidth"
            mode = (s.get("op", "read") + (" NT" if s.get("nontemporal") else "")) if bw else "Latenz"
            add_row(self.mem_tab, [test_label(t["config"]), mode, s.get("simd", "–") if bw else "–", s["threads"],
                                   short_size(s["buffer_size_bytes"]), numa_placement(t) or "lokal",
                                   fmt_metric("gbps", s.get("gbps")) if bw else "–", fmt_metric("latency_ns", s.get("latency_ns")) if not bw else "–"],
                    {6: T.VIOLET, 7: T.TEAL, 5: T.AMBER if "remote" in (numa_placement(t) or "") else None})
        self._fit(self.mem_tab)
        bwt = [t for t in mem_t if t["summary"].get("mode") == "bandwidth"]
        self._bars(self.p_mem, [test_label(t["config"]) for t in bwt], [t["summary"].get("gbps", 0) for t in bwt], T.VIOLET, "GB/s")
        self.tsel.blockSignals(True); self.tsel.clear()
        for t in cpu_t + mem_t:
            self.tsel.addItem(test_label(t["config"]), t["index"])
        self.tsel.blockSignals(False)
        self._show_thread(0)
        st = storage_tests(r)
        self.st_card.setVisible(bool(st)); self.st_tab.setRowCount(0)
        for t in st:
            s = t["summary"]; l = s["lat_us"]
            add_row(self.st_tab, [test_label(t["config"]), fmt_num(s["iops"]), f"{s['mbps']:,.0f}".replace(",", " "), f"{l['mean']:,.0f}",
                                  f"{l['p99']:,.0f}".replace(",", " "), s["errors"]])
        self._fit(self.st_tab)

    @staticmethod
    def _fit(t):
        t.setFixedHeight(32 * max(1, t.rowCount()) + 38)

    @staticmethod
    def _bars(p, names, vals, col, unit):
        p.clear()
        if not vals:
            return
        x = np.arange(len(vals))
        p.addItem(pg.BarGraphItem(x=x, height=vals, width=0.6, brush=pg.mkBrush(col), pen=pg.mkPen(None)))
        ax = p.getAxis("bottom"); ax.setTicks([[(i, _short(n)) for i, n in enumerate(names)]])
        p.setXRange(-0.6, len(vals) - 0.4, padding=0); p.setYRange(0, max(vals) * 1.15 or 1, padding=0)
        p.getPlotItem().setTitle(f"<span style='color:{T.MUTED};font-size:9pt'>{unit}</span>")

    def _show_thread(self, _i=0):
        r = self.current()
        self.p_thr.clear(); self.p_ts.clear()
        if r is None or self.tsel.currentIndex() < 0:
            self.th_info.setText(""); return
        idx = self.tsel.currentData()
        t = next(x for x in r["tests"] if x["index"] == idx)
        key, total = summary_metric(t)
        name, unit = METRIC_TXT[key]
        pw = [w for w in t.get("per_worker", []) if key in w]
        if pw:
            vals = [w[key] for w in pw]; med = float(np.median(vals))
            lowb = (lambda v: v > 1.1 * med) if key == "latency_ns" else (lambda v: v < 0.9 * med)
            self.p_thr.addItem(pg.BarGraphItem(x=[w["id"] for w in pw], height=vals, width=0.8 if len(pw) < 48 else 1.0,
                                               brushes=[pg.mkBrush(T.AMBER if lowb(v) else T.ACCENT) for v in vals], pen=pg.mkPen(None)))
            self.p_thr.setXRange(-0.7, len(pw) - 0.3, padding=0); self.p_thr.setYRange(0, max(vals) * 1.15 or 1, padding=0)
            self.p_thr.getPlotItem().setTitle(f"<span style='color:{T.MUTED};font-size:9pt'>{unit} je Thread</span>")
            nodes = sorted({w.get("node") for w in pw if w.get("node") is not None})
            mems = sorted({w.get("mem_node") for w in pw if w.get("mem_node") is not None})
            remote = sum(1 for w in pw if w.get("mem_node") is not None and w.get("node") is not None and w["mem_node"] != w["node"])
            cpus = [w.get("cpu") for w in pw if w.get("cpu") is not None]
            facts = [f"{len(pw)} Thread(s)", f"Summe {fmt_metric(key, total)} {unit}" if key != "latency_ns" else f"{fmt_metric(key, total)} ns"]
            if cpus:
                facts.append("CPUs " + (", ".join(map(str, cpus)) if len(cpus) <= 12 else f"{', '.join(map(str, cpus[:10]))}, … ({len(cpus)})"))
            if nodes:
                facts.append(f"Knoten {', '.join(map(str, nodes))}" + (f" · RAM-Knoten {', '.join(map(str, mems))}" if mems else ""))
            if remote:
                facts.append(f"{remote} Thread(s) remote")
            facts += compute_details(t)[:1]
            self.th_info.setText("  ·  ".join(facts))
        else:
            self.th_info.setText("keine per_worker-Werte")
        ts = [p for p in t.get("timeseries", []) if key in p]
        if ts:
            x = [p["t"] for p in ts]; y = [p[key] for p in ts]
            self.p_ts.plot(x, y, pen=pg.mkPen(T.VIOLET, width=2), symbol="o", symbolSize=5, symbolBrush=T.VIOLET, symbolPen=None)
            self.p_ts.setYRange(0, max(y) * 1.15 or 1, padding=0)
            self.p_ts.getPlotItem().setTitle(f"<span style='color:{T.MUTED};font-size:9pt'>{name} über die Zeit ({unit}, 1 s)</span>")


def _short(name, n=14):
    return name if len(name) <= n else name[: n - 1] + "…"
