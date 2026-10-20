#!/usr/bin/env python3
"""Headless end-to-end check against the mock sbench (contract 1.1):
 1. exit 4 (raw disk without admin) -> elevation hint      2. destructive job without --allow-destructive -> exit 2
 3. live run via QProcess/stdout, R/W split + worker latency, stop -> exit 3, result read only after done
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
      and report["bundle_anonymized"] is True and report["bundle_has_gui_log"] and report["data_dir_from_info"])
sys.exit(0 if ok else 1)
