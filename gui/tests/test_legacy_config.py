"""v0.2.0-beta.1 opened only briefly on machines that had run a beta before: user files from older versions/interrupted
runs (%LOCALAPPDATA%\\sbench-gui\\profiles|results) and the start path itself were never tested. These tests load the
anonymised beta.2 state (tests/fixtures/legacy-beta2) and unusable files (tests/fixtures/legacy-broken), check that no
stray top-level windows appear while the main window is built and that crash.log catches failures before Qt exists."""
import glob, json, os, shutil, subprocess, sys

import pytest

from conftest import GUI

FX = os.path.join(GUI, "tests", "fixtures")
BETA2 = os.path.join(FX, "legacy-beta2")
BROKEN = os.path.join(FX, "legacy-broken")


def _seed(tmp_path, *sets):
    """%LOCALAPPDATA% (tmp/la) with the given fixture sets; returns (gui_dir, data_dir)."""
    la = tmp_path / "la"; la.mkdir(exist_ok=True)
    data = tmp_path / "data"
    for s in sets:
        shutil.copytree(os.path.join(FX, s, "LocalAppData"), la, dirs_exist_ok=True)
        if os.path.isdir(os.path.join(FX, s, "data")):
            shutil.copytree(os.path.join(FX, s, "data"), data, dirs_exist_ok=True)
    return la / "sbench-gui", data


@pytest.fixture
def legacy_env(tmp_path, monkeypatch):
    def make(*sets):
        gd, data = _seed(tmp_path, *sets)
        monkeypatch.delenv("SBENCH_EXE", raising=False)
        monkeypatch.setenv("SBENCH_GUI_DIR", str(gd))
        monkeypatch.setenv("SBENCH_DATA_DIR", str(data))
        monkeypatch.setenv("SBENCH_MOCK_SPEED", "50")
        from sbgui import legacy
        legacy.take_quarantined(); legacy._state["skip"].clear()
        return gd
    return make


def _win(gd):
    from sbgui.app import parse, build_window
    from sbgui import legacy
    legacy.sanitize_user_dir(str(gd))
    return build_window(parse([]), deterministic=False, extra_results=[str(gd / "results")])


def test_fixtures_are_anonymised():
    import re
    bad = re.compile(r"runnervm|RUNNER~1|\b10\.\d+\.\d+\.\d+\b|\b172\.(1[6-9]|2\d|3[01])\.|\b192\.168\.|IAD\d{6}", re.I)
    for f in glob.glob(os.path.join(FX, "**", "*"), recursive=True):
        if os.path.isfile(f):
            txt = open(f, encoding="utf-8-sig", errors="replace").read()
            assert not bad.search(txt), f
            for ip in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", txt):
                assert ip.startswith("192.0.2."), (f, ip)


def test_legacy_beta2_files_are_usable():
    from sbgui import legacy
    for f in glob.glob(os.path.join(BETA2, "LocalAppData", "sbench-gui", "results", "*.json")):
        assert legacy.check_file(f, "result")[1] is None, f
    for f in glob.glob(os.path.join(BETA2, "LocalAppData", "sbench-gui", "profiles", "*.json")):
        assert legacy.check_file(f, "profile")[1] is None, f
    for f in glob.glob(os.path.join(GUI, "profiles", "*.json")):            # bundled profiles
        assert legacy.check_file(f, "profile")[1] is None, f
    for f in glob.glob(os.path.join(GUI, "demo", "result*.json")):          # demo results
        assert legacy.check_file(f, "result")[1] is None, f


def test_bom_is_accepted(tmp_path):
    from sbgui import legacy
    src = glob.glob(os.path.join(BETA2, "LocalAppData", "sbench-gui", "results", "*.json"))[0]
    f = tmp_path / "bom.json"
    f.write_bytes(b"\xef\xbb\xbf" + open(src, "rb").read().lstrip(b"\xef\xbb\xbf"))
    doc, why = legacy.check_file(str(f), "result")
    assert why is None and doc["schema"] == "sb-result/1"


@pytest.mark.parametrize("name, reason", [
    ("20261022-170000-leer.json", "leer"), ("20261022-171500-abgeschnitten.json", "kein gültiges JSON"),
    ("20261022-172000-liste.json", "JSON-list"), ("20261022-172500-unvollstaendig.json", "meta.hypervisor.role"),
    ("20261022-173000-falsches-schema.json", "sb-info/1")])
def test_broken_results_detected(name, reason):
    from sbgui import legacy
    doc, why = legacy.check_file(os.path.join(BROKEN, "LocalAppData", "sbench-gui", "results", name), "result")
    assert doc is None and reason in why


