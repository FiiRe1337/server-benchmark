"""Entry point.

  sbench-gui                               normal mode (frozen: embedded real core, see core.py; source: $SBENCH_EXE or mock)
  sbench-gui --demo                        live run of the mock via QProcess
  sbench-gui --screenshot <dir> [--scale 1.5] [--size 1440x900|1366x768] [--replay progress.jsonl] [--results <dir>] [--info info.json]
  sbench-gui --self-test [--platforms windows,offscreen] [--report f.json] [--no-core]
                                           -> JSON report (stdout and/or --report), exit 0/1. Without --no-core it also
                                              checks the core: version --json + SHA-256, info, 3 s storage job, bundle --all
"""
import argparse, glob, json, os, platform, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(ROOT, "demo")


def parse(argv):
    ap = argparse.ArgumentParser(prog="sbench-gui")
    ap.add_argument("--screenshot", metavar="DIR")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--size", default="1440x900")
    ap.add_argument("--replay", default=os.path.join(DEMO, "progress.jsonl"), help="progress.jsonl (sb-progress/1) for the live view")
    ap.add_argument("--replay-job", default=os.path.join(DEMO, "job-oltp.json"), help="sb-job/1 that produced --replay (labels only)")
    ap.add_argument("--results", default=DEMO, help="dir with sb-result/1 files")
    ap.add_argument("--info", default=os.path.join(DEMO, "info.json"), help="sb-info/1 used in screenshot mode")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--self-test-child", metavar="PLATFORM")
    ap.add_argument("--platforms", default="windows,offscreen" if os.name == "nt" else "offscreen")
    ap.add_argument("--report", metavar="FILE", help="also write the JSON report to FILE (needed for windowed EXE: no stdout)")
    ap.add_argument("--guide", metavar="DIR", help="README guide screenshots with the REAL core (see sbgui/guide.py)")
    ap.add_argument("--guide-target", metavar="FILE", help="test file for --guide (default: %%TEMP%%\\sbench-anleitung.dat)")
    ap.add_argument("--no-core", action="store_true", help="self-test: skip the core checks (GUI only)")
    ap.add_argument("--extra-results", metavar="DIR", action="append", default=[], help=argparse.SUPPRESS)
    ap.add_argument("--expect-result", metavar="FILE", help=argparse.SUPPRESS)
    return ap.parse_args(argv)


def result_files(d, extra_dirs=()):
    """d = demo/--results dir (+ schema example); None = only real results from extra_dirs (normal GUI start)."""
    if d is None:
        return [f for e in extra_dirs for f in sorted(glob.glob(os.path.join(e, "*.json")), reverse=True)]
    files = sorted(glob.glob(os.path.join(d, "result*.json")))
    for e in extra_dirs:
        files += sorted(glob.glob(os.path.join(e, "*.json")), reverse=True)
    try:
        sys.path.insert(0, ROOT); import sb_contract
        ex = os.path.join(sb_contract.base_dir(), "examples", "result-storage.json")
    except (ImportError, FileNotFoundError):
        ex = ""
    quad = [f for f in files if "quad" in f]
    order = [f for h in ("host", "vm.json", "vm-san") for f in quad if f.endswith(f"-{h}" if h != "vm.json" else "-vm.json") or f.endswith(f"-{h}.json")]
    rest = [f for f in files if f not in order]
    return order + rest + ([ex] if os.path.exists(ex) else [])


def make_app(argv_extra=(), scale=1.0):
    if scale != 1.0:
        os.environ["QT_SCALE_FACTOR"] = f"{scale:g}"
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication
    # Qt 6 is per-monitor-DPI aware by default; keep fractional factors (125 %, 150 %) exact.
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication([sys.argv[0], *argv_extra])
    from . import theme
    theme.apply(app)
    return app


