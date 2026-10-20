import numpy as np, pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QLinearGradient, QBrush, QFont
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton, QProgressBar, QPlainTextEdit, QCheckBox
from ..widgets import Card, Kpi, label, chip, table, add_row, plot, fmt_num, short_target, Responsive
from ..buffers import RingBuffer, minmax_downsample
from ..model import test_label, human_bytes
from .. import theme as T

FIELDS = ("t", "iops", "mbps", "lat_mean", "lat_p50", "lat_p99", "lat_max", "cpu", "r_iops", "w_iops", "r_mbps", "w_mbps", "proc_cpu")
NAN = float("nan")
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
        pc.add(ph); v.addWidget(pc)

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
        c_lat = Card("Latenz", "µs", right=[lw], margins=14); self.p_lat = plot(); c_lat.add(self.p_lat, 1)
        self.cur_p99 = self.p_lat.plot(pen=pg.mkPen(T.AMBER, width=1.6))
        self.cur_mean = self.p_lat.plot(pen=pg.mkPen(T.TEAL, width=2))
        self.cur_p50 = self.p_lat.plot(pen=pg.mkPen(T.GREEN, width=1.4, style=Qt.DashLine))
        charts.addWidget(c_lat, 1, 1)
        charts.setRowStretch(0, 5); charts.setRowStretch(1, 4)
        self.bounds = []
        side_w = QWidget(); side = QVBoxLayout(side_w); side.setContentsMargins(0, 0, 0, 0); side.setSpacing(14)
        wc = Card("Worker", "letzter Tick · Worker i → Ziel i mod n", margins=14); wc.setMinimumHeight(170)
        self.wtable = table(["#", "Ziel", "IOPS", "MB/s", "Ø µs", "p99 µs"], stretch_col=1, mono_cols=(2, 3, 4, 5))
        wc.add(self.wtable, 1); side.addWidget(wc, 5)
        lc = Card("Ereignisse", "JSON-Lines von sbench", margins=14); lc.setMinimumHeight(110)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(500); lc.add(self.log, 1)
        side.addWidget(lc, 3)
        v.addWidget(Responsive([(chart_w, 1, None), (side_w, 0, 380)], breakpoint=1080), 1)

        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh)  # 10 Hz redraw, decoupled from tick rate
        self._reset()

    # ------------------------------------------------------------------
    def _reset(self):
        self.buf.clear(); self.offset = 0.0; self.last_raw = 0.0; self.phase = None; self.cur_test = None
        self.total = 0.0; self.last_workers = []; self.targets = []; self.test_idx = 0; self.test_names = []; self.jobtests = []
        for p, ln in getattr(self, "bounds", []):
            p.removeItem(ln)
        self.bounds = []
        self._set_phase(None)

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
            self.log.appendPlainText(f'tick t={ev["t"]:>4.0f}  iops={ev["iops"]}  p99={ev["lat_us"]["p99"]}')
        if e == "start":
            self.run_chip.setText(f"läuft · {ev.get('run_id')}"); self.run_chip.setObjectName("chipAccent")
            self.run_chip.style().polish(self.run_chip)
        elif e == "phase":
            self._set_phase(ev["name"]); self.phase_total = ev.get("duration_s")
            if ev["name"] in ("preparing", "warmup") and not self.buf.n:
                tot = self.total or 60
                for pl in (self.p_iops, self.p_mb, self.p_lat):
                    pl.setXRange(0, tot, padding=0.01); pl.setYRange(0, 1, padding=0)
                self.hint.setText("Messwerte erscheinen ab der Messphase (Vorbereitung → Warmup → Messung)"); self.hint.setPos(tot / 2, 0.5)
            if ev["name"] in ("preparing", "warmup", "cleanup"):
                self.progress.setValue(0); self.time_lbl.setText({"preparing": "Vorbereitung …", "warmup": "Warmup …", "cleanup": "Cleanup …"}[ev["name"]])
            if ev["test"] != self.test_idx:
                self.test_idx = ev["test"]; self._describe(self.test_idx)
        elif e == "progress":  # contract 1.1: preparing (bytes) / warmup (s), ~1/s
            tot = ev["total"] or 1; self.progress.setValue(int(min(1, ev["done"] / tot) * 1000))
            if ev["unit"] == "bytes":
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
            self.cur_test = ev["test"]; self.last_raw = t
            l = ev.get("lat_us", {})
            self.buf.append([self.offset + t, ev.get("iops", 0), ev.get("mbps", 0), l.get("mean", 0), l.get("p50", 0),
                             l.get("p99", 0), l.get("max", 0), ev.get("cpu_pct", 0),
                             ev.get("read_iops", NAN), ev.get("write_iops", NAN), ev.get("read_mbps", NAN), ev.get("write_mbps", NAN),
                             ev.get("proc_cpu_pct", NAN)])
            if "read_iops" in ev and not self.has_rw:
                self.has_rw = True; self.rw.setEnabled(True)
            self.last_workers = ev.get("workers") or []
        elif e == "error":
            self.run_chip.setText(f"Fehler {ev.get('code')}"); self.run_chip.setObjectName("chipErr"); self.run_chip.style().polish(self.run_chip)
        elif e == "done":
            self._set_phase("done")
            self.result_ready.emit(ev["result"])

    def on_finished(self, code, text):
        self.refresh(); self.timer.stop()
        self.run_chip.setText(f"beendet · {text} ({code})"); self.run_chip.setObjectName("chipOk" if code == 0 else "chipWarn")
        self.run_chip.style().unpolish(self.run_chip); self.run_chip.style().polish(self.run_chip)
        if code == 4:
            self.elevate_requested.emit()  # GUI offers 'Als Administrator neu starten' (runas sbench.exe + named pipe)

    def refresh(self):
        a = self.buf.array()
        if not len(a):
            return
        x = a[:, 0]; self.hint.setText("")
        split = self.rw.isChecked() and self.has_rw
        for cur, col in ((self.cur_iops, 8 if split else 1), (self.cur_mb, 10 if split else 2), (self.cur_mean, 3), (self.cur_p50, 4), (self.cur_p99, 5)):
            cur.setData(*minmax_downsample(x, np.nan_to_num(a[:, col])))
        for cur, col in ((self.cur_w_iops, 9), (self.cur_w_mb, 11)):
            if split:
                cur.setData(*minmax_downsample(x, np.nan_to_num(a[:, col])))
            else:
                cur.setData([], [])
        for p in (self.p_iops, self.p_mb, self.p_lat):
            p.setXRange(0, max(self.total, x[-1]) * 1.0, padding=0.01)
        self.p_lat.setYRange(0, np.percentile(a[:, 5], 98) * 1.15, padding=0)
        self.p_iops.setYRange(0, a[:, 1].max() * 1.12, padding=0); self.p_mb.setYRange(0, a[:, 2].max() * 1.12, padding=0)
        last = a[-1]; win = a[-4:]
        meas = a
        if self.has_rw and win[:, 8].mean() == win[:, 8].mean():
            self.k_iops.set(fmt_num(win[:, 1].mean()), f"Lesen {fmt_num(win[:, 8].mean())}  ·  Schreiben {fmt_num(win[:, 9].mean())}")
        else:
            self.k_iops.set(fmt_num(win[:, 1].mean()), f"Ø Lauf {fmt_num(meas[:, 1].mean())}  ·  max {fmt_num(meas[:, 1].max())}")
        self.k_mbps.set(f"{win[:, 2].mean():,.0f}".replace(",", " "), f"Ø Lauf {meas[:, 2].mean():,.0f} MB/s (dezimal)".replace(",", " "))
        self.k_lat.set(f"{win[:, 3].mean():.0f}", f"min {meas[:, 3].min():.0f}  ·  max {meas[:, 3].max():.0f} µs")
        self.k_p99.set(f"{win[:, 5].mean():,.0f}".replace(",", " "), f"max {last[6]:,.0f} µs".replace(",", " "))
        pc = last[12]
        self.k_cpu.set(f"{last[7]:.0f}", (f"sbench-Prozess {pc:.0f} %  ·  " if pc == pc else "") + f"Ø System {meas[:, 7].mean():.0f} %")
        if self.phase == "measure":
            el = x[-1]; self.progress.setValue(int(min(1, el / self.total) * 1000) if self.total else 0)
            self.time_lbl.setText(f"Messung {_mmss(el)} / {_mmss(self.total)}")

        if self.last_workers and self.wtable.rowCount() != len(self.last_workers):
            self.wtable.setRowCount(0)
            for w in self.last_workers:
                add_row(self.wtable, [w["id"], "", "", "", "", ""])
        for i, w in enumerate(self.last_workers):
            tgt = short_target(self.targets[w["id"] % len(self.targets)]["path"]) if self.targets else "–"  # worker i -> target i mod n
            wl = w.get("lat_us") or {}  # optional per worker (1.1)
            for c, val in ((1, tgt), (2, fmt_num(w.get("iops"))), (3, f"{w.get('mbps', 0):,.0f}".replace(",", " ")),
                           (4, f"{wl['mean']:.0f}" if "mean" in wl else "–"), (5, f"{wl['p99']:,.0f}".replace(",", " ") if "p99" in wl else "–")):
                self.wtable.item(i, c).setText(str(val))


def _mmss(s):
    s = int(round(s)); return f"{s // 60:02d}:{s % 60:02d}"


def _short(ev):
    import json
    return json.dumps(ev, separators=(",", ":"))[:120]
