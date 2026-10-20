#!/usr/bin/env python3
"""Headless end-to-end check against the mock sbench (contract 1.3; mock default):
 1. exit 4 (raw disk without admin) -> elevation hint      2. destructive job without --allow-destructive -> exit 2
 3. live run via QProcess/stdout, R/W split + worker latency, stop -> exit 3, result read only after done
 3b. contract 1.2/1.3: cpu + memory run, live metric/thread bars, stop during memory measure -> exit 3, partial result
     1.3: tick.metric on every tick, phase.threads/cpus -> thread KPI, memory test partial with stopped_in=measure, tick_ms
 3c. runtime error (mock SBENCH_MOCK_FAIL_AT, --keep-files semantics via targets[]) -> exit 1, partial test with errors[],
     results page shows the error box and the leftover-file hint
 4. re-attach: run started outside the GUI -> sbench list --running -> TailSession follows runs/<id>.jsonl to done
 5. support bundle with --include (GUI logs + profiles) and --anonymize -> manifest/sha256 validated
Writes $SBENCH_SHOT_DIR (default build/)/03c_live_qprocess.png. Uses temp SBENCH_DATA_DIR / SBENCH_GUI_DIR."""
import json, os, subprocess, sys, tempfile, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["SBENCH_DATA_DIR"] = tempfile.mkdtemp(prefix="sbd-"); os.environ["SBENCH_GUI_DIR"] = tempfile.mkdtemp(prefix="sbg-")
from sbgui.app import make_app, parse, build_window
from sbgui import sbench
from PySide6.QtCore import QCoreApplication, QEventLoop

import logging
sbench.setup_logging(); logging.getLogger('e2e').info('start')
app = make_app()
win = build_window(parse([]), deterministic=False); win.setFixedSize(1440, 900); win.show()
win.pages["live"].elevate_requested.disconnect()
report = {"data_dir_from_info": win.data_dir == os.environ["SBENCH_DATA_DIR"]}


def wait(cond, timeout):
    t0 = time.time()
    while not cond() and time.time() - t0 < timeout:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 50); time.sleep(0.02)
    return cond()


def run(job, opts, tick=250, stop_after=None):
    codes, results, evs = [], [], []
    win.start_run(job, opts, tick_ms=tick); s = win.session
    s.finished.connect(lambda c, t: codes.append(c)); s.event.connect(evs.append)
    win.pages["live"].result_ready.connect(results.append)
    if stop_after:
        # stop only once the measure phase delivers ticks (mock: ~7 s preparing + 2 s warm-up for four-corners);
        # a fixed delay is too short on slow runners and would stop during preparing (no R/W split, no worker stats)
        wait(lambda: sum(1 for e in evs if e.get("ev") == "tick") >= stop_after, 45); win.pages["live"].refresh()
        sd = os.environ.get("SBENCH_SHOT_DIR", os.path.join(ROOT, "build")); os.makedirs(sd, exist_ok=True); win.grab().save(os.path.join(sd, "03c_live_qprocess.png")); win.stop_run()
    wait(lambda: codes, 25)
    win.pages["live"].result_ready.disconnect()
    return codes[:1], results[:1], evs


elev = []
win.pages["live"].elevate_requested.connect(lambda: elev.append(1))
c, _, _ = run(os.path.join(ROOT, "demo", "job-oltp.json"), {"allow_destructive": True})
report["privilege_exit"] = c; report["elevate_offered"] = bool(elev)
c, _, evs = run(os.path.join(ROOT, "demo", "job-oltp.json"), {"allow_destructive": False})
report["destructive_exit"] = c; report["destructive_code"] = [e.get("code") for e in evs if e.get("ev") == "error"][:1]

