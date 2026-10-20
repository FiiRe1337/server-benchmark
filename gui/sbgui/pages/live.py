import numpy as np, pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QLinearGradient, QBrush, QFont
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton, QProgressBar, QPlainTextEdit, QCheckBox
from ..widgets import Card, Kpi, label, chip, table, add_row, plot, fmt_num, short_target, Responsive
from ..buffers import RingBuffer, minmax_downsample
from ..model import (test_label, human_bytes, short_size, size_bytes, tick_metric, expected_metric, fmt_metric, estimated_score,
                     METRIC_TXT, score_reference, worker_score, FUTURE_METRICS, ERROR_TXT)
from .. import theme as T

FIELDS = ("t", "iops", "mbps", "lat_mean", "lat_p50", "lat_p99", "lat_max", "cpu", "r_iops", "w_iops", "r_mbps", "w_mbps", "proc_cpu",
          "test", "ops_per_s", "gbps", "latency_ns", "score")
COL = {k: i for i, k in enumerate(FIELDS)}
NAN = float("nan")
PREP_BAR_MIN = 64 << 20     # memory preparing: show the "Puffer wird angelegt" bar from this total size on (small buffers are instant)
PHASES = [("preparing", "Vorbereitung"), ("warmup", "Warmup"), ("measure", "Messung"), ("cleanup", "Cleanup"), ("done", "Fertig")]


def grad_brush(color, alpha=70):
    g = QLinearGradient(0, 0, 0, 1); g.setCoordinateMode(QLinearGradient.ObjectBoundingMode)
    c = QColor(color); c.setAlpha(alpha); g.setColorAt(0, c); c2 = QColor(color); c2.setAlpha(0); g.setColorAt(1, c2)
    return QBrush(g)