def build_window(a, deterministic, extra_results=()):
    from . import sbench
    from .main_window import MainWindow
    from .model import check_schema
    if deterministic:
        info = json.load(open(a.info, encoding="utf-8")); kv = "0.1.0-mock"
    else:
        info = sbench.info(); kv = sbench.kern_version()
    check_schema(info, "sb-info/1")
    # real start: never mix the mock demo results / schema example into the user's results (only --demo or explicit --results)
    d = a.results if (deterministic or a.demo or os.path.abspath(a.results) != os.path.abspath(DEMO)) else None
    return MainWindow(info, kv, result_files(d, list(extra_results) + list(getattr(a, "extra_results", []) or [])))


def screenshots(a):
    import tempfile
    os.environ["SBENCH_GUI_DIR"] = tempfile.mkdtemp(prefix="sbench-gui-shot-")   # no user profiles/logs in screenshots
    _app = make_app(scale=a.scale)   # keep the QApplication alive
    from PySide6.QtCore import QCoreApplication
    from .sbench import Replay
    from .overlay import Overlay
    w, h = (int(x) for x in a.size.split("x"))
    os.makedirs(a.screenshot, exist_ok=True)
    win = build_window(a, deterministic=True)
    win.setFixedSize(w, h); win.show()
    # deterministic paths in screenshots (CI runner paths would leak otherwise)
    win.pages["export"].out.setText("%LOCALAPPDATA%\\sbench-gui\\export")
    pump = lambda n=4: [QCoreApplication.processEvents() for _ in range(n)]
    saved, layout = [], {}

    def shot(name):
        pump(); path = os.path.join(a.screenshot, f"{name}.png")
        win.grab().save(path); saved.append(path)
        sa = win.stack.currentWidget(); pg_ = sa.widget()
        layout[name] = {"viewport": [sa.viewport().width(), sa.viewport().height()],
                        "content_min": [pg_.minimumSizeHint().width(), pg_.minimumSizeHint().height()],
                        "h_scroll": sa.horizontalScrollBar().maximum() > 0, "v_scroll": sa.verticalScrollBar().maximum() > 0,
                        "sidebar": win.sidebar.width()}

    def close_overlays():
        for ov in win.centralWidget().findChildren(Overlay):
            ov.hide(); ov.deleteLater()
        pump()

    win.show_page("dashboard"); shot("01_dashboard")
    pe = win.pages["profiles"]
    win.show_page("profiles"); pe.validate(); shot("02_profile_editor")       # Devend's mix example: names, W_DESTRUCTIVE, W_NEEDS_ADMIN
    pe.specs.item(0, 1).setText("50"); pe.specs.item(1, 2).setText("1000")   # reproduce validate-error example
    pe.validate(); shot("02b_profile_validation")
    _select(pe, "oltp-mixed")                                                  # raw disk without allow_destructive
    dlg = pe.start_flow(); dlg.edits[0][0].setText(dlg.edits[0][1]); shot("02c_destructive_confirm")
    dlg.ok.click(); pump(); shot("02d_elevation_offer"); close_overlays()
    pe.import_icf(os.path.join(DEMO, "iometer-example.icf")); shot("02e_icf_import"); close_overlays()
    _select(pe, "mix-60-40")

    live = win.pages["live"]
    job = json.load(open(a.replay_job, encoding="utf-8"))
    rep = Replay(a.replay); live.attach(rep, job, tick_ms=250)
    rep.feed_until(lambda e: e.get("ev") == "progress" and e["phase"] == "preparing" and e["done"] >= e["total"] * 0.4)
    live.refresh(); live.timer.stop()
    win.run_mini.setText("● läuft · 20261020-091500-demo"); win.run_mini.setObjectName("chipAccent"); win.run_mini.style().polish(win.run_mini)
    win.show_page("live"); shot("03b_live_preparing")
    rep2 = Replay(a.replay); live.attach(rep2, job, tick_ms=250)
    rep2.feed_fraction(0.72)          # deterministic: no timers, no sleeps
    live.rw.setChecked(True); live.refresh(); live.timer.stop()
    shot("03_live_run")
    win.show_page("dashboard")
    win.offer_reattach([{"run_id": "20261020-091500-demo", "pid": 7412, "started_utc": "2026-10-20T07:15:00.120Z",
                         "out": "C:\\Users\\michi\\AppData\\Local\\sbench-gui\\results\\20261020-091500-demo.json",
                         "progress_file": "C:\\Tools\\sbench\\data\\runs\\20261020-091500-demo.jsonl"}])
    shot("03d_reattach_offer"); close_overlays()
    win.show_page("compare"); shot("04_compare")
    win.show_page("export"); shot("05_export")
    win.support_bundle([], anonymize=True); shot("06_support_dialog"); close_overlays()
    win.toast.show_msg("Support-Paket erstellt", "sbench-support.zip · 17 Dateien inkl. manifest.json (SHA-256) · anonymisiert\n"
                       "C:\\Users\\michi\\Desktop", "."); shot("07_support_export_done")
    _emit({"ok": True, "scale": a.scale, "size": a.size, "min_size": [win.minimumWidth(), win.minimumHeight()],
           "files": saved, "layout": layout}, a.report)
    return 0