def test_quarantine_renames_only_user_files(tmp_path):
    from sbgui import legacy
    legacy.take_quarantined(); legacy._state["skip"].clear()
    user = tmp_path / "results"; user.mkdir()
    bad = user / "x.json"; bad.write_text("", encoding="utf-8")
    other = tmp_path / "demo.json"; other.write_text("{", encoding="utf-8")
    ok = glob.glob(os.path.join(BETA2, "LocalAppData", "sbench-gui", "results", "*.json"))[0]
    res = legacy.load_results([str(bad), str(other), ok], user_dir=str(user))
    assert [f for f, _ in res] == [ok]
    assert not bad.exists() and len(list(user.glob("x.json.defekt-*"))) == 1
    assert other.exists()                                     # outside the user dir: only skipped
    q = legacy.take_quarantined()
    assert [os.path.basename(p) for p, _, _ in q] == ["x.json", "demo.json"] and q[1][1] is None
    assert "x.json" in legacy.warning_text(q) and "gesichert als" in legacy.warning_text(q)


def test_main_window_with_beta2_state(qapp, legacy_env):
    from sbgui import legacy
    gd = legacy_env("legacy-beta2")
    win = _win(gd)
    assert legacy.take_quarantined() == []
    assert len(win.pages["results"].results) == 4 and len(win.pages["compare"].results) == 4 and len(win.pages["export"].results) == 4
    pe = win.pages["profiles"]
    # the user's own copy of "Mix 60/40" (saved by beta.2) replaces the bundled one at start
    assert pe.job and pe.list.currentItem().data(0x0100).startswith(str(gd))
    win.close(); win.deleteLater()


def test_main_window_with_broken_files(qapp, legacy_env):
    """Before the fix: JSONDecodeError in Compare.__init__ (pages/compare.py:82) -> no main window."""
    from sbgui import legacy
    gd = legacy_env("legacy-beta2", "legacy-broken")
    win = _win(gd)
    q = legacy.take_quarantined()
    names = sorted(os.path.basename(p) for p, _, _ in q)
    assert len(names) == 8, names
    assert "mix-60-40-alt.json" in names           # passes the structure check, fails in the editor -> backed up on load
    assert all(b and os.path.exists(b) for _, b, _ in q)
    assert sorted(os.listdir(gd / "results")) == sorted(os.listdir(os.path.join(BETA2, "LocalAppData", "sbench-gui", "results"))) + \
        sorted(f for f in os.listdir(gd / "results") if ".defekt-" in f)
    assert len(win.pages["compare"].results) == 4
    pe = win.pages["profiles"]
    assert pe.job and "Mix 60/40" in pe.job["name"]   # fell back to the next usable mix-60-40 profile
    win.close(); win.deleteLater()


def test_reload_results_skips_new_broken_file(qapp, legacy_env):
    from sbgui import legacy
    gd = legacy_env("legacy-beta2")
    win = _win(gd)
    (gd / "results" / "20261023-080000-abgebrochen.json").write_text('{"schema": "sb-result/1", "meta": {', encoding="utf-8")
    win.reload_results()
    assert len(win.pages["compare"].results) == 4
    assert [os.path.basename(p) for p, _, _ in legacy.take_quarantined()] == ["20261023-080000-abgebrochen.json"]
    win.close(); win.deleteLater()


def test_no_stray_toplevel_windows(qapp, legacy_env):
    """v0.2.0-beta.1: Card() called setVisible(True) on a still parentless subtitle label -> 27 own windows flashed up."""
    from PySide6.QtCore import QObject, QEvent
    from PySide6.QtWidgets import QWidget
    gd = legacy_env("legacy-beta2")
    seen = []

    class F(QObject):
        def eventFilter(self, o, ev):
            if ev.type() == QEvent.Show and isinstance(o, QWidget) and o.isWindow():
                seen.append(f"{type(o).__name__} {getattr(o, 'text', lambda: '')()!r}")
            return False
    f = F(); qapp.installEventFilter(f)
    try:
        win = _win(gd)
    finally:
        qapp.removeEventFilter(f)
    assert seen == []
    from sbgui.widgets import Card
    c = Card("Titel", "Untertitel"); c.show(); qapp.processEvents()
    assert c.subtitle_label.isVisible()
    c2 = Card("Titel"); c2.show(); qapp.processEvents()
    assert not c2.subtitle_label.isVisible()
    for w in (c, c2, win):
        w.close(); w.deleteLater()


