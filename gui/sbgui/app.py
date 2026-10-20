"""Entry point.

  sbench-gui                               normal mode (sbench.exe next to the EXE, else mock)
  sbench-gui --demo                        live run of the mock via QProcess
  sbench-gui --screenshot <dir> [--scale 1.5] [--size 1440x900|1366x768] [--replay progress.jsonl] [--results <dir>] [--info info.json]
  sbench-gui --self-test [--platforms windows,offscreen]   -> JSON report on stdout, exit 0/1
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
    return ap.parse_args(argv)


def result_files(d, extra_dirs=()):
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
    return MainWindow(info, kv, result_files(a.results, extra_results))


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
    me = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "sbgui"]
    ok = True
    for plat in [p for p in a.platforms.split(",") if p]:
        t0 = time.time()
        rf = os.path.join(tempfile.gettempdir(), f"sbench-gui-selftest-{os.getpid()}-{plat}.json")
        try:
            cp = subprocess.run(me + ["--self-test-child", plat, "--report", rf], capture_output=True, text=True, timeout=60, cwd=ROOT)
            try:
                res = json.load(open(rf, encoding="utf-8")); os.remove(rf)
            except (OSError, ValueError):
                res = {"ok": False, "exit": cp.returncode, "stderr": (cp.stderr or "")[-1500:]}
        except subprocess.TimeoutExpired:
            res = {"ok": False, "error": "timeout"}
        res["seconds"] = round(time.time() - t0, 2)
        report["platforms"][plat] = res; ok &= bool(res.get("ok"))
    report["ok"] = ok
    _emit(report, a.report)
    return 0 if ok else 1


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
        if os.name == "nt":
            res["loaded_dlls"] = _loaded_modules()
        res["ok"] = img.width() > 0 and not img.isNull()
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
    if a.demo:
        return demo(a)
    from . import sbench
    logf = sbench.setup_logging()
    app = make_app()
    import logging
    logging.getLogger("sbench-gui").info("start, log %s, sbench %s", logf, sbench.sbench_command())
    win = build_window(a, deterministic=False, extra_results=[os.path.join(sbench.gui_dir(), "results")])
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