class LiveView(QWidget):
    stop_requested = Signal()
    result_ready = Signal(str)   # path from done event; only read after done (atomic rename)
    elevate_requested = Signal()

    def __init__(self):
        super().__init__(); self.setObjectName("page")
        self.buf = RingBuffer(fields=FIELDS); self.source = None
        v = QVBoxLayout(self); v.setContentsMargins(28, 24, 28, 24); v.setSpacing(14)

        hdr = QHBoxLayout(); tv = QVBoxLayout(); tv.setSpacing(2)
        self.title = label("Live-Lauf", "h1"); tv.addWidget(self.title)
        self.subtitle = label("Kein Lauf aktiv", "muted"); tv.addWidget(self.subtitle)
        hdr.addLayout(tv); hdr.addStretch(1)
        self.run_chip = chip("bereit"); hdr.addWidget(self.run_chip); hdr.addSpacing(8)
        self.stop_btn = QPushButton("■  Stoppen"); self.stop_btn.setObjectName("danger"); self.stop_btn.setMinimumWidth(120)
        self.stop_btn.clicked.connect(self.stop_requested.emit); hdr.addWidget(self.stop_btn)
        v.addLayout(hdr)

        # phase / progress
        pc = Card(margins=14)
        ph = QHBoxLayout(); ph.setSpacing(8); self.phase_chips = {}
        for key, txt in PHASES:
            c = chip(txt); self.phase_chips[key] = c; ph.addWidget(c)
        ph.addSpacing(10)
        self.progress = QProgressBar(); self.progress.setTextVisible(False); self.progress.setRange(0, 1000); self.progress.setFixedHeight(8); ph.addWidget(self.progress, 1)
        ph.addSpacing(10)
        self.time_lbl = label("00:00 / 00:00", "big"); self.time_lbl.setStyleSheet("font-size: 15px;"); self.time_lbl.setMinimumWidth(210)
        self.time_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter); ph.addWidget(self.time_lbl)
        pc.add(ph)
        # contract 1.2: memory tests allocate + first-touch their buffers in `preparing` (progress in bytes, sum of all threads)
        self.prep_bar = QProgressBar(); self.prep_bar.setRange(0, 1000); self.prep_bar.setFixedHeight(22); self.prep_bar.setTextVisible(True)
        self.prep_bar.setFormat("Puffer wird angelegt"); self.prep_bar.setAlignment(Qt.AlignCenter); self.prep_bar.setObjectName("prepBar")
        self.prep_bar.setStyleSheet(f"QProgressBar#prepBar {{ font-size: 11px; font-weight: 600; color: {T.TEXT}; }}")
        self.prep_bar.hide(); pc.add(self.prep_bar)
        v.addWidget(pc)

        # KPIs
        kh = QHBoxLayout(); kh.setSpacing(12)
        self.k_iops = Kpi("IOPS", "", T.ACCENT); self.k_mbps = Kpi("Durchsatz", "MB/s", T.VIOLET)
        self.k_lat = Kpi("Latenz Ø", "µs", T.TEAL); self.k_p99 = Kpi("Latenz p99", "µs", T.AMBER); self.k_cpu = Kpi("CPU System", "%", T.PINK)
        for k in (self.k_iops, self.k_mbps, self.k_lat, self.k_p99, self.k_cpu):
            kh.addWidget(k, 1)
        v.addLayout(kh)

        # charts + side
        chart_w = QWidget(); charts = QGridLayout(chart_w); charts.setContentsMargins(0, 0, 0, 0); charts.setSpacing(14)
        chart_w.setMinimumHeight(330)
        self.tick_chip = chip("Tick 250 ms", "chipAccent")
        self.rw = QCheckBox("Lesen/Schreiben getrennt"); self.rw.toggled.connect(self._rw_toggled)
        self.rw.setToolTip("tick.read_iops / write_iops / read_mbps / write_mbps (optional im Vertrag 1.1)")
        self.rw_leg = label(f"<span style='color:{T.ACCENT}'>●</span> Lesen  <span style='color:{T.PINK}'>●</span> Schreiben", "dim")
        self.rw_leg.hide()
        self.c_iops = c_iops = Card("IOPS", "alle Worker · t ab Messbeginn", right=[self.rw_leg, self.rw, self.tick_chip], margins=14)
        self.p_iops = plot(); c_iops.add(self.p_iops, 1)
        self.cur_iops = self.p_iops.plot(pen=pg.mkPen(T.ACCENT, width=2), fillLevel=0, brush=grad_brush(T.ACCENT))
        self.hint = pg.TextItem("", color=T.DIM, anchor=(0.5, 0.5)); self.hint.setFont(QFont(T.UI_FONT, 11)); self.p_iops.addItem(self.hint)
        self.cur_w_iops = self.p_iops.plot(pen=pg.mkPen(T.PINK, width=2), fillLevel=0, brush=grad_brush(T.PINK, 45))
        charts.addWidget(c_iops, 0, 0, 1, 2)
        c_mb = Card("Durchsatz", "MB/s", margins=14); self.p_mb = plot(); c_mb.add(self.p_mb, 1)
        self.cur_mb = self.p_mb.plot(pen=pg.mkPen(T.VIOLET, width=2), fillLevel=0, brush=grad_brush(T.VIOLET))
        self.cur_w_mb = self.p_mb.plot(pen=pg.mkPen(T.PINK, width=2), fillLevel=0, brush=grad_brush(T.PINK, 45))
        self.c_mb = c_mb
        charts.addWidget(c_mb, 1, 0)
        leg = QHBoxLayout(); leg.setSpacing(10)
        for txt, col in (("Ø", T.TEAL), ("p50", T.GREEN), ("p99", T.AMBER)):
            l = label(f"●  {txt}"); l.setStyleSheet(f"color:{col}; font-size:11px;"); leg.addWidget(l)
        lw = QWidget(); lw.setLayout(leg); leg.setContentsMargins(0, 0, 0, 0)
        self.lat_leg = lw
        c_lat = Card("Latenz", "µs", right=[lw], margins=14); self.p_lat = plot(); c_lat.add(self.p_lat, 1); self.c_lat = c_lat
        self.cur_p99 = self.p_lat.plot(pen=pg.mkPen(T.AMBER, width=1.6))
        self.cur_mean = self.p_lat.plot(pen=pg.mkPen(T.TEAL, width=2))
        self.cur_p50 = self.p_lat.plot(pen=pg.mkPen(T.GREEN, width=1.4, style=Qt.DashLine))
        charts.addWidget(c_lat, 1, 1)
        charts.setRowStretch(0, 5); charts.setRowStretch(1, 4)
        # cpu/memory (contract 1.2): metric curve in the top chart, CPU load in the middle chart, per-thread bars (one bar
        # per thread, last tick; 128 threads stay readable, no 128 lines) in the right chart
        self.cur_metric = self.p_iops.plot(pen=pg.mkPen(T.ACCENT, width=2), fillLevel=0, brush=grad_brush(T.ACCENT))
        self.thread_bars = pg.BarGraphItem(x=[], height=[], width=0.8, brush=pg.mkBrush(T.ACCENT), pen=pg.mkPen(None))
        self.p_lat.addItem(self.thread_bars)
        self.thread_avg = pg.InfiniteLine(angle=0, pen=pg.mkPen(T.AMBER, width=1, style=Qt.DashLine)); self.p_lat.addItem(self.thread_avg)
        self.bar_hint = pg.TextItem("", color=T.DIM, anchor=(0.5, 0.5)); self.bar_hint.setFont(QFont(T.UI_FONT, 10)); self.p_lat.addItem(self.bar_hint)
        self.leg_cpu = label(f"<span style='color:{T.VIOLET}'>●</span> System  <span style='color:{T.PINK}'>●</span> sbench", "dim")
        self.c_mb.lay.itemAt(0).layout().insertWidget(2, self.leg_cpu)
        self.bounds = []
        side_w = QWidget(); side = QVBoxLayout(side_w); side.setContentsMargins(0, 0, 0, 0); side.setSpacing(14)
        wc = Card("Worker", "letzter Tick · Worker i → Ziel i mod n", margins=14); wc.setMinimumHeight(170); self.wc = wc
        self.wtable = table(["#", "Ziel", "IOPS", "MB/s", "Ø µs", "p99 µs"], stretch_col=1, mono_cols=(2, 3, 4, 5))
        wc.add(self.wtable, 1)
        # contract 1.3: phase.cpus -> logical processor / NUMA node / SMT sibling per thread
        self.ttable = table(["Thread", "CPU · Knoten", "Wert", "Anteil", "Δ zum Ø"], stretch_col=1, mono_cols=(2, 3, 4)); self.ttable.hide()
        wc.add(self.ttable, 1); side.addWidget(wc, 5)
        lc = Card("Ereignisse", "JSON-Lines von sbench", margins=14); lc.setMinimumHeight(110)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(500); lc.add(self.log, 1)
        side.addWidget(lc, 3)
        v.addWidget(Responsive([(chart_w, 1, None), (side_w, 0, 380)], breakpoint=1080), 1)

        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh)  # 10 Hz redraw, decoupled from tick rate
        self.mode = None; self.cpu_score = None    # meta.cpu_score from sbench info (contract 1.3), set by MainWindow
        self._reset()

    # ------------------------------------------------------------------
    def _reset(self):
        self.buf.clear(); self.offset = 0.0; self.last_raw = 0.0; self.phase = None; self.cur_test = None
        self.total = 0.0; self.last_workers = []; self.targets = []; self.test_idx = 0; self.test_names = []; self.jobtests = []
        self.skipped = []  # job indices the core skipped (E_NOT_IMPLEMENTED, core M1: absent from result tests[])
        self.test_offset = {}; self.waiting = True; self.prep_bar.hide()
        self.test_threads = {}; self.test_cpus = {}     # contract 1.3: first phase event per test (threads, cpus[])
        self.prev_workers = {}; self.lagging = set(); self.smoothed = None     # storage: worker that missed the 250 ms ack shows 0, recounted next tick
        for p, ln in getattr(self, "bounds", []):
            p.removeItem(ln)
        self.bounds = []
        self._set_phase(None)
        self._apply_mode("storage")

    def attach(self, source, job=None, title=None, tick_ms=250, reattached=False):
        self._reset(); self.source = source; self.has_rw = False; self.rw.setEnabled(False)
        self.tick_chip.setText(f"Tick {tick_ms} ms")
        if job:
            self.jobtests = job["tests"]
            self.test_names = [test_label(t) for t in job["tests"]]
            self.total = sum(t.get("duration_s", 0) for t in job["tests"])  # x axis = measurement time only
            self._describe(0, title or job.get("name", ""))
        self.log.clear()
        if reattached:
            self.log.appendPlainText("# wieder verbunden: lese runs/<run_id>.jsonl von Anfang an (tail)")
        source.event.connect(self.on_event)
        source.finished.connect(self.on_finished)
        self.timer.start(100)

    def _describe(self, idx, title=None):
        if not self.jobtests:
            return
        t = self.jobtests[idx]
        if title:
            self.job_title = title
        self.title.setText(f"Live-Lauf · {getattr(self, 'job_title', '')}  ·  Test {idx + 1}/{len(self.jobtests)}")
        if t["type"] == "storage":
            self.targets = t["targets"]
            self.subtitle.setText(f"{self.test_names[idx]}  ·  {t.get('workers', 1)} Worker × QD {t.get('queue_depth', 1)}  ·  "
                                  f"{len(t['access_specs'])} Access Specs  ·  {t.get('cache', 'unbuffered')}  ·  "
                                  f"{len(self.targets)} Ziele")
        elif t["type"] in ("cpu", "memory"):
            self.subtitle.setText("  ·  ".join([self.test_names[idx]] + _compute_facts(t, self.test_threads.get(idx))))
        else:
            self.subtitle.setText(self.test_names[idx])
        self._apply_mode(expected_metric(t) or ("storage" if t["type"] == "storage" else self.mode))

    def _cfg(self, idx):
        return self.jobtests[idx] if isinstance(idx, int) and 0 <= idx < len(self.jobtests) else None

    def _apply_mode(self, mode):
        """storage (IOPS/MB/s/latency) or a contract-1.2 metric (ops_per_s | gbps | latency_ns): relabel KPIs/cards."""
        if mode == self.mode or mode is None:
            return
        self.mode = mode; comp = mode != "storage"
        for w in (self.rw, self.rw_leg if self.rw.isChecked() else None, self.lat_leg, self.wtable):
            if w is not None:
                w.setVisible(not comp)
        for w in (self.leg_cpu, self.ttable):
            w.setVisible(comp)
        for c in (self.cur_iops, self.cur_w_iops, self.cur_mb, self.cur_w_mb, self.cur_mean, self.cur_p50, self.cur_p99, self.cur_metric):
            c.setData([], [])
        self.thread_bars.setOpts(x=[], height=[]); self.thread_bars.setVisible(comp); self.thread_avg.setVisible(False); self.bar_hint.setText("")
        self.cur_metric.setVisible(comp)
        for c in (self.cur_iops, self.cur_w_iops, self.cur_mean, self.cur_p50, self.cur_p99):
            c.setVisible(not comp)
        for p, ln in self.bounds:
            ln.setVisible(not comp)          # compute charts show only the current test
        if not comp:
            self.k_iops.set_title("IOPS", ""); self.k_mbps.set_title("Durchsatz", "MB/s"); self.k_lat.set_title("Latenz Ø", "µs")
            self.k_p99.set_title("Latenz p99", "µs")
            self.c_iops.title_label.setText("IOPS"); self.c_iops.subtitle_label.setText("alle Worker · t ab Messbeginn")
            self.c_mb.title_label.setText("Durchsatz"); self.c_mb.subtitle_label.setText("MB/s")
            self.c_lat.title_label.setText("Latenz"); self.c_lat.subtitle_label.setText("µs")
            self.wc.title_label.setText("Worker"); self.wc.subtitle_label.setText("letzter Tick · Worker i → Ziel i mod n")
            for k in (self.k_iops, self.k_mbps, self.k_lat, self.k_p99):
                k.set("–", " ")
            return
        name, unit = METRIC_TXT[mode]
        self.k_iops.set_title(name, unit)
        if mode == "ops_per_s":
            self.k_mbps.set_title("Score", ""); self.k_lat.set_title("Threads", ""); self.k_p99.set_title("Streuung Threads", "%")
            self.c_iops.title_label.setText("Operationen pro Sekunde"); self.c_mb.subtitle_label.setText("% aller logischen Prozessoren")
        elif mode == "gbps":
            self.k_mbps.set_title("Pro Thread Ø", "GB/s"); self.k_lat.set_title("Threads", ""); self.k_p99.set_title("Streuung Threads", "%")
            self.c_iops.title_label.setText("Speicherbandbreite"); self.c_mb.subtitle_label.setText("% aller logischen Prozessoren")
        else:
            self.k_mbps.set_title("Latenz min", "ns"); self.k_lat.set_title("Puffer", ""); self.k_p99.set_title("Latenz max", "ns")
            self.c_iops.title_label.setText("Speicherlatenz"); self.c_mb.subtitle_label.setText("% aller logischen Prozessoren")
            self.wc.subtitle_label.setText("Latenz misst immer mit 1 Thread (kein workers[])")
        self.c_iops.subtitle_label.setText(f"{unit} · aktueller Test · t ab Messbeginn")
        self.c_mb.title_label.setText("CPU-Last")
        self.c_lat.title_label.setText("Pro Thread"); self.c_lat.subtitle_label.setText(f"{unit} · letzter Tick · gestrichelt = Ø")
        self.wc.title_label.setText("Threads")
        if mode != "latency_ns":
            self.wc.subtitle_label.setText("letzter Tick · tick.workers[]")
        for k in (self.k_iops, self.k_mbps, self.k_lat, self.k_p99):
            k.set("–", " ")

    def _rw_toggled(self, on):
        self.rw_leg.setVisible(on)
        self.c_iops.title_label.setText("IOPS · Lesen / Schreiben" if on else "IOPS")
        self.c_mb.title_label.setText("Durchsatz · Lesen / Schreiben" if on else "Durchsatz")
        self.refresh()

    def _set_phase(self, name):
        self.phase = name
        order = [k for k, _ in PHASES]
        for k, c in self.phase_chips.items():
            if name and k == name:
                c.setObjectName("chipAccent")
            elif name and order.index(k) < order.index(name):
                c.setObjectName("chipOk")
            else:
                c.setObjectName("chip")
            c.style().unpolish(c); c.style().polish(c)

    def on_event(self, ev):
        e = ev.get("ev")
        if e != "tick":
            self.log.appendPlainText(_short(ev))
        elif abs(ev["t"] - round(ev["t"])) < 1e-6 and round(ev["t"]) % 2 == 0:
            k = tick_metric(ev, self._cfg(ev.get("test")))
            if k == "iops":
                self.log.appendPlainText(f'tick t={ev["t"]:>4.0f}  iops={ev["iops"]}  p99={(ev.get("lat_us") or {}).get("p99")}')
            else:
                self.log.appendPlainText(f'tick t={ev["t"]:>4.0f}  {k}={ev.get(k)}  cpu={ev.get("cpu_pct")}  w={len(ev.get("workers") or []) or 1}')
        if e == "start":
            self.run_chip.setText(f"läuft · {ev.get('run_id')}"); self.run_chip.setObjectName("chipAccent")
            self.run_chip.style().polish(self.run_chip)
        elif e == "phase":
            self._set_phase(ev["name"]); self.phase_total = ev.get("duration_s")
            if "threads" in ev:          # contract 1.3: resolved thread/worker count (also for threads: 0) + bound CPUs
                self.test_threads[ev["test"]] = ev["threads"]
                if ev.get("cpus"):
                    self.test_cpus[ev["test"]] = {c["id"]: c for c in ev["cpus"]}
            if ev["test"] != self.test_idx or "threads" in ev:
                self.test_idx = ev["test"]; self._describe(self.test_idx)
            if ev["name"] != "preparing":
                self.prep_bar.hide()
            comp = self.mode not in (None, "storage")
            if ev["name"] in ("preparing", "warmup") and (not self.buf.n or (comp and ev["test"] != self.cur_test)):
                self.waiting = True
                tot = (self._cfg(ev["test"]) or {}).get("duration_s", 1) if comp else (self.total or 60)
                for pl in (self.p_iops, self.p_mb, self.p_lat):
                    pl.setXRange(0, tot, padding=0.01); pl.setYRange(0, 1, padding=0)
                if comp:   # new cpu/memory test: nothing of the previous test may stay visible
                    self.cur_metric.setData([], []); self.cur_mb.setData([], []); self.cur_w_mb.setData([], [])
                    self.thread_bars.setOpts(x=[], height=[]); self.thread_avg.setVisible(False); self.bar_hint.setText("")
                    self.last_workers = []; self.ttable.setRowCount(0)
                    for k in (self.k_iops, self.k_mbps, self.k_lat, self.k_p99, self.k_cpu):
                        k.set("–", " ")
                    n = self.test_threads.get(ev["test"])
                    if n and self.mode != "latency_ns":
                        self.k_lat.set(str(n), self._cpu_summary(ev["test"]) or " ")
                self.hint.setText("Messwerte erscheinen ab der Messphase (Vorbereitung → Warmup → Messung)"); self.hint.setPos(tot / 2, 0.5)
            if ev["name"] in ("preparing", "warmup", "cleanup"):
                self.progress.setValue(0); self.time_lbl.setText({"preparing": "Vorbereitung …", "warmup": "Warmup …", "cleanup": "Cleanup …"}[ev["name"]])
        elif e == "progress":  # contract 1.1: preparing (bytes) / warmup (s), ~1/s
            tot = ev["total"] or 1; frac = min(1, ev["done"] / tot); self.progress.setValue(int(frac * 1000))
            cfg = self._cfg(ev.get("test")) or {}
            if ev["unit"] == "bytes" and (cfg.get("type") == "memory" or (not cfg and self.mode in ("gbps", "latency_ns"))):
                # contract 1.2: memory buffers (threads x buffer_size) are allocated + written once (first touch)
                done_txt = f"{human_bytes(ev['done'])} / {human_bytes(ev['total'])}"
                n = self.test_threads.get(ev.get("test"))   # 1.3: total = threads x actual buffer size (also copy)
                if n and n > 1:
                    done_txt += f"  ({n} Threads × {human_bytes(ev['total'] // n, 'MiB' if ev['total'] // n < 1 << 30 else 'GiB')})"
                self.time_lbl.setText(f"Puffer {done_txt}")
                if ev["total"] >= PREP_BAR_MIN:
                    self.prep_bar.setValue(int(frac * 1000)); self.prep_bar.setFormat(f"Puffer wird angelegt  ·  {done_txt}  ·  {frac * 100:.0f} %")
                    self.prep_bar.show()
                self.hint.setText(f"Puffer wird angelegt … {frac * 100:.0f} %  (Messwerte ab der Messphase)")
            elif ev["unit"] == "bytes":
                self.time_lbl.setText(f"Testdatei {human_bytes(ev['done'])} / {human_bytes(ev['total'])}")
            else:
                self.time_lbl.setText(f"Warmup {_mmss(ev['done'])} / {_mmss(ev['total'])}")
        elif e == "tick":
            t = ev["t"]
            if self.cur_test is not None and ev["test"] != self.cur_test:  # t restarts per test (measure start)
                self.offset += self.last_raw
                for p in (self.p_iops, self.p_mb, self.p_lat):
                    ln = pg.InfiniteLine(pos=self.offset, angle=90, pen=pg.mkPen(T.DIM, width=1, style=Qt.DashLine)); p.addItem(ln)
                    self.bounds.append((p, ln))
            if ev["test"] not in self.test_offset:
                self.test_offset[ev["test"]] = self.offset
            self.cur_test = ev["test"]; self.last_raw = t
            mk = tick_metric(ev, self._cfg(ev["test"]))
            if mk in FUTURE_METRICS or (mk not in ("iops", None) and mk not in METRIC_TXT):
                return                       # M3 network (mbps) / ping (latency_us): no live mode yet -> event log only
            self._apply_mode("storage" if mk in ("iops", None) else mk)
            if self.waiting:
                self.waiting = False; self.prep_bar.hide()
            l = ev.get("lat_us") or {}
            # latency missing in an interval (no completed I/O) -> NaN: the line breaks there instead of dropping to 0
            self.buf.append([self.offset + t, ev.get("iops", 0), ev.get("mbps", 0), l.get("mean", NAN), l.get("p50", NAN),
                             l.get("p99", NAN), l.get("max", NAN), ev.get("cpu_pct", 0),
                             ev.get("read_iops", NAN), ev.get("write_iops", NAN), ev.get("read_mbps", NAN), ev.get("write_mbps", NAN),
                             ev.get("proc_cpu_pct", NAN), ev["test"], ev.get("ops_per_s", NAN), ev.get("gbps", NAN), ev.get("latency_ns", NAN),
                             ev.get("score", NAN)])
            if "read_iops" in ev and not self.has_rw:
                self.has_rw = True; self.rw.setEnabled(True)
            ws = ev.get("workers") or []
            if mk == "iops":
                # contract 1.3: a worker that did not ack the interval switch within 250 ms is 0 in this tick and its I/O
                # counts towards the next tick -> not an error; the table shows the mean of the last two ticks and marks it
                self.lagging = {w["id"] for w in ws if not w.get("iops") and (self.prev_workers.get(w["id"]) or {}).get("iops")}
                self.smoothed = [self._smooth(w) for w in ws]
                self.prev_workers = {w["id"]: w for w in ws}
            self.last_workers = ws
        elif e == "error" and ev.get("code") == "E_NOT_IMPLEMENTED":
            ti = ev.get("test")
            if isinstance(ti, int) and ti not in self.skipped:
                self.skipped.append(ti)
                if 0 <= ti < len(self.jobtests):  # x axis = measurement time of tests that actually run
                    self.total = max(0.0, self.total - self.jobtests[ti].get("duration_s", 0))
            self.run_chip.setText(f"{len(self.skipped)} Test(s) übersprungen (nicht implementiert)"); self.run_chip.setObjectName("chipWarn")
            self.run_chip.style().polish(self.run_chip)
        elif e == "error":
            self.run_chip.setText(f"Fehler: {ERROR_TXT[ev['code']]}" if ev.get("code") in ERROR_TXT else f"Fehler {ev.get('code')}")
            self.run_chip.setToolTip(f"{ev.get('code')}: {ev.get('msg', '')}"); self.run_chip.setObjectName("chipErr"); self.run_chip.style().polish(self.run_chip)
        elif e == "done":
            self._set_phase("done")
            self.result_ready.emit(ev["result"])

    def on_finished(self, code, text):
        self.refresh(); self.timer.stop()
        a = self.buf.array()
        el = _mmss(a[-1, 0]) if len(a) else "00:00"
        self.time_lbl.setText({0: f"Fertig · {el} gemessen", 3: f"Gestoppt · {el} gemessen"}.get(code, f"Beendet (Exit {code})"))
        skip = f" · {len(self.skipped)} übersprungen" if self.skipped else ""
        self.run_chip.setText(f"beendet · {text} ({code}){skip}"); self.run_chip.setObjectName("chipOk" if code == 0 and not skip else "chipWarn")
        self.run_chip.style().unpolish(self.run_chip); self.run_chip.style().polish(self.run_chip)
        if code == 4:
            self.elevate_requested.emit()  # GUI offers 'Als Administrator neu starten' (runas sbench.exe + named pipe)

    def refresh(self):
        a = self.buf.array()
        if not len(a) or (self.waiting and self.mode not in (None, "storage")):
            return
        if self.mode not in (None, "storage"):
            return self._refresh_compute(a)
        x = a[:, 0]; self.hint.setText("")
        split = self.rw.isChecked() and self.has_rw
        for cur, col in ((self.cur_iops, 8 if split else 1), (self.cur_mb, 10 if split else 2)):
            cur.setData(*minmax_downsample(x, np.nan_to_num(a[:, col])))
        for cur, col in ((self.cur_mean, 3), (self.cur_p50, 4), (self.cur_p99, 5)):
            cur.setData(*minmax_downsample(x, a[:, col]), connect="finite")      # NaN = gap, no interpolation
        for cur, col in ((self.cur_w_iops, 9), (self.cur_w_mb, 11)):
            if split:
                cur.setData(*minmax_downsample(x, np.nan_to_num(a[:, col])))
            else:
                cur.setData([], [])
        for p in (self.p_iops, self.p_mb, self.p_lat):
            p.setXRange(0, max(self.total, x[-1]) * 1.0, padding=0.01)
        p99 = a[:, 5][np.isfinite(a[:, 5])]
        self.p_lat.setYRange(0, (np.percentile(p99, 98) if len(p99) else 1) * 1.15 or 1, padding=0)
        self.p_iops.setYRange(0, a[:, 1].max() * 1.12, padding=0); self.p_mb.setYRange(0, a[:, 2].max() * 1.12, padding=0)
        last = a[-1]; win = a[-4:]
        meas = a
        if self.has_rw and win[:, 8].mean() == win[:, 8].mean():
            self.k_iops.set(fmt_num(win[:, 1].mean()), f"Lesen {fmt_num(win[:, 8].mean())}  ·  Schreiben {fmt_num(win[:, 9].mean())}")
        else:
            self.k_iops.set(fmt_num(win[:, 1].mean()), f"Ø Lauf {fmt_num(meas[:, 1].mean())}  ·  max {fmt_num(meas[:, 1].max())}")
        self.k_mbps.set(f"{win[:, 2].mean():,.0f}".replace(",", " "), f"Ø Lauf {meas[:, 2].mean():,.0f} MB/s (dezimal)".replace(",", " "))
        self.k_lat.set(_nf(win[:, 3]), f"min {_nf(meas[:, 3], np.nanmin)}  ·  max {_nf(meas[:, 3], np.nanmax)} µs")
        self.k_p99.set(_nf(win[:, 5]), f"max {_nf(a[:, 6], np.nanmax)} µs")
        pc = last[12]
        self.k_cpu.set(f"{last[7]:.0f}", (f"sbench-Prozess {pc:.0f} %  ·  " if pc == pc else "") + f"Ø System {meas[:, 7].mean():.0f} %")
        if self.phase == "measure":
            el = x[-1]; self.progress.setValue(int(min(1, el / self.total) * 1000) if self.total else 0)
            self.time_lbl.setText(f"Messung {_mmss(el)} / {_mmss(self.total)}")

        if self.last_workers and self.wtable.rowCount() != len(self.last_workers):
            self.wtable.setRowCount(0)
            for w in self.last_workers:
                add_row(self.wtable, [w["id"], "", "", "", "", ""])
        sm = getattr(self, "smoothed", None) or self.last_workers
        for i, w in enumerate(sm):
            tgt = short_target(self.targets[w["id"] % len(self.targets)]["path"]) if self.targets else "–"  # worker i -> target i mod n
            wl = w.get("lat_us") or {}  # optional per worker; 1.3: absent in intervals without completed I/O -> "–"
            lag = w["id"] in self.lagging
            for c, val in ((0, f"{w['id']}{' ↻' if lag else ''}"), (1, tgt), (2, fmt_num(w.get("iops"))),
                           (3, f"{w.get('mbps', 0):,.0f}".replace(",", " ")),
                           (4, f"{wl['mean']:.0f}" if "mean" in wl else "–"), (5, f"{wl['p99']:,.0f}".replace(",", " ") if "p99" in wl else "–")):
                it = self.wtable.item(i, c); it.setText(str(val))
                if c == 0:
                    it.setForeground(QColor(T.DIM if lag else T.TEXT))
                    it.setToolTip("Worker hat den Tick-Wechsel nicht innerhalb von 250 ms quittiert (z. B. lange I/O): 0 in diesem Tick, "
                                  "sein Intervall zählt zum nächsten Tick. Angezeigt: Mittel der letzten zwei Ticks." if lag else "")


    def _smooth(self, w):
        """Storage worker row = mean of this and the previous tick (a late ack moves I/O from one tick to the next)."""
        p = self.prev_workers.get(w["id"])
        if not p:
            return w
        out = dict(w)
        for k in ("iops", "mbps"):
            if isinstance(w.get(k), (int, float)) and isinstance(p.get(k), (int, float)):
                out[k] = (w[k] + p[k]) / 2
        # lat_us absent (no completed I/O in this interval) stays absent -> table shows "–" (Devend: no last value)
        return out

    def _cpu_summary(self, test):
        """'CPUs 0–63 · Knoten 0, 1 · 64 SMT' from phase.cpus (contract 1.3)."""
        cpus = list((self.test_cpus.get(test) or {}).values())
        if not cpus:
            return ""
        nodes = sorted({c["numa_node"] for c in cpus}); smt = sum(1 for c in cpus if c.get("smt_sibling_of") is not None)
        return f"Knoten {', '.join(map(str, nodes))}" + (f" · {smt} SMT" if smt else " · ohne SMT")

    def _refresh_compute(self, a):
        """cpu/memory: current test only (values of different workloads differ by orders of magnitude)."""
        key = self.mode; col = COL[key]; self.hint.setText("")
        cur = a[a[:, COL["test"]] == self.cur_test] if self.cur_test is not None else a
        cur = cur[np.isfinite(cur[:, col])]
        if not len(cur):
            return
        off = self.test_offset.get(self.cur_test, 0.0)
        x = cur[:, 0] - off; y = cur[:, col]
        self.cur_metric.setData(*minmax_downsample(x, y))
        cfg = self._cfg(self.cur_test) or {}
        dur = max(cfg.get("duration_s") or 0, x[-1])
        self.p_iops.setXRange(0, dur, padding=0.01)
        self.p_iops.setYRange(0, max(y.max(), 1e-9) * 1.15, padding=0)
        cpu = cur[:, COL["cpu"]]; proc = cur[:, COL["proc_cpu"]]
        self.cur_mb.setData(*minmax_downsample(x, cpu))
        if np.isfinite(proc).any():
            self.cur_w_mb.setData(*minmax_downsample(x, np.nan_to_num(proc)))
        self.p_mb.setXRange(0, dur, padding=0.01)
        top = max(np.nanmax(cpu), np.nanmax(proc) if np.isfinite(proc).any() else 0)
        self.p_mb.setYRange(0, min(100, max(2.0, top * 1.25)), padding=0)
        win = y[-4:]; now = float(win.mean())
        sub = f"Ø Test {fmt_metric(key, float(y.mean()))}  ·  min {fmt_metric(key, float(y.min()))}  ·  max {fmt_metric(key, float(y.max()))}"
        self.k_iops.set(fmt_metric(key, now), sub)
        vals = [w.get(key) for w in self.last_workers if isinstance(w.get(key), (int, float))]
        if key == "ops_per_s":
            wl = cfg.get("workload", "mixed") if cfg else None
            sco = cur[:, COL["score"]]; sco = sco[np.isfinite(sco)]
            meta = {"cpu_score": self.cpu_score} if self.cpu_score else None
            ref, from_core = score_reference(wl, meta) if wl else (None, False)
            if len(sco):                 # contract 1.3: tick.score from the core
                self.k_mbps.set_title("Score", "")
                self.k_mbps.set(fmt_metric("score", float(sco[-4:].mean())),
                                f"vom Kern · Ø Test {fmt_metric('score', float(sco.mean()))}"
                                + (f" · Ref. {fmt_metric('ops_per_s', ref)} ops/s" if ref and from_core else ""))
            else:                        # cores < 1.3: estimate (labelled), reference from info meta.cpu_score if known
                sc = estimated_score(wl, now, meta) if wl else None
                self.k_mbps.set_title("Score (Schätzung)", "")
                self.k_mbps.set(fmt_metric("score", sc) if sc is not None else "–",
                                f"Kern < 1.3 · 1000 = 1 Ref.-Thread ({fmt_metric('ops_per_s', ref)} ops/s {wl})"
                                if sc is not None else "Workload unbekannt (Job fehlt)")
        elif key == "gbps":
            self.k_mbps.set(fmt_metric("gbps", now / max(1, len(vals))), "Summe ÷ Threads" + (" · copy zählt Lesen + Schreiben" if cfg.get("op") == "copy" else ""))
        else:
            self.k_mbps.set(fmt_metric(key, float(y.min())), "abhängige Ladezugriffe, 1 Thread")
            self.k_p99.set(fmt_metric(key, float(y.max())), f"Ø Test {fmt_metric(key, float(y.mean()))} ns")
            bs = cfg.get("buffer_size")
            self.k_lat.set(_iec(bs) if bs else "–", "≫ L3 = RAM-Latenz, klein = Cache" if bs else " ")
        if key != "latency_ns":
            n = len(vals) or self.test_threads.get(self.cur_test, 0)
            self.k_lat.set(str(n or "–"), f"Thread min {fmt_metric(key, min(vals))}  ·  max {fmt_metric(key, max(vals))}" if vals
                           else (self._cpu_summary(self.cur_test) or " "))
            if vals:
                m = sum(vals) / n; spread = (max(vals) - min(vals)) / m * 100 if m else 0
                slow = min(range(n), key=lambda i: vals[i])
                self.k_p99.set(f"{spread:.1f}".replace(".", ","), f"langsamster Thread #{self.last_workers[slow].get('id', slow)}")
        pc = cur[-1, COL["proc_cpu"]]
        self.k_cpu.set(f"{cur[-1, COL['cpu']]:.1f}".replace(".", ","), (f"sbench-Prozess {pc:.1f} %  ·  ".replace(".", ",") if pc == pc else "")
                       + f"Ø {float(np.nanmean(cpu)):.1f} %".replace(".", ","))
        if self.phase == "measure":
            el = x[-1]; self.progress.setValue(int(min(1, el / dur) * 1000) if dur else 0)
            self.time_lbl.setText(f"Messung {_mmss(el)} / {_mmss(cfg.get('duration_s') or dur)}")
        # per-thread bars (last tick) + thread table
        if vals:
            m = sum(vals) / len(vals); med = float(np.median(vals))
            ids = [w.get("id", i) for i, w in enumerate(self.last_workers) if isinstance(w.get(key), (int, float))]
            cpus = self.test_cpus.get(self.cur_test) or {}
            smt = {i for i in ids if (cpus.get(i) or {}).get("smt_sibling_of") is not None}   # 1.3: SMT sibling of an earlier thread
            brushes = [pg.mkBrush(T.AMBER if v < 0.9 * med else (T.VIOLET if i in smt else T.ACCENT)) for i, v in zip(ids, vals)]
            self.c_lat.subtitle_label.setText(f"{METRIC_TXT[key][1]} · letzter Tick · gestrichelt = Ø"
                                              + (" · violett = SMT-Geschwister" if smt else ""))
            self.thread_bars.setOpts(x=ids, height=vals, width=0.8 if len(vals) < 48 else 1.0, brushes=brushes)
            self.thread_avg.setValue(m); self.thread_avg.setVisible(True); self.bar_hint.setText("")
            self.p_lat.setXRange(-0.7, max(ids) + 0.7, padding=0); self.p_lat.setYRange(0, max(vals) * 1.15, padding=0)
            if self.ttable.rowCount() != len(vals):
                self.ttable.setRowCount(0)
                for i in ids:
                    c = cpus.get(i)
                    r_ = add_row(self.ttable, [f"#{i}", _cpu_txt(c), "", "", ""], {1: T.VIOLET if i in smt else T.MUTED})
                    if c:
                        self.ttable.item(r_, 1).setToolTip(
                            f"logischer Prozessor {c['cpu']} (Gruppe {c['group']}, Nr. {c['number']}) · NUMA-Knoten {c['numa_node']} · Kern {c['core']}"
                            + (f" · SMT-Geschwister von CPU {c['smt_sibling_of']}" if c.get("smt_sibling_of") is not None else ""))
            tot = sum(vals)
            # cpu: per-thread score = ops_per_s / ref × 1000 with ref from the core (meta.cpu_score), else share of the sum
            meta = {"cpu_score": self.cpu_score} if self.cpu_score else None
            wl = (cfg or {}).get("workload", "mixed")
            per_score = key == "ops_per_s" and worker_score(1.0, wl, meta) is not None
            hdr = "Score" if per_score else "Anteil"
            if self.ttable.horizontalHeaderItem(3).text() != hdr:
                self.ttable.horizontalHeaderItem(3).setText(hdr)
                self.ttable.horizontalHeaderItem(3).setToolTip("ops/s des Threads ÷ Referenz × 1000 (Referenz vom Kern, meta.cpu_score)"
                                                               if per_score else "Anteil an der Summe aller Threads")
            for r, v in enumerate(vals):
                self.ttable.item(r, 2).setText(fmt_metric(key, v))
                self.ttable.item(r, 3).setText(fmt_metric("score", worker_score(v, wl, meta)) if per_score else
                                               (f"{v / tot * 100:.1f} %".replace(".", ",") if tot else "–"))
                d = (v - m) / m * 100 if m else 0
                self.ttable.item(r, 4).setText(f"{d:+.1f} %".replace(".", ","))
                self.ttable.item(r, 4).setForeground(QColor(T.AMBER if v < 0.9 * med else T.MUTED))
        else:
            self.thread_bars.setOpts(x=[], height=[]); self.thread_avg.setVisible(False)
            self.p_lat.setXRange(0, 1, padding=0); self.p_lat.setYRange(0, 1, padding=0); self.bar_hint.setPos(0.5, 0.5)
            self.bar_hint.setText("Latenz: immer 1 Thread –\nkeine Werte pro Thread" if key == "latency_ns" else "keine workers[] im Tick")
            if self.ttable.rowCount():
                self.ttable.setRowCount(0)


