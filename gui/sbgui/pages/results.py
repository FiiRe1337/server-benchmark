"""Ergebnisse: one sb-result/1 in detail. Contract 1.2 (M2): cpu score per workload + threads, memory GB/s per mode
(read/write/copy, NT) + instruction set, latency in ns, NUMA placement (numa_node -> mem_node) and RAM per node with
its source (meta.numa_nodes[].ram_method). Storage tests as a compact summary table.
Contract 1.3: partial/stopped_in per test (null summary values when stopped before measure), core score reference
(meta.cpu_score, summary.score_reference), time series at tick_ms; fatal errors[] of a test (first one prominent, the
rest expandable), storage targets[] created/deleted (warning for files left behind), per-thread score and (1.3.1) SMT
from the test's cpus[]."""
import json, os
import numpy as np, pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QFrame, QToolButton
from ..widgets import Card, label, chip, table, add_row, plot, fmt_num, Responsive, WrapLabel
from ..model import (check_schema, host_label, human_bytes, numa_cpus, storage_tests, test_label, summary_metric, fmt_metric,
                     compute_details, numa_placement, RAM_METHOD_TXT, METRIC_TXT, short_size, sval, is_partial, partial_text,
                     has_values, TYPE_TXT, fatal_errors, error_text, test_cpus, leftover_targets, worker_score, run_outcome,
                     OUTCOME_TXT)
from .. import theme as T


def _local(utc):
    import datetime as dt
    return dt.datetime.fromisoformat(utc.replace("Z", "+00:00")).astimezone().strftime("%d.%m.%Y %H:%M")