def _select(pe, stem):
    from PySide6.QtCore import Qt
    for i in range(pe.list.count()):
        if stem in (pe.list.item(i).data(Qt.UserRole) or ""):
            pe.list.setCurrentRow(i); return


def demo(a):
    app = make_app()
    win = build_window(a, deterministic=False)
    _fit(win); win.show()
    from PySide6.QtCore import QTimer
    QTimer.singleShot(300, lambda: win.start_run(os.path.join(ROOT, "profiles", "four-corners.json")))
    return app.exec()


# ------------------------------------------------------------------ self-test
def self_test(a):
    """Spawn one child per Qt platform plugin (a missing plugin aborts the process, so isolate it)."""
    report = {"schema": "sbench-gui-selftest/1", "python": sys.version.split()[0], "os": platform.platform(),
              "frozen": bool(getattr(sys, "frozen", False)), "platforms": {}}
    try:
        import PySide6
        report["pyside6"] = PySide6.__version__
    except ImportError as e:
        report["pyside6"] = f"missing: {e}"
    import tempfile
    from . import core
    report["gui_version"] = core.gui_version(); report["build"] = core.build_info()
    me = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "sbgui"]
    ok = True
    child_extra = []
    work = tempfile.mkdtemp(prefix="sbench-gui-selftest-")
    if not a.no_core:
        report["core"] = core_self_test(work)
        ok &= bool(report["core"]["ok"])
        if report["core"].get("result_file"):
            child_extra = ["--extra-results", os.path.dirname(report["core"]["result_file"]), "--expect-result", report["core"]["result_file"]]
    for plat in [p for p in a.platforms.split(",") if p]:
        t0 = time.time()
        rf = os.path.join(tempfile.gettempdir(), f"sbench-gui-selftest-{os.getpid()}-{plat}.json")
        try:
            cp = subprocess.run(me + ["--self-test-child", plat, "--report", rf] + child_extra, capture_output=True, text=True, timeout=60, cwd=ROOT,
                                creationflags=_NO_WINDOW)
            try:
                res = json.load(open(rf, encoding="utf-8")); os.remove(rf)
            except (OSError, ValueError):
                res = {"ok": False, "exit": cp.returncode, "stderr": (cp.stderr or "")[-1500:]}
        except subprocess.TimeoutExpired:
            res = {"ok": False, "error": "timeout"}
        res["seconds"] = round(time.time() - t0, 2)
        report["platforms"][plat] = res; ok &= bool(res.get("ok"))
    report["ok"] = ok
    report["summary"] = _summary(report)
    _emit(report, a.report)
    if not ok and sys.stderr:
        print("\n".join(report["summary"]), file=sys.stderr)
    return 0 if ok else 1


_NO_WINDOW = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW: windowed EXE must not flash consoles


