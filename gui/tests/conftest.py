"""Shared fixtures. Tests run from gui/ (`python -m pytest`), headless (QT_QPA_PLATFORM=offscreen), against the mock core."""
import os, sys

import pytest

GUI = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, GUI)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtCore import QCoreApplication
    return QCoreApplication.instance() or QCoreApplication([])


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