class ResultsPage(QWidget):
    def __init__(self, result_files, exit_codes=None):
        """exit_codes: {run_id: exit_code} or a callable returning it (MainWindow: <data_dir>/runs/index.jsonl). The exit
        code decides 'abgebrochen' (3) vs 'Laufzeitfehler' (1); without one the result is classified heuristically."""
        super().__init__(); self.setObjectName("page")
        self.exit_codes = exit_codes
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
        # contract 1.3: which test stopped where ("abgebrochen in Warmup · keine Messwerte")
        self.partial_note = WrapLabel("", "muted"); self.partial_note.setObjectName("partialNote")
        self.partial_note.setStyleSheet(f"QLabel#partialNote {{ color: {T.AMBER}; border: 1px solid {T.AMBER}; border-radius: 8px; padding: 8px 12px; }}")
        v.addWidget(self.partial_note)
        # runtime errors that ended a test: first one prominent, further ones behind "▸ n weitere"
        self.err_box = QFrame(); self.err_box.setObjectName("errBox")
        self.err_box.setStyleSheet(f"QFrame#errBox {{ border: 1px solid {T.RED}; border-radius: 8px; }}")
        self.err_lay = QVBoxLayout(self.err_box); self.err_lay.setContentsMargins(12, 8, 12, 8); self.err_lay.setSpacing(4)
        self.err_box.hide(); v.addWidget(self.err_box)
        self.err_more = []          # (toggle button, details label) per test, for tests

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
        # targets[] (created/deleted): small info line + warning when sbench left a test file behind (--keep-files)
        self.tgt_info = WrapLabel("", "dim"); self.st_card.add(self.tgt_info)
        self.tgt_warn = WrapLabel("", "muted"); self.tgt_warn.setStyleSheet(f"color: {T.AMBER};"); self.st_card.add(self.tgt_warn)
        self.empty = label("Keine Ergebnisse vorhanden. Nach einem Lauf erscheint das Ergebnis hier.", "muted")
        for w in (self.sys_card, self.cpu_card, self.mem_card, self.th_card, self.st_card, self.empty):
            v.addWidget(w)
        v.addStretch(1)
        self.box.currentIndexChanged.connect(self.show_index)
        self.tsel.currentIndexChanged.connect(self._show_thread)
        self.show_index(0)

    # ------------------------------------------------------------------
    def add_result(self, path):
        """Append one more sb-result/1 file (e.g. a 1.3.1 demo outside the result folder); returns False if unreadable."""
        try:
            r = check_schema(json.load(open(path, encoding="utf-8")), "sb-result/1")
        except (OSError, ValueError):
            return False
        self.results.append((path, r))
        self.box.addItem(f"{host_label(r['meta'])} · {_local(r['started_utc'])} · {os.path.basename(path)}", path)
        return True

    def exit_code_for(self, r):
        """Exit code of this result's run from the run index, None if unknown (demo files, other machines)."""
        src = self.exit_codes() if callable(self.exit_codes) else (self.exit_codes or {})
        return src.get(r.get("run_id"))

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
        self.empty.setVisible(r is None); self.partial_note.setVisible(False); self._errors([])
        if r is None:
            self.state.setText("kein Ergebnis"); return
        m = r["meta"]
        self.sub.setText(f"{host_label(m)} · Kern {r['kern_version']} · Lauf {r.get('run_id', '–')} · {_local(r['started_utc'])}")
        parts = [t for t in r["tests"] if is_partial(t, r)]
        failed = [t for t in parts if fatal_errors(t)]
        code = self.exit_code_for(r)
        self.outcome = outcome = run_outcome(r, code)
        ended = outcome in ("aborted", "failed")
        self.state.setText(OUTCOME_TXT[outcome] + (" · Teilergebnis" if ended else "") if outcome != "ok" or not parts else "Teilergebnis")
        self.state.setToolTip(f"Exit-Code {code} (runs/index.jsonl)" if code is not None else
                              "ohne Exit-Code: aus dem Ergebnis abgeleitet (errors[] → Laufzeitfehler, aborted → abgebrochen)")
        self.state.setObjectName("chipErr" if outcome in ("failed", "invalid", "privileges") else ("chipWarn" if ended or parts else "chipOk"))
        if parts:
            lines = []
            for t in parts:
                lines.append(f"Test {t['index'] + 1} ({TYPE_TXT.get(t['type'], 'Storage')}) „{test_label(t['config'])}“: {partial_text(t, r)}"
                             + (" · Laufzeitfehler (siehe unten)" if fatal_errors(t) else ""))
            if ended:
                lines.append("Nachfolgende Tests des Jobs wurden nicht gestartet und fehlen im Ergebnis.")
            self.partial_note.setText("\n".join(lines)); self.partial_note.setVisible(True)
        self._errors(failed)
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

        cs = m.get("cpu_score") or {}
        self.cpu_card.subtitle_label.setText(
            f"Score = ops/s ÷ Referenz × {cs.get('points', 1000):g} (Werte vom Kern; Referenz: {cs['reference_system']})" if cs.get("reference_system")
            else "Score = ops/s ÷ Referenz-Thread × 1000 (höher = besser)")
        self.cpu_card.subtitle_label.setToolTip(cs.get("formula", ""))
        cpu_t = [t for t in r["tests"] if t["type"] == "cpu"]
        mem_t = [t for t in r["tests"] if t["type"] == "memory"]
        self.cpu_card.setVisible(bool(cpu_t)); self.mem_card.setVisible(bool(mem_t)); self.th_card.setVisible(bool(cpu_t or mem_t))
        self.cpu_tab.setRowCount(0)
        for t in cpu_t:
            s = t.get("summary") or {}
            r_ = add_row(self.cpu_tab, [self._name(t, r), s.get("workload", t["config"].get("workload", "–")), s.get("threads", "–"),
                                        numa_placement(t) or "alle", fmt_metric("ops_per_s", s.get("ops_per_s")), fmt_metric("score", s.get("score"))],
                         {5: T.ACCENT, 0: T.AMBER if is_partial(t, r) else None})
            self._tip(self.cpu_tab, r_, t, r)
            if s.get("score_reference"):
                self.cpu_tab.item(r_, 5).setToolTip(f"Referenz {fmt_metric('ops_per_s', s['score_reference'])} ops/s (1 Thread, {s.get('workload')})")
        self._fit(self.cpu_tab)
        self._bars(self.p_cpu, [test_label(t["config"]) for t in cpu_t], [sval(t, "summary", "score") for t in cpu_t], T.ACCENT, "Score")
        self.mem_tab.setRowCount(0)
        for t in mem_t:
            s = t.get("summary") or {}; bw = s.get("mode", t["config"].get("mode")) == "bandwidth"
            mode = (s.get("op", "read") + (" NT" if s.get("nontemporal") else "")) if bw else "Latenz"
            r_ = add_row(self.mem_tab, [self._name(t, r), mode, s.get("simd", "–") if bw else "–", s.get("threads", "–"),
                                        short_size(s["buffer_size_bytes"]) if s.get("buffer_size_bytes") else "–", numa_placement(t) or "lokal",
                                        fmt_metric("gbps", s.get("gbps")) if bw else "–", fmt_metric("latency_ns", s.get("latency_ns")) if not bw else "–"],
                         {6: T.VIOLET, 7: T.TEAL, 5: T.AMBER if "remote" in (numa_placement(t) or "") else None,
                          0: T.AMBER if is_partial(t, r) else None})
            self._tip(self.mem_tab, r_, t, r)
        self._fit(self.mem_tab)
        bwt = [t for t in mem_t if sval(t, "summary", "mode") == "bandwidth"]
        self._bars(self.p_mem, [test_label(t["config"]) for t in bwt], [sval(t, "summary", "gbps") for t in bwt], T.VIOLET, "GB/s")
        self.tsel.blockSignals(True); self.tsel.clear()
        for t in cpu_t + mem_t:
            self.tsel.addItem(test_label(t["config"]), t["index"])
        self.tsel.blockSignals(False)
        self._show_thread(0)
        st = storage_tests(r)
        self.st_card.setVisible(bool(st)); self.st_tab.setRowCount(0)
        for t in st:
            s = t.get("summary") or {}; l = s.get("lat_us") or {}
            num = lambda v: "–" if v is None else f"{v:,.0f}".replace(",", " ")   # noqa: E731  (1.3: null = not measured)
            r_ = add_row(self.st_tab, [self._name(t, r), fmt_num(s.get("iops")), num(s.get("mbps")), num(l.get("mean")), num(l.get("p99")),
                                       "–" if s.get("errors") is None else s["errors"]], {0: T.AMBER if is_partial(t, r) else None})
            self._tip(self.st_tab, r_, t, r)
        self._fit(self.st_tab)
        self._targets(st)

    def _errors(self, tests):
        """Fatal errors[] per test: first entry prominent (red), the rest expandable. ERROR_TXT for agreed codes, else code + msg."""
        while self.err_lay.count():
            it = self.err_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
            elif it.layout() is not None:
                lay = it.layout()
                while lay.count():
                    x = lay.takeAt(0).widget()
                    if x is not None:
                        x.deleteLater()
        self.err_more = []
        for t in tests:
            errs = fatal_errors(t)
            head = WrapLabel(f"Test {t['index'] + 1} „{test_label(t['config'])}“ – {error_text(errs[0])}", "errHead")
            head.setStyleSheet(f"color: {T.RED}; font-weight: 600;"); head.setToolTip(f"{errs[0].get('code')}: {errs[0].get('msg', '')}")
            self.err_lay.addWidget(head)
            if len(errs) > 1:
                rest = WrapLabel("\n".join(error_text(e) for e in errs[1:]), "muted"); rest.hide()
                btn = QToolButton(); btn.setCheckable(True); btn.setAutoRaise(True)
                n = len(errs) - 1
                btn.setText(f"▸ {n} weitere{'r' if n == 1 else ''} Fehler")
                btn.toggled.connect(lambda on, b=btn, l=rest, n=n: (l.setVisible(on), b.setText(f"{'▾' if on else '▸'} {n} weitere{'r' if n == 1 else ''} Fehler")))
                self.err_lay.addWidget(btn, 0, Qt.AlignLeft); self.err_lay.addWidget(rest)
                self.err_more.append((btn, rest))
        self.err_box.setVisible(bool(tests))

    def _targets(self, st):
        """targets[] per storage test: 'angelegt · gelöscht' etc.; warning with path when a created file was not deleted."""
        info, left = [], []
        for t in st:
            for g in t.get("targets") or []:
                state = ("angelegt · gelöscht" if g.get("deleted") else "angelegt · nicht gelöscht") if g.get("created") else "vorhanden (nicht angelegt)"
                info.append(f"{g.get('path')} ({state})")
            left += [g["path"] for g in leftover_targets(t)]
        uniq = list(dict.fromkeys(info))
        self.tgt_info.setText(("Ziele: " + " · ".join(uniq[:4]) + (f" · … (+{len(uniq) - 4})" if len(uniq) > 4 else "")) if uniq else "")
        self.tgt_info.setVisible(bool(uniq))
        left = list(dict.fromkeys(left))
        self.tgt_warn.setText(("⚠ Testdatei nicht gelöscht (z. B. --keep-files oder Löschen fehlgeschlagen) – belegt weiter Speicherplatz: "
                               + ", ".join(left)) if left else "")
        self.tgt_warn.setVisible(bool(left))

    @staticmethod
    def _name(t, res):
        return test_label(t["config"]) + (" †" if is_partial(t, res) else "")

    @staticmethod
    def _tip(tab, row, t, res):
        p = partial_text(t, res)
        if p:
            tab.item(row, 0).setToolTip(p)

    @staticmethod
    def _fit(t):
        t.setFixedHeight(32 * max(1, t.rowCount()) + 38)

    @staticmethod
    def _bars(p, names, vals, col, unit):
        p.clear()
        vals = [v if v is not None else 0 for v in vals]      # 1.3: null (stopped before measure) -> no bar
        if not vals:
            return
        x = np.arange(len(vals))
        p.addItem(pg.BarGraphItem(x=x, height=vals, width=0.6, brush=pg.mkBrush(col), pen=pg.mkPen(None)))
        ax = p.getAxis("bottom"); ax.setTicks([[(i, _short(n)) for i, n in enumerate(names)]])
        p.setXRange(-0.6, len(vals) - 0.4, padding=0); p.setYRange(0, max(vals) * 1.15 or 1, padding=0)
        p.getPlotItem().setTitle(f"<span style='color:{T.MUTED};font-size:9pt'>{unit}</span>")

    def _show_thread(self, _i=0):
        r = self.current()
        self.p_thr.clear(); self.p_ts.clear(); self.thr_scores = {}; self.thr_smt = set()
        if r is None or self.tsel.currentIndex() < 0:
            self.th_info.setText(""); return
        idx = self.tsel.currentData()
        t = next(x for x in r["tests"] if x["index"] == idx)
        key, total = summary_metric(t)
        name, unit = METRIC_TXT[key]
        pw = [w for w in t.get("per_worker", []) if isinstance(w.get(key), (int, float))]
        if pw:
            vals = [w[key] for w in pw]; med = float(np.median(vals))
            # cpu: bars = per-thread score (ops/s ÷ core reference × 1000), when the core gave a reference
            s = t.get("summary") or {}; wl = s.get("workload") or t["config"].get("workload")
            scores = [worker_score(v, wl, r.get("meta"), s) for v in vals] if key == "ops_per_s" else []
            use_score = bool(scores) and all(x is not None for x in scores)
            bars = scores if use_score else vals
            lowb = (lambda v: v > 1.1 * med) if key == "latency_ns" else (lambda v: v < 0.9 * med)
            tcpus = test_cpus(t)           # 1.3.1: cpus[] per test -> SMT siblings (mapped via worker id); 1.3: none
            smt = {w["id"] for w in pw if (tcpus.get(w["id"]) or {}).get("smt_sibling_of") is not None}
            self.p_thr.addItem(pg.BarGraphItem(x=[w["id"] for w in pw], height=bars, width=0.8 if len(pw) < 48 else 1.0,
                                               brushes=[pg.mkBrush(T.AMBER if lowb(v) else (T.VIOLET if w["id"] in smt else T.ACCENT))
                                                        for w, v in zip(pw, vals)], pen=pg.mkPen(None)))
            self.p_thr.setXRange(-0.7, len(pw) - 0.3, padding=0); self.p_thr.setYRange(0, max(bars) * 1.15 or 1, padding=0)
            ttl = "Score je Thread (ops/s ÷ Referenz × 1000)" if use_score else f"{unit} je Thread"
            self.p_thr.getPlotItem().setTitle(f"<span style='color:{T.MUTED};font-size:9pt'>{ttl}"
                                              + (" · violett = SMT-Geschwister" if smt else "") + "</span>")
            self.thr_scores = dict(zip([w["id"] for w in pw], scores)) if use_score else {}
            self.thr_smt = smt
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
            if use_score:
                facts.append(f"Score je Thread {fmt_metric('score', min(scores))} – {fmt_metric('score', max(scores))}")
            if smt:
                facts.append(f"{len(smt)} SMT-Geschwister")
            elif not tcpus and key != "latency_ns":
                facts.append("SMT nur live (Kern < 1.3.1)")
            facts += compute_details(t)[:1]
            self.th_info.setText("  ·  ".join(facts))
        elif not has_values(t):
            self.th_info.setText(f"Keine Messwerte: {partial_text(t, r)}" + (f" · {t['summary']['threads']} Thread(s) vorgesehen"
                                                                              if sval(t, "summary", "threads") else ""))
        else:
            self.th_info.setText("keine per_worker-Werte")
        ts = [p for p in t.get("timeseries", []) if key in p]
        if ts:
            x = [p["t"] for p in ts]; y = [p[key] for p in ts]
            self.p_ts.plot(x, y, pen=pg.mkPen(T.VIOLET, width=2), symbol="o", symbolSize=5, symbolBrush=T.VIOLET, symbolPen=None)
            self.p_ts.setYRange(0, max(y) * 1.15 or 1, padding=0)
            iv = f"{r['tick_ms'] / 1000:g} s".replace(".", ",") if r.get("tick_ms") else "1 s"      # 1.3: timeseries at tick_ms
            self.p_ts.getPlotItem().setTitle(f"<span style='color:{T.MUTED};font-size:9pt'>{name} über die Zeit ({unit}, je {iv})</span>")


def _short(name, n=14):
    return name if len(name) <= n else name[: n - 1] + "…"