c, res, evs = run(os.path.join(ROOT, "profiles", "four-corners.json"), {}, stop_after=6)   # stop after 6 ticks
live = win.pages["live"]
report["stop_exit"] = c; report["result_after_done"] = bool(res) and os.path.exists(res[0])
report["progress_events"] = sum(1 for e in evs if e.get("ev") == "progress")
report["rw_split_available"] = live.has_rw; report["worker_lat_shown"] = live.wtable.rowCount() > 0 and live.wtable.item(0, 5).text() not in ("", "–")
if res:
    r = json.load(open(res[0], encoding="utf-8")); report["result_aborted"] = r["aborted"]

# 3b) contract 1.2: cpu + memory, stop during the memory measure phase -> exit 3, cpu complete + memory partial
m2 = os.path.join(os.environ["SBENCH_GUI_DIR"], "job-m2-e2e.json")
json.dump({"schema": "sb-job/1", "name": "M2 E2E", "tests": [
    {"type": "cpu", "name": "CPU int 4", "threads": 4, "workload": "int", "warmup_s": 0, "duration_s": 2},
    {"type": "memory", "name": "RAM lesen", "mode": "bandwidth", "op": "read", "threads": 4, "buffer_size": "256MiB", "warmup_s": 0,
     "duration_s": 60}]}, open(m2, "w", encoding="utf-8"))
modes = []
codes, res, evs = [], [], []
win.start_run(m2, {}, tick_ms=250); s = win.session
s.finished.connect(lambda c, t: codes.append(c)); s.event.connect(evs.append); live.result_ready.connect(res.append)
wait(lambda: any(e.get("ev") == "tick" and e.get("test") == 0 for e in evs), 30); live.refresh(); modes.append(live.mode)
report["m2_cpu_thread_bars"] = len(live.thread_bars.opts["height"])
report["m2_preparing_seen"] = wait(lambda: any(e.get("ev") == "progress" and e.get("phase") == "preparing" for e in evs), 30)
wait(lambda: sum(1 for e in evs if e.get("ev") == "tick" and e.get("test") == 1) >= 4, 45); live.refresh(); modes.append(live.mode)
ticks = [e for e in evs if e.get("ev") == "tick"]
report["m2_metric_on_ticks"] = bool(ticks) and all(e.get("metric") in e for e in ticks)
report["m2_cpu_tick_score"] = all("score" in e for e in ticks if e.get("metric") == "ops_per_s")
report["m2_phase_threads"] = dict(sorted(live.test_threads.items()))
win.stop_run(); wait(lambda: codes, 25); live.result_ready.disconnect()
report["m2_modes"] = modes; report["m2_stop_exit"] = codes[:1]
if res:
    r = json.load(open(res[0], encoding="utf-8"))
    report["m2_result"] = {"aborted": r["aborted"], "tests": [t["type"] for t in r["tests"]],
                           "gbps": r["tests"][-1]["summary"].get("gbps") if r["tests"] else None,
                           "partial": [(t.get("partial"), t.get("stopped_in")) for t in r["tests"]], "tick_ms": r.get("tick_ms")}
    chk = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "check_mock.py"), res[0]], capture_output=True, text=True)
    report["m2_result_valid"] = chk.returncode == 0

# 3c) runtime error: E_IO after 2 ticks of the storage test -> exit 1, partial, 2 errors[]; results page error box
fj = os.path.join(os.environ["SBENCH_GUI_DIR"], "job-fail-e2e.json")
json.dump({"schema": "sb-job/1", "name": "Fehler E2E", "tests": [
    {"type": "storage", "name": "4K Fehler", "targets": [{"kind": "file", "path": os.path.join(os.environ["SBENCH_GUI_DIR"], "f.dat"), "file_size": "64MiB"}],
     "access_specs": [{"block_size": "4KiB", "read_pct": 70, "random_pct": 100, "weight": 100}], "workers": 2, "warmup_s": 0, "duration_s": 20},
    {"type": "cpu", "threads": 1, "workload": "int", "warmup_s": 0, "duration_s": 1}]}, open(fj, "w", encoding="utf-8"))