def _summary(report):
    """Readable one-line-per-check summary (also on stderr when the self-test fails)."""
    lines = [f"sbench-gui {report.get('gui_version')} Self-Test: {'OK' if report.get('ok') else 'FEHLGESCHLAGEN'}"]
    c = report.get("core")
    if c:
        lines.append(f"Kern ({c.get('mode')}): {c.get('exe')}  data_dir={c.get('data_dir')}")
        for name, chk in c.get("checks", {}).items():
            lines.append(f"  {'OK    ' if chk.get('ok') else 'FEHLER'} {name}: {chk.get('detail', '')}")
    for plat, r in report.get("platforms", {}).items():
        lines.append(f"  {'OK    ' if r.get('ok') else 'FEHLER'} GUI {plat}: {r.get('error') or r.get('expect_result') or r.get('grab')}")
    return lines


SELFTEST_JOB = {"schema": "sb-job/1", "name": "selftest", "tests": [{
    "type": "storage", "name": "selftest-4k-rr",
    "targets": [{"path": None, "kind": "file", "file_size": "64MiB"}],
    "workers": 1, "queue_depth": 4, "warmup_s": 1, "duration_s": 3, "cache": "unbuffered",
    "access_specs": [{"weight": 100, "block_size": "4KiB", "read_pct": 100, "random_pct": 100, "align": "4KiB"}]}]}


def selftest_job(temp_dir):
    """The fixed self-test job with the target in the real %TEMP% (sbench does not expand variables)."""
    import copy
    job = copy.deepcopy(SELFTEST_JOB)
    job["tests"][0]["targets"][0]["path"] = os.path.join(temp_dir, "sbench-selftest.dat")
    return job


def _same_path(a, b):
    return bool(a and b) and os.path.normcase(os.path.normpath(os.path.abspath(a))) == os.path.normcase(os.path.normpath(os.path.abspath(b)))