def _run(args, tmp_path, *sets, timeout=120):
    gd, data = _seed(tmp_path, *sets)
    crash = tmp_path / "crash"
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "LOCALAPPDATA": str(tmp_path / "la"), "SBENCH_DATA_DIR": str(data),
           "SBENCH_CRASH_DIR": str(crash), "SBENCH_MOCK_SPEED": "50"}
    env.pop("SBENCH_GUI_DIR", None); env.pop("SBENCH_EXE", None)
    cp = subprocess.run([sys.executable, "-m", "sbgui", *args], cwd=GUI, env=env, capture_output=True, text=True, timeout=timeout)
    return cp, gd, crash / "crash.log"


@pytest.mark.parametrize("sets, n_res, n_q", [((), 0, 0), (("legacy-beta2",), 4, 0), (("legacy-beta2", "legacy-broken"), 4, 8)])
def test_smoke_start_cli(tmp_path, sets, n_res, n_q):
    """Same call as the CI start test of the EXE (there via --self-test): real start path incl. app.exec()."""
    rf = tmp_path / "start.json"
    cp, gd, crash = _run(["--smoke-start", "1.5", "--platforms", "offscreen", "--report", str(rf)], tmp_path, *sets)
    rep = json.loads(rf.read_text(encoding="utf-8"))
    assert cp.returncode == 0 and rep["ok"], (rep.get("summary"), cp.stderr[-2000:])
    assert rep["visible"] and rep["stray_windows"] == [] and rep["exceptions"] == []
    assert rep["results"] == n_res and len(rep["quarantined"]) == n_q
    assert rep["profiles"] >= 5
    assert not crash.exists() or crash.stat().st_size == 0
    assert (gd / "logs").is_dir() and glob.glob(str(gd / "logs" / "gui-*.log"))


def test_crash_log_python_error_before_qt(tmp_path):
    cp, _, crash = _run(["--self-test-crash", "python"], tmp_path)
    assert cp.returncode == 1
    txt = crash.read_text(encoding="utf-8")
    assert "RuntimeError: --self-test-crash" in txt and "sbench-gui" in txt and "argv:" in txt


def test_crash_log_native_crash(tmp_path):
    cp, _, crash = _run(["--self-test-crash", "native"], tmp_path)
    assert cp.returncode != 0
    txt = crash.read_text(encoding="utf-8")
    assert "Fatal Python error" in txt or "Windows fatal exception" in txt
    assert "app.py" in txt                                     # Python stack of the crashing thread


def test_crash_log_import_error(tmp_path):
    """A broken bundle (sbgui.app not importable) must still end up in crash.log, not vanish."""
    crash = tmp_path / "crash"
    code = ("import runpy, sys; sys.modules['sbgui.app'] = None; sys.argv = ['sbench-gui']; "
            "runpy.run_module('sbgui', run_name='__main__')")
    env = {**os.environ, "SBENCH_CRASH_DIR": str(crash), "SBENCH_NO_DIALOGS": "1"}
    cp = subprocess.run([sys.executable, "-c", code], cwd=GUI, env=env, capture_output=True, text=True, timeout=60)
    assert cp.returncode == 1
    assert "ModuleNotFoundError: import of sbgui.app halted" in (crash / "crash.log").read_text(encoding="utf-8")


def test_empty_crash_log_removed_at_exit(tmp_path):
    cp, _, crash = _run(["--smoke-start", "0.5", "--platforms", "offscreen", "--report", str(tmp_path / "r.json")], tmp_path)
    assert cp.returncode == 0 and not crash.exists()


def test_errors_report_appends_to_crash_log(tmp_path, monkeypatch):
    from sbgui import errors, crashlog
    monkeypatch.setenv("SBENCH_CRASH_DIR", str(tmp_path))
    monkeypatch.setitem(errors._state, "quiet", True)
    try:
        raise ValueError("Testfehler im Slot")
    except ValueError:
        errors.report(*sys.exc_info())
    assert "ValueError: Testfehler im Slot" in open(crashlog.crash_file(), encoding="utf-8").read()
    errors.take_seen()


def test_crashlog_paths(monkeypatch):
    from sbgui import crashlog
    assert crashlog.crash_file({"LOCALAPPDATA": r"C:\Users\DEMO-USER\AppData\Local"}).replace("/", "\\").endswith(
        r"C:\Users\DEMO-USER\AppData\Local\sbench\crash.log")
    assert crashlog.crash_file({"SBENCH_CRASH_DIR": "/x"}) == os.path.join("/x", "crash.log")
    assert not crashlog.interactive(["--self-test"]) and not crashlog.interactive(["--smoke-start", "3"])
    monkeypatch.delenv("SBENCH_NO_DIALOGS", raising=False)
    assert crashlog.interactive([])
