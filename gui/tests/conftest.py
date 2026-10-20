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