def core_self_test(work):
    """Real core (frozen) or mock (source): no admin rights needed. Every call goes through sbench.command()
    (SHA-256 check + --data-dir). Returns {ok, mode, exe, data_dir, checks{...}, result_file}."""
    import tempfile, zipfile
    from . import core, sbench
    from .model import check_schema, storage_tests
    rep = {"ok": False, "checks": {}}
    checks = rep["checks"]

    def check(name, ok, detail="", **extra):
        checks[name] = {"ok": bool(ok), "detail": detail, **extra}; return bool(ok)

    def call(args, timeout=60):
        cmd = sbench.command(args)
        cp = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=_NO_WINDOW)
        return cp.returncode, cp.stdout.decode("utf-8", "replace"), cp.stderr.decode("utf-8", "replace")

    try:
        c = core.get()
    except core.CoreError as e:
        check("core_install", False, str(e)); return rep
    rep.update(mode=c.mode, exe=c.exe, data_dir=c.data_dir, expected_sha256=c.expected_sha)
    check("core_install", True, f"{c.mode}: {c.exe}")
    mei = getattr(sys, "_MEIPASS", None)
    check("not_in_meipass", not (mei and core.is_within(c.exe, mei)), f"{c.exe} (_MEIPASS={mei})")
    try:
        import sb_contract
        val = sb_contract.validators()
    except (ImportError, FileNotFoundError) as e:
        val = None; check("schemas", False, f"{type(e).__name__}: {e}")
    if val is None:
        check("schemas", False, "jsonschema/Schemas nicht verfügbar"); return rep

    def errs(doc, schema):
        return [f"{'/'.join(map(str, e.absolute_path))}: {e.message}"[:200] for e in val[schema].iter_errors(doc)][:5]

    try:
        # 1. version --json, SHA-256 against the value embedded at build time
        code, out, err = call(["version", "--json"])
        v = json.loads(out) if code == 0 else {}
        rep["core_version"] = v.get("version")
        if c.expected_sha:
            check("version_sha256", v.get("sha256") == c.expected_sha,
                  f"version={v.get('version')} sha256={v.get('sha256')} erwartet={c.expected_sha}", exit=code)
        else:
            check("version_sha256", code == 0 and len(v.get("sha256") or "") == 64,
                  f"version={v.get('version')} sha256={v.get('sha256')} (Quell-Lauf: kein eingebetteter Hash)", exit=code)
        bi = core.build_info()
        if bi.get("core_version") and v.get("version"):
            check("version_matches_build", v["version"] == bi["core_version"], f"{v['version']} vs. Build {bi['core_version']}")

        # 2. info --json against sb-info/1, meta.data_dir == our --data-dir
        code, out, err = call(["info", "--json"])
        info = json.loads(out) if code == 0 else {}
        e = errs(info, "sb-info/1") if info else ["keine Ausgabe"]
        check("info_schema", code == 0 and not e, "; ".join(e) or f"host={info.get('meta', {}).get('host')}", exit=code)
        dd = info.get("meta", {}).get("data_dir")
        check("info_data_dir", _same_path(dd, c.data_dir), f"meta.data_dir={dd}")

        # 3. 3 s storage job (4 KiB random read, file in %TEMP%), result only after `done`
        tmp = tempfile.gettempdir()
        job_path = os.path.join(work, "selftest-job.json")
        json.dump(selftest_job(tmp), open(job_path, "w", encoding="utf-8"), indent=1)
        rdir = os.path.join(work, "results"); os.makedirs(rdir, exist_ok=True)
        rid = "selftest-" + sbench.new_run_id()
        out_path = os.path.join(rdir, f"result-{rid}.json")
        code, out, err = call(sbench.run_args(job_path, out_path, rid, 250, False, "stdout"), timeout=180)
        evs = []
        for ln in out.splitlines():
            try:
                evs.append(json.loads(ln))
            except ValueError:
                pass
        done = [x for x in evs if x.get("ev") == "done"]
        errors = [x for x in evs if x.get("ev") == "error"]
        check("run_exit_0", code == 0 and done, f"exit={code} events={len(evs)} done={bool(done)}"
              + (f" errors={[x.get('code') for x in errors]}" if errors else "") + (f" stderr={err.strip()[-300:]}" if code else ""))
        res = json.load(open(out_path, encoding="utf-8")) if done and os.path.isfile(out_path) else None
        if res is not None:
            e = errs(res, "sb-result/1")
            check("result_schema", not e, "; ".join(e) or f"run_id={res.get('run_id')}")
            check("result_run_id", res.get("run_id") == rid, f"run_id={res.get('run_id')}")
            st = storage_tests(check_schema(res, "sb-result/1"))
            n = st[0]["summary"].get("io_count", 0) if st else 0
            check("io_count", n > 0, f"io_count={n} iops={st[0]['summary'].get('iops') if st else None}")
            rep["result_file"] = out_path
            rep["result_summary"] = {"iops": st[0]["summary"].get("iops") if st else None, "io_count": n,
                                     "lat_mean_us": (st[0]["summary"].get("lat_us") or {}).get("mean") if st else None}
        else:
            check("result_schema", False, f"kein Ergebnis {out_path}")

        # 4. bundle --all (contract: without --all/--results only info.json + includes)
        zp = os.path.join(work, "selftest-bundle.zip")
        args = sbench.bundle_args(zp)
        code, out, err = call(args, timeout=120)
        man = None
        try:
            man = json.loads(zipfile.ZipFile(zp).read("manifest.json"))
        except (OSError, KeyError, ValueError, zipfile.BadZipFile) as ex:
            err += f" {ex}"
        e = errs(man, "sb-bundle/1") if man else ["kein manifest.json"]
        has_run = bool(man) and any(rid in (f.get("path") or "") for f in man.get("files", []))
        check("bundle_all", "--all" in args and code == 0 and not e and has_run,
              f"exit={code} files={len(man.get('files', [])) if man else 0} enthält Lauf={has_run} " + ("; ".join(e) or err.strip()[-200:]))
    except core.CoreIntegrityError as ex:
        check("integrity", False, str(ex))
    except Exception as ex:  # noqa: BLE001 -- report everything, never crash the self-test
        check("exception", False, f"{type(ex).__name__}: {ex}")
    finally:
        try:
            os.remove(os.path.join(tempfile.gettempdir(), "sbench-selftest.dat"))
        except OSError:
            pass
    rep["ok"] = all(x["ok"] for x in checks.values())
    return rep


