"""`sbench-gui --guide <dir>`: screenshots for the README user guide, taken from the REAL GUI driving the REAL core.

Nothing is faked: `info` comes from the core, the runs are real storage runs on a test file (no admin rights needed),
the live view is captured during a running measurement, the stop is a real `sbench stop`, the comparison shows the two
real runs of this session and the support bundle is created by `sbench bundle --all --anonymize`.
Used by .github/workflows/screenshots.yml on windows-2022 (windows platform plugin, 100 % scale, fixed window size).

Files (one per README step, plus a few sub-steps):
  02_start.png          window incl. title bar (GUI + core version) right after start, page Übersicht (screen capture)
  03_testprofil.png     Testprofile: guide profile (targets, access specs, workers x queue depth), validated by the core
  03b_ohne_rechte.png   only if the GUI is not elevated: offer to start without admin rights (file target)
  04_live.png           Live-Lauf during the measurement phase of the first test (70/30 mix, read/write split on)
  04b_gestoppt.png      Live-Lauf after "Stoppen" (exit 3, partial result)
  05_vergleich.png      Vergleich of the two complete runs (2 worker x QD 8 vs. 1 worker x QD 1)
  06_export.png         Export page with "Anonymisieren" checked
  06b_support_dialog.png  support dialog (bundle --all, anonymize)
  06c_support_fertig.png  confirmation after the bundle was written
report.json: sizes, layout checks (scrollbars / clipping), run ids, exit codes, key numbers of the runs.
"""
import json, os, sys, tempfile, time

GUIDE_SPECS = {
    "mix": {"type": "storage", "name": "4K random 70/30",
            "access_specs": [
                {"name": "4K random lesen", "weight": 70, "block_size": "4KiB", "read_pct": 100, "random_pct": 100, "align": "4KiB"},
                {"name": "4K random schreiben", "weight": 30, "block_size": "4KiB", "read_pct": 0, "random_pct": 100, "align": "4KiB"}]},
    "seq": {"type": "storage", "name": "64K sequenziell lesen",
            "access_specs": [{"name": "64K seq lesen", "weight": 100, "block_size": "64KiB", "read_pct": 100, "random_pct": 0, "align": "64KiB"}]},
}


def guide_job(name, target, workers, qd, duration_s=12, warmup_s=3, file_size="512MiB"):
    """Iometer-style job: one weighted 70/30 mix test + one sequential test on the same file target."""
    import copy
    tests = []
    for key in ("mix", "seq"):
        t = copy.deepcopy(GUIDE_SPECS[key])
        t.update({"targets": [{"path": target, "kind": "file", "file_size": file_size}], "workers": workers, "queue_depth": qd,
                  "warmup_s": warmup_s, "duration_s": duration_s, "cache": "unbuffered"})
        tests.append({k: t[k] for k in ("type", "name", "targets", "workers", "queue_depth", "warmup_s", "duration_s", "cache", "access_specs")})
    return {"schema": "sb-job/1", "name": name, "tests": tests}


