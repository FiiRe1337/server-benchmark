"""Shared fixtures. Tests run from gui/ (`python -m pytest`), headless (QT_QPA_PLATFORM=offscreen), against the mock core."""
import os, sys, tempfile

import pytest

GUI = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, GUI)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# never write into the developer's real %LOCALAPPDATA%\\sbench\\crash.log (sbgui/crashlog.py); subprocesses inherit it
os.environ.setdefault("SBENCH_CRASH_DIR", tempfile.mkdtemp(prefix="sbench-crash-test-"))


@pytest.fixture(scope="session")
def qapp():
    """One QApplication (widgets) for the whole session, themed like the real GUI."""
    from PySide6.QtWidgets import QApplication
    if QApplication.instance():
        return QApplication.instance()
    from sbgui.app import make_app
    return make_app()


@pytest.fixture(autouse=True)
def _fresh_core():
    """sbgui.core caches the resolved core per process; tests change SBENCH_* env, so resolve again per test."""
    from sbgui import core
    core.reset()
    yield
    core.reset()


@pytest.fixture
def mock_env(tmp_path, monkeypatch):
    """Isolated data/GUI dirs, mock core forced (no stray sbench.exe / SBENCH_EXE from the runner)."""
    monkeypatch.delenv("SBENCH_EXE", raising=False)
    monkeypatch.delenv("SBENCH_MOCK_ELEVATED", raising=False)
    monkeypatch.setenv("SBENCH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SBENCH_GUI_DIR", str(tmp_path / "gui"))
    monkeypatch.setenv("SBENCH_MOCK_SPEED", "50")
    return tmp_path


def demo(*p):
    return os.path.join(GUI, "demo", *p)


def profile(name):
    return os.path.join(GUI, "profiles", name)


def local_job(src, tmp, **test0):
    """Copy of a job/profile with every file target moved into tmp (on Windows the mock, like the real core,
    rejects file targets whose directory does not exist: D:\\sbench\\ on a CI runner -> E_TARGET_NOT_FOUND).
    Keyword arguments update tests[0] (e.g. duration_s=2); tests=1 keeps only the first test."""
    import json
    job = json.load(open(src, encoding="utf-8"))
    keep = test0.pop("tests", None)
    if keep:
        job["tests"] = job["tests"][:keep]
    for t in job["tests"]:
        for tg in t.get("targets", []):
            if tg["kind"] == "file":
                tg["path"] = str(tmp / os.path.basename(tg["path"].replace("\\", "/")))
    job["tests"][0].update(test0)
    out = tmp / ("job-" + os.path.basename(src))
    out.write_text(json.dumps(job), encoding="utf-8")
    return str(out)


def _close_widgets():
    """Close and delete leftover top-level widgets (live view, results pages, pyqtgraph plots) and their timers while
    the QApplication still exists, so interpreter finalization does not tear down Qt objects in random order."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return
    for w in QApplication.topLevelWidgets():
        w.close(); w.deleteLater()
    for _ in range(3):
        app.processEvents()


def pytest_sessionfinish(session, exitstatus):
    session.config._sb_exitstatus = int(exitstatus)


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    """Windows CI: all tests pass and the junit XML is written, but the process then dies with an access violation while
    Windows unloads the Qt DLLs at process exit (seen even inside os._exit, faulthandler trace in run 39579246514) and
    python reports exit code 1. Clean up the Qt objects explicitly and, on Windows, end the process with pytest's own
    exit status via TerminateProcess, which skips the DLL detach handlers (reports are already written here)."""
    import faulthandler
    st = getattr(config, "_sb_exitstatus", None)
    faulthandler.enable(file=sys.__stderr__, all_threads=True)   # any other teardown crash shows up in the CI log
    _close_widgets()
    if sys.platform == "win32" and st is not None:
        import ctypes
        sys.stdout.flush(); sys.stderr.flush(); sys.__stdout__.flush(); sys.__stderr__.flush()
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.TerminateProcess(ctypes.c_void_p(k32.GetCurrentProcess()), int(st))
        os._exit(st)                                              # not reached