def _emit(obj, path=None):
    txt = json.dumps(obj, indent=1)
    if path:
        with open(path, "w", encoding="utf-8") as f:
            f.write(txt)
    if sys.stdout:
        print(txt)


def self_test_child(plat, a):
    res = {"ok": False, "platform": plat}
    try:
        _app = make_app(["-platform", plat])   # keep the QApplication alive
        from PySide6.QtCore import qVersion, QCoreApplication
        from PySide6.QtGui import QFontDatabase, QGuiApplication
        res["qt"] = qVersion(); res["platform_name"] = QGuiApplication.platformName()
        win = build_window(a, deterministic=True)
        win.resize(1280, 800); win.show()
        for _ in range(5):
            QCoreApplication.processEvents()
        img = win.grab().toImage()
        res["grab"] = [img.width(), img.height()]
        res["dpr"] = win.devicePixelRatioF()
        res["screens"] = [s.name() for s in QGuiApplication.screens()]
        res["fonts_ok"] = "Inter" in QFontDatabase.families()
        res["pages"] = list(win.pages)
        if a.expect_result:   # result of the core self-test run must be parsed by the GUI (compare/export pages)
            files = [f for f, _ in win.pages["compare"].results]
            res["expect_result"] = "geladen" if any(_same_path(f, a.expect_result) for f in files) else f"fehlt in {len(files)} Ergebnissen"
        if os.name == "nt":
            res["loaded_dlls"] = _loaded_modules()
        res["ok"] = img.width() > 0 and not img.isNull() and res.get("expect_result", "geladen") == "geladen"
    except Exception as e:  # noqa: BLE001
        res["error"] = f"{type(e).__name__}: {e}"
    _emit(res, a.report)
    return 0 if res["ok"] else 1


def _loaded_modules():
    """Windows: names of all DLLs loaded in this process (dependency collection, e.g. opengl32/dwmapi/d3d*/uxtheme)."""
    import ctypes
    from ctypes import wintypes
    psapi = ctypes.WinDLL("psapi"); k32 = ctypes.WinDLL("kernel32")
    hproc = k32.GetCurrentProcess(); mods = (wintypes.HMODULE * 1024)(); need = wintypes.DWORD()
    psapi.EnumProcessModules(hproc, mods, ctypes.sizeof(mods), ctypes.byref(need))
    out = []
    for m in mods[: need.value // ctypes.sizeof(wintypes.HMODULE)]:
        buf = ctypes.create_unicode_buffer(260); k32.GetModuleFileNameW(wintypes.HMODULE(m), buf, 260)
        out.append(os.path.basename(buf.value).lower())
    return sorted(set(out))


def main(argv=None):
    a = parse(sys.argv[1:] if argv is None else argv)
    if a.self_test_child:
        return self_test_child(a.self_test_child, a)
    if a.self_test:
        return self_test(a)
    if a.screenshot:
        return screenshots(a)
    if a.guide:
        from . import guide
        return guide.run(a)
    if a.demo:
        return demo(a)
    from . import sbench, core
    logf = sbench.setup_logging()
    app = make_app()
    import logging
    try:
        logging.getLogger("sbench-gui").info("start GUI %s, log %s, sbench %s", core.gui_version(), logf, sbench.sbench_command())
        win = build_window(a, deterministic=False, extra_results=[os.path.join(sbench.gui_dir(), "results")])
    except core.CoreError as e:
        logging.getLogger("sbench-gui").error("core: %s", e)
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(None, "sbench – Kern nicht startbar", f"{e}\n\nProtokoll: {logf}")
        return 1
    _fit(win); win.show()
    from PySide6.QtCore import QTimer
    QTimer.singleShot(400, win.check_running)        # re-attach to runs that outlived a previous GUI
    return app.exec()


def _fit(win):
    """Open at 1440x900, but never larger than the available screen (RDP 1366x768 -> maximized)."""
    from PySide6.QtGui import QGuiApplication
    av = QGuiApplication.primaryScreen().availableGeometry()
    if av.width() < 1440 or av.height() < 900:
        win.showMaximized()
    else:
        win.resize(1440, 900)