def run(a):
    from .app import make_app, build_window
    from . import sbench, core
    from .model import sval, run_outcome, EXIT_OUTCOME
    out = os.path.abspath(a.guide); os.makedirs(out, exist_ok=True)
    w, h = (int(x) for x in a.size.split("x"))
    target = a.guide_target or os.path.join(tempfile.gettempdir(), "sbench-anleitung.dat")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    rep = {"schema": "sbench-gui-guide/1", "ok": False, "size": a.size, "target": target, "shots": {}, "runs": {}, "errors": []}

    def save_report():
        with open(os.path.join(out, "report.json"), "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1, ensure_ascii=False)

    sbench.setup_logging()
    gd = sbench.gui_dir()
    profiles = {"A": ("Anleitung: 2 Worker × QD 8", 2, 8), "B": ("Anleitung: 1 Worker × QD 1", 1, 1)}
    paths = {}
    for k, (name, wk, qd) in profiles.items():
        paths[k] = os.path.join(gd, "profiles", f"anleitung-{k.lower()}.json")
        with open(paths[k], "w", encoding="utf-8") as f:
            json.dump(guide_job(name, target, wk, qd), f, indent=1, ensure_ascii=False)

    app = make_app(scale=a.scale)
    from PySide6.QtCore import QCoreApplication, QEventLoop
    from PySide6.QtGui import QGuiApplication
    from .overlay import Overlay
    from .app import _select

    def pump(sec=0.0):
        t0 = time.time()
        while True:
            QCoreApplication.processEvents(QEventLoop.AllEvents, 50)
            if time.time() - t0 >= sec:
                return
            time.sleep(0.02)

    def wait(pred, timeout, what):
        t0 = time.time()
        while not pred():
            if time.time() - t0 > timeout:
                raise TimeoutError(f"Zeitüberschreitung ({timeout} s): {what}")
            pump(0.1)

    rep["platform"] = QGuiApplication.platformName()
    rep["gui_version"] = core.gui_version()
    try:
        win = build_window(a, deterministic=False, extra_results=[os.path.join(gd, "results")])
        rep["core"] = {"exe": core.get().exe, "data_dir": core.get().data_dir, "version": win.kern_version}
        win.setFixedSize(w, h); win.move(30, 30); win.show(); win.raise_(); win.activateWindow()
        pump(1.5)
        scr = QGuiApplication.primaryScreen()
        rep["screen"] = [scr.geometry().width(), scr.geometry().height(), scr.devicePixelRatio()]
        rep["window"] = [win.width(), win.height()]

        def layout_info():
            sa = win.stack.currentWidget()
            return {"viewport": [sa.viewport().width(), sa.viewport().height()],
                    "h_scroll": sa.horizontalScrollBar().maximum() > 0, "v_scroll": sa.verticalScrollBar().maximum() > 0}

        def shot(name, frame=False):
            pump(0.4)
            path = os.path.join(out, f"{name}.png")
            img, how = None, "widget"
            if frame:       # real screen capture incl. title bar (needs an interactive desktop that fits the window)
                fg = win.frameGeometry(); sg = scr.geometry()
                if sg.contains(fg):
                    pm = scr.grabWindow(0, fg.x(), fg.y(), fg.width(), fg.height())
                    im = pm.toImage()
                    if not pm.isNull() and len({im.pixel(x, y) for x in range(0, im.width(), 97) for y in range(0, im.height(), 89)}) > 8:
                        img, how = pm, "screen"
                if img is None:
                    rep["errors"].append(f"{name}: Bildschirmaufnahme nicht möglich (Screen {sg.width()}x{sg.height()}, Fenster {fg.width()}x{fg.height()}), Widget-Aufnahme")
            if img is None:
                img = win.grab()
            img.save(path)
            rep["shots"][name] = {"file": os.path.basename(path), "size": [img.width(), img.height()], "capture": how, **layout_info()}
            save_report()

        def close_overlays():
            for ov in win.centralWidget().findChildren(Overlay):
                ov.hide(); ov.deleteLater()
            pump(0.2)

        def run_profile(key, until=None, stop_after=None):
            """Start via the profile editor (validate -> run, like the ▶ button). Returns the session."""
            pe = win.pages["profiles"]; win.show_page("profiles")
            pe._load_list(); _select(pe, f"anleitung-{key.lower()}"); pump(0.3)
            fin = []
            dlg = pe.start_flow()
            if dlg is not None:
                from .overlay import ElevationOffer
                if not isinstance(dlg, ElevationOffer):
                    raise RuntimeError(f"Start von Profil {key} verlangt einen Dialog ({type(dlg).__name__}) – nicht erwartet")
                # W_NEEDS_ADMIN for a file target only means a slower file creation: start without rights (no UAC)
                if "03b_ohne_rechte" not in rep["shots"]:
                    shot("03b_ohne_rechte")
                dlg.close_accept(False); pump(0.3)
            s = win.session
            s.finished.connect(lambda c, t: fin.append((c, t)))
            live = win.pages["live"]
            if until:
                wait(lambda: until(live) or fin, 240, f"Live-Zustand Profil {key}")
            if stop_after is not None:
                wait(lambda: (live.phase == "measure" and live.buf.n >= stop_after) or fin, 240, "Messphase für Stopp")
                win.stop_run()
            wait(lambda: fin, 300, f"Ende von Profil {key}")
            pump(0.5)
            res = None
            try:
                res = json.load(open(s.out_path, encoding="utf-8"))
            except (OSError, ValueError):
                pass
            rep["runs"][key + ("-stop" if stop_after is not None else "")] = {
                "run_id": s.run_id, "exit": fin[0][0], "text": fin[0][1], "result": s.out_path if res else None,
                "aborted": res.get("aborted") if res else None,
                "outcome": run_outcome(res, fin[0][0]) if res else EXIT_OUTCOME.get(fin[0][0]),   # exit code decides
                # contract 1.3: a test stopped in preparing/warmup has null summary values (partial/stopped_in)
                "tests": [{"name": t["config"].get("name"), "iops": sval(t, "summary", "iops"), "mbps": sval(t, "summary", "mbps"),
                           "lat_mean_us": sval(t, "summary", "lat_us", "mean"), "io_count": sval(t, "summary", "io_count"),
                           "partial": t.get("partial"), "stopped_in": t.get("stopped_in")}
                          for t in (res or {}).get("tests", []) if t["type"] == "storage"]}
            save_report()
            return s, fin[0][0], res

        # 2. first start: overview (screen capture incl. title bar with versions)
        win.show_page("dashboard"); shot("02_start", frame=True)
        # 3. storage test profile, validated by the real core
        pe = win.pages["profiles"]; win.show_page("profiles"); pe._load_list(); _select(pe, "anleitung-a"); pump(0.3)
        pe.validate(); shot("03_testprofil")
        # 4. live run captured mid-measurement (first test, >= 7 s measured), then the run completes
        ticks_per_s = 4   # GUI tick 250 ms
        _, code_a, res_a = run_profile("A", until=lambda lv: lv.phase == "measure" and lv.test_idx == 0 and lv.buf.n >= 7 * ticks_per_s
                                       and (lv.rw.setChecked(lv.has_rw) or lv.refresh() or shot("04_live") or True))
        # second complete run for the comparison
        _, code_b, res_b = run_profile("B")
        # 4b. stop: third run, stopped after ~4 s of measurement
        _, code_s, _ = run_profile("A", stop_after=4 * ticks_per_s)
        win.show_page("live"); shot("04b_gestoppt")
        if code_a != 0 or code_b != 0:
            rep["errors"].append(f"Läufe A/B: Exit {code_a}/{code_b}")
        if code_s != 3:
            rep["errors"].append(f"gestoppter Lauf: Exit {code_s} (erwartet 3)")
        # 5. comparison of the two complete runs
        win.reload_results(); cmp_ = win.pages["compare"]
        want = {rep["runs"]["A"]["result"], rep["runs"]["B"]["result"]}
        from PySide6.QtCore import Qt
        for i in range(cmp_.list.count()):
            it = cmp_.list.item(i); f = cmp_.results[it.data(Qt.UserRole)][0]
            it.setCheckState(Qt.Checked if f in want else Qt.Unchecked)
        win.show_page("compare"); shot("05_vergleich")
        rep["compare_selected"] = cmp_.selected_files()
        # 6. export page + support bundle (--all, anonymized)
        exp = win.pages["export"]; exp.anon.setChecked(True)
        win.show_page("export"); shot("06_export")
        win.support_bundle([], anonymize=True); shot("06b_support_dialog"); close_overlays()
        desk = os.path.join(os.path.expanduser("~"), "Desktop"); os.makedirs(desk, exist_ok=True)
        zp = os.path.join(desk, "sbench-support.zip")
        if os.path.exists(zp):
            os.remove(zp)
        done = []
        p = win._bundle_save([], {"selected": False, "logs": True, "profiles": True, "anonymize": True}, out_path=zp)
        p.finished.connect(lambda c, _s: done.append(c))
        wait(lambda: done, 120, "sbench bundle")
        pump(0.5); shot("06c_support_fertig")
        n, anon = sbench.bundle_summary(zp)
        rep["bundle"] = {"exit": done[0], "file": zp, "files": n, "anonymized": anon}
        if done[0] != 0 or not anon:
            rep["errors"].append(f"bundle exit {done[0]}, anonymisiert={anon}")
        clipped = [k for k, v in rep["shots"].items() if v["h_scroll"]]
        if clipped:
            rep["errors"].append(f"horizontal abgeschnitten (Scrollbar): {clipped}")
        rep["ok"] = not [e for e in rep["errors"] if "Widget-Aufnahme" not in e]
    except Exception as e:  # noqa: BLE001 -- always write the report
        import traceback
        rep["errors"].append(f"{type(e).__name__}: {e}"); rep["traceback"] = traceback.format_exc()[-3000:]
    save_report()
    if sys.stdout:
        print(json.dumps(rep, indent=1, ensure_ascii=False))
    app.quit()
    return 0 if rep["ok"] else 1