def _nf(col, fn=np.nanmean):
    """Latency KPI from a column that may contain NaN (intervals without latency): '–' when all are missing."""
    v = col[np.isfinite(col)]
    return f"{fn(v):,.0f}".replace(",", " ") if len(v) else "–"


def _cpu_txt(c):
    """Thread table cell: logical processor, NUMA node, SMT marker (contract 1.3 phase.cpus[])."""
    if not c:
        return "–"
    return f"{c['cpu']} · N{c['numa_node']}" + (" · SMT" if c.get("smt_sibling_of") is not None else "")


def _compute_facts(t, resolved=None):
    """Live subtitle facts for cpu/memory jobs (contract 1.2 job fields; 1.3: resolved thread count from phase.threads)."""
    th = t.get("threads", 0)
    def thr():
        if not th:
            return f"alle Threads ({resolved})" if resolved else "alle Threads"
        return f"{th} Thread{'s' if th != 1 else ''}"
    if t["type"] == "cpu":
        out = [f"Workload {t.get('workload', 'mixed')}", thr()]
    else:
        bs = _iec(t["buffer_size"])
        if t["mode"] == "latency":
            out = ["Latenz (Pointer Chasing, 1 Thread)", f"Puffer {bs}"]
        else:
            out = [f"Bandbreite {t.get('op', 'read')}" + (" NT" if t.get("nontemporal") else ""),
                   thr(), f"{bs}/Thread", f"SIMD {t.get('simd', 'auto')}"]
        if "mem_node" in t:
            out.append(f"Knoten {t.get('numa_node', '*')} → RAM {t['mem_node']}")
    if "numa_node" in t and not (t["type"] == "memory" and "mem_node" in t):
        out.append(f"NUMA-Knoten {t['numa_node']}")
    out.append(f"Warmup {t.get('warmup_s', 1):g} s")
    return out


def _iec(size):
    """'1GiB' / 268435456 -> '1 GiB' / '256 MiB' (job sizes are powers of 1024)."""
    b = size_bytes(size); ss = short_size(b)
    return f"{ss[:-1]} {ss[-1]}iB" if ss[-1] in "KMG" else ss


def _mmss(s):
    s = int(round(s)); return f"{s // 60:02d}:{s % 60:02d}"


def _short(ev):
    """One log line per event; 1.3 phase events carry cpus[] with one entry per thread (128+) -> shown as a count."""
    import json
    if isinstance(ev.get("cpus"), list):
        ev = {k: (f"[{len(v)} CPUs]" if k == "cpus" else v) for k, v in ev.items()}
    return json.dumps(ev, separators=(",", ":"), ensure_ascii=False)[:120]