os.environ["SBENCH_MOCK_FAIL_AT"] = "0:E_IO:2:2"
c, res, evs = run(fj, {})
del os.environ["SBENCH_MOCK_FAIL_AT"]
report["fail_exit"] = c; report["fail_error_event"] = [e.get("code") for e in evs if e.get("ev") == "error"][:1]
if res:
    r = json.load(open(res[0], encoding="utf-8"))
    report["fail_result"] = {"tests": len(r["tests"]), "partial": r["tests"][0]["partial"], "errors": len(r["tests"][0]["errors"]),
                             "aborted": r["aborted"]}
    rp = win.pages["results"]
    if rp.add_result(res[0]) and rp.select_file(res[0]):
        report["fail_results_page"] = rp.err_box.isVisibleTo(rp) and bool(rp.err_more) and rp.state.text().startswith("Laufzeitfehler")

# 4) re-attach
env = dict(os.environ, SBENCH_MOCK_SPEED="4")
rid = "reattach-1"
p = subprocess.Popen([sys.executable, os.path.join(ROOT, "mock_sbench.py"), "run", "--job", os.path.join(ROOT, "profiles", "mix-60-40-example.json"),
                      "--out", os.path.join(os.environ["SBENCH_GUI_DIR"], "results", rid + ".json"), "--id", rid, "--allow-destructive"],
                     env=dict(env, SBENCH_MOCK_ELEVATED="1"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
wait(lambda: False, 1.5)
dlg = win.check_running(); report["reattach_offered"] = dlg is not None
codes = []
if dlg:
    dlg.close_accept(("attach", dlg._sel())); win.session.finished.connect(lambda c, t: codes.append(c))
    wait(lambda: codes, 40)
report["reattach_exit"] = codes[:1]; report["reattach_ticks"] = int(live.buf.n)
p.wait(10)

# 5) bundle
out = os.path.join(tempfile.gettempdir(), "sbench-e2e-bundle.zip")
bp = win._bundle_save([], {"logs": True, "profiles": True, "anonymize": True}, out_path=out)
done = []; bp.finished.connect(lambda c, s: done.append(c)); wait(lambda: done, 20)
report["bundle_exit"] = done[:1]
chk = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "check_mock.py"), out], capture_output=True, text=True)
report["bundle_valid"] = chk.returncode == 0
import zipfile
man = json.loads(zipfile.ZipFile(out).read("manifest.json")) if os.path.exists(out) else {}
report["bundle_includes"] = man.get("includes"); report["bundle_anonymized"] = man.get("anonymized")
report["bundle_has_gui_log"] = any(f["path"].startswith("extra/logs/") for f in man.get("files", []))

print(json.dumps(report, indent=1))
ok = (report["privilege_exit"] == [4] and report["destructive_exit"] == [2] and report["stop_exit"] == [3]
      and report.get("result_aborted") is True and report["rw_split_available"] and report["worker_lat_shown"]
      and report["reattach_exit"] == [0] and report["reattach_ticks"] > 5 and report["bundle_exit"] == [0] and report["bundle_valid"]
      and report["bundle_anonymized"] is True and report["bundle_has_gui_log"] and report["data_dir_from_info"]
      and report["m2_modes"] == ["ops_per_s", "gbps"] and report["m2_cpu_thread_bars"] == 4 and report["m2_preparing_seen"]
      and report["m2_stop_exit"] == [3] and report.get("m2_result_valid")
      and (report.get("m2_result") or {}).get("tests") == ["cpu", "memory"] and report["m2_result"]["aborted"] is True
      and report["m2_result"]["partial"] == [(False, None), (True, "measure")] and report["m2_result"]["tick_ms"] == 250
      and report["fail_exit"] == [1] and report["fail_error_event"] == ["E_IO"] and report.get("fail_results_page")
      and report.get("fail_result") == {"tests": 1, "partial": True, "errors": 2, "aborted": False}
      and report["m2_metric_on_ticks"] and report["m2_cpu_tick_score"] and report["m2_phase_threads"] == {0: 4, 1: 4})
sys.exit(0 if ok else 1)
