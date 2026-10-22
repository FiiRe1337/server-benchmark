"""Click smoke test (sbgui/clicktest.py): every QPushButton/QToolButton/QAction on every page is pressed with
QFileDialog/QMessageBox/QDialog.exec/QMenu.exec replaced; no exception, no dead button, known buttons react.
The same runner runs inside the frozen EXE: `sbench-gui --self-test-clicks` (build-gui.yml, windows-2022)."""
import json, os, subprocess, sys

from conftest import GUI


def _win(qapp):
    from sbgui.app import build_window, parse
    win = build_window(parse([]), deterministic=True); win.resize(1440, 900); win.show()
    return win


def test_every_button_on_every_page(qapp, mock_env):
    from sbgui import clicktest
    win = _win(qapp)
    rep = clicktest.run(win)
    assert rep["ok"], "\n".join(rep["summary"])
    texts = {b["text"] for b in rep["buttons"]}
    assert {"+ Datei", "+ Volume", "+ Disk"} <= texts
    by = {b["text"]: b for b in rep["buttons"] if b["page"] == "profiles"}
    assert any("getSaveFileName" in c for c in by["+ Datei"]["calls"]) and by["+ Datei"]["overlays"] == ["TargetPicker"]
    assert by["+ Volume"]["overlays"] == ["TargetPicker"] and by["+ Disk"]["overlays"] == ["TargetPicker"]
    assert all(b["wired"] for b in rep["buttons"])
    win.close(); win.deleteLater()


def test_dead_button_is_reported(qapp, mock_env):
    """The runner itself must catch the beta.1 bug class (button without handler) and raising slots."""
    from PySide6.QtWidgets import QPushButton
    from sbgui import clicktest
    win = _win(qapp)
    pe = win.pages["profiles"]
    dead = QPushButton("+ Tot"); pe.layout().addWidget(dead)
    boom = QPushButton("+ Boom"); boom.clicked.connect(lambda: 1 / 0); pe.layout().addWidget(boom)
    rep = clicktest.run(win)
    assert not rep["ok"]
    assert "profiles: + Tot" in rep["dead"]
    assert "profiles: + Boom" in rep["failed"]
    b = next(x for x in rep["buttons"] if x["text"] == "+ Boom")
    assert "ZeroDivisionError" in b["errors"][0]
    win.close(); win.deleteLater()


def test_self_test_clicks_cli(tmp_path, mock_env):
    """`python -m sbgui --self-test-clicks --report f` (same entry the frozen EXE uses in CI)."""
    rf = tmp_path / "clicks.json"
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    cp = subprocess.run([sys.executable, "-m", "sbgui", "--self-test-clicks", "--platforms", "offscreen", "--report", str(rf)],
                        cwd=GUI, env=env, capture_output=True, text=True, timeout=240)
    rep = json.loads(rf.read_text(encoding="utf-8"))
    assert cp.returncode == 0 and rep["ok"], rep.get("summary") or rep.get("error")
    assert rep["count"] >= 25


def test_click_test_covers_m2_buttons(qapp, mock_env):
    """M2 buttons (+ CPU / + RAM / Test entfernen, results error toggle) are pressed and react; the storage buttons are
    pressed with a storage test selected (click_prepare), so they never hit a cpu/memory test."""
    from sbgui import clicktest
    win = _win(qapp)
    rep = clicktest.run(win)
    assert rep["ok"], "\n".join(rep["summary"])
    by = {(b["page"], b["text"]): b for b in rep["buttons"]}
    for t in ("+ CPU", "+ RAM", "Test entfernen", "+ Datei", "+ Volume", "+ Disk", "Entfernen", "+ Spec", "Duplizieren"):
        assert by[("profiles", t)]["enabled"] and by[("profiles", t)]["observable"], t
    tog = [b for b in rep["buttons"] if b["page"] == "results" and "weitere" in b["text"]]
    assert len(tog) == 1 and tog[0]["observable"]
    win.close(); win.deleteLater()


def test_target_buttons_only_for_storage_tests(qapp, mock_env, monkeypatch):
    """cpu/memory test selected: target buttons disabled with a tooltip; called anyway -> message, job unchanged."""
    from PySide6.QtWidgets import QMessageBox
    win = _win(qapp)
    pe = win.pages["profiles"]
    msgs = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: msgs.append(a[1:3])))
    assert win._quickstart("cpu") and pe.job["tests"][pe.ti]["type"] == "cpu"
    for b in (pe.b_file, pe.b_vol, pe.b_disk, pe.b_tdel):
        assert not b.isEnabled() and "nur bei Storage-Tests" in b.toolTip()
    before = json.dumps(pe.job)
    assert pe.add_target("file") is None and pe.remove_target() is None and pe.add_spec() is None
    assert json.dumps(pe.job) == before and "Storage" in msgs[-1][1]
    st = pe.storage_indices()
    if st:
        pe.test_box.setCurrentIndex(st[0])
        assert all(b.isEnabled() for b in (pe.b_file, pe.b_vol, pe.b_disk, pe.b_tdel))
    assert win._quickstart("memory") and pe.job["tests"][pe.ti]["type"] == "memory"
    assert win._quickstart("storage4k") and pe.ti == 2 and "Four Corners" in pe.title.text()
    n = len(pe.job["tests"]); pe.add_test("cpu"); assert len(pe.job["tests"]) == n + 1 and pe.valid_chip.text() == "nicht geprüft"
    pe.remove_test(); assert len(pe.job["tests"]) == n
    win.close(); win.deleteLater()
