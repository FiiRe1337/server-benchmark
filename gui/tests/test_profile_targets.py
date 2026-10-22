"""Profile editor: + Datei / + Volume / + Disk, test selector, '<n> Fehler' chip (fix for v0.1.0-beta.1)."""
import json, os

import pytest

from conftest import profile


def _fake_not_found(job_path):
    """What the real core reports on a PC without D:\\sbench: E_TARGET_NOT_FOUND for every test's target."""
    job = json.load(open(job_path, encoding="utf-8"))
    errs = [{"path": f"/tests/{i}/targets/{k}/path", "code": "E_TARGET_NOT_FOUND", "msg": "Verzeichnis D:\\sbench existiert nicht."}
            for i, t in enumerate(job["tests"]) for k, _ in enumerate(t.get("targets", []))]
    return {"schema": "sb-validate/1", "ok": not errs, "errors": errs, "warnings": []}


@pytest.fixture
def editor(qapp, mock_env, monkeypatch):
    from PySide6.QtWidgets import QMainWindow
    from sbgui.pages.profiles import ProfileEditor
    (mock_env / "gui" / "profiles").mkdir(parents=True, exist_ok=True)
    win = QMainWindow(); pe = ProfileEditor([os.path.dirname(profile("four-corners.json")), str(mock_env / "gui" / "profiles")])
    win.setCentralWidget(pe); win.resize(1440, 900); win.show()
    for i in range(pe.list.count()):
        if "four-corners" in pe.list.item(i).data(0x0100):
            pe.list.setCurrentRow(i)
    msgs = []
    from PySide6.QtWidgets import QMessageBox
    for kind in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, kind, staticmethod(lambda _p=None, t="", x="", *a, k=kind, **kw: msgs.append((k, t, x)) or QMessageBox.Ok))
    pe._msgs = msgs
    yield pe
    win.close(); win.deleteLater()


def _overlays(pe):
    from sbgui.overlay import Overlay
    return [o for o in pe.findChildren(Overlay) if o.isVisible()]


def test_four_corners_loads_all_tests(editor):
    pe = editor
    assert pe.job["name"] == "Four Corners + 70/30" and len(pe.job["tests"]) == 5
    assert pe.test_box.count() == 5 and pe.test_box.isEnabled()
    pe.test_box.setCurrentIndex(3)
    assert pe.ti == 3 and pe.specs.item(0, 2).text() == "64KiB" and pe.f["workers"].value() == 2
    assert len(pe.to_job()["tests"]) == 5


def test_chip_and_issue_list_agree(editor, monkeypatch):
    """beta.1: 'Speichern' / profile switch reset the list to 'Noch nicht geprüft.' but the chip kept '5 Fehler'."""
    from sbgui import sbench
    pe = editor
    monkeypatch.setattr(sbench, "validate", _fake_not_found)
    pe.validate()
    assert pe.valid_chip.text().startswith("5 Fehler")
    assert pe.targets.item(0, 4).text() == "nicht gefunden"          # beta.1 showed 'bereit'
    pe.save()                                                         # unchanged job: result stays visible, consistent
    assert pe.valid_chip.text().startswith("5 Fehler") and pe.last is not None
    pe.list.setCurrentRow(0 if "four-corners" not in pe.list.item(0).data(0x0100) else 1)
    assert pe.valid_chip.text() == "nicht geprüft" and pe.last is None
    assert pe.issues.itemAt(0).widget().text() == "Noch nicht geprüft."


def test_edit_marks_result_stale(editor, monkeypatch):
    from sbgui import sbench
    pe = editor
    monkeypatch.setattr(sbench, "validate", _fake_not_found)
    pe.validate(); pe.f["queue_depth"].setValue(16)
    assert pe.valid_chip.text() == "nicht geprüft (geändert)" and pe.last is None


def test_add_file_target_all_tests(editor, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog
    pe = editor
    called = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: called.append(a) or (str(tmp_path / "t.dat"), "")))
    dlg = pe.add_target("file")
    assert called and dlg is not None and dlg in _overlays(pe)
    assert dlg.replace.isChecked()          # D:\sbench does not exist here -> 'replace' preselected
    assert dlg.all_tests.isChecked()
    dlg.size.setCurrentText("4GiB"); dlg.ok.click()
    for t in pe.job["tests"]:
        assert t["targets"] == [{"path": str(tmp_path / "t.dat"), "kind": "file", "file_size": "4GiB"}]
    assert pe.targets.rowCount() == 1 and pe.targets.item(0, 2).text() == "4GiB"


def test_file_dialog_cancel_does_nothing_harmful(editor, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    pe = editor
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))
    before = json.dumps(pe.job)
    assert pe.add_target("file") is None and json.dumps(pe.job) == before


def test_add_volume_and_disk_from_info(editor):
    pe = editor
    pe.info_provider = lambda: json.load(open(os.path.join(os.path.dirname(profile("x")), "..", "demo", "info.json"), encoding="utf-8"))
    dlg = pe.add_target("volume")
    assert dlg.list.count() == 2 and "D:" in dlg.list.item(1).text()
    dlg.list.setCurrentRow(1); dlg.all_tests.setChecked(False); dlg.replace.setChecked(False); dlg.ok.click()
    tg = pe.job["tests"][pe.ti]["targets"]
    assert tg[-1] == {"path": "\\\\.\\D:", "kind": "volume"} and len(tg) == 2
    assert pe.targets.item(1, 4).text() == "Admin nötig"
    assert all(len(t["targets"]) == 1 for i, t in enumerate(pe.job["tests"]) if i != pe.ti)
    dlg = pe.add_target("physical")
    assert dlg.list.count() == 3 and "PhysicalDrive1" in dlg.list.item(1).text()
    assert "3,5 TiB" in dlg.list.item(1).text()
    assert any("Administratorrechte" in w.text() for w in dlg.findChildren(type(pe.title)))


def test_empty_disk_list_explains_admin(editor):
    pe = editor
    pe.info_provider = lambda: {"schema": "sb-info/1", "meta": {"elevated": False}, "targets": {"volumes": [], "physical_disks": []}}
    assert pe.add_target("physical") is None
    kind, title, text = pe._msgs[-1]
    assert kind == "warning" and "Disks" in title and "Administratorrechte" in text


def test_info_failure_is_shown(editor):
    pe = editor

    def boom():
        raise TimeoutError("info --json")
    pe.info_provider = boom
    assert pe.add_target("volume") is None
    assert "TimeoutError" in pe._msgs[-1][2]


def test_issue_popup_lists_fields_and_focuses(editor, monkeypatch):
    from sbgui import sbench
    from sbgui.issue_popup import IssuePopup
    pe = editor
    monkeypatch.setattr(sbench, "validate", _fake_not_found)
    pe.validate()
    pe.valid_chip.clicked.emit()
    pop = next(o for o in _overlays(pe) if isinstance(o, IssuePopup))
    assert len(pop.rows) == 5
    texts = [w.text() for w in pop.findChildren(type(pe.title))]
    assert "Test 2 »4K Random Write« · Ziel 1 · Pfad" in texts
    assert any(t.startswith("Ziel bzw. Verzeichnis existiert nicht") for t in texts)
    pop.rows[3][0].clicked.emit()                  # /tests/3/targets/0/path
    assert pe.ti == 3 and pe.test_box.currentIndex() == 3 and pe.targets.currentRow() == 0
    assert pe.valid_chip.text().startswith("5 Fehler")   # switching tests keeps the result of the whole job


def test_start_with_errors_shows_popup(editor, monkeypatch):
    from sbgui import sbench
    from sbgui.issue_popup import IssuePopup
    pe = editor
    monkeypatch.setattr(sbench, "validate", _fake_not_found)
    assert isinstance(pe.start_flow(), IssuePopup)


def test_unchecked_chip_click_validates(editor):
    pe = editor
    assert pe.valid_chip.text() == "nicht geprüft"
    pe.valid_chip.clicked.emit()
    assert pe.last is not None and pe.valid_chip.text() != "nicht geprüft"


def test_validate_failure_is_shown(editor, monkeypatch):
    from sbgui import sbench

    def boom(_p):
        raise TimeoutError("validate --job")
    monkeypatch.setattr(sbench, "validate", boom)
    assert editor.validate() is None and editor._msgs[-1][0] == "critical"


def test_specs_buttons(editor):
    pe = editor
    n = pe.specs.rowCount()
    pe.add_spec(); assert pe.specs.rowCount() == n + 1
    pe.specs.setCurrentCell(0, 1); pe.dup_spec(); assert pe.specs.rowCount() == n + 2
    assert pe.to_job()["tests"][pe.ti]["access_specs"][1]["block_size"] == "4KiB"


def test_remove_target_rules(editor):
    pe = editor
    pe.targets.setCurrentCell(-1, -1); assert pe.remove_target() is None and "auswählen" in pe._msgs[-1][2]
    pe.targets.selectRow(0); assert pe.remove_target() is None and "mindestens ein Ziel" in pe._msgs[-1][2]


def test_new_profile_unique(editor):
    pe = editor
    a = pe.new_profile(); b = pe.new_profile()
    assert a != b and os.path.exists(a) and os.path.exists(b)
    assert pe.job["name"] == "Neues Profil" and pe.job["tests"][0]["targets"][0]["path"]


def test_field_names():
    from sbgui.issues import field_name, reason
    job = {"tests": [{"name": "A"}, {}]}
    assert field_name("/tests/0/access_specs/1/block_size", job) == "Test 1 »A« · Spec 2 · Block"
    assert field_name("/tests/1/access_specs", job) == "Test 2 · Access Specs · Gewichtssumme"
    assert field_name("/tests/1/queue_depth", job) == "Test 2 · Queue Depth/Worker"
    assert field_name("", job) == "Job"
    assert reason({"code": "E_WEIGHT_SUM", "msg": "Summe 90"}) == "Summe der Gewichte ist nicht 100 %: Summe 90"


def test_excepthook_logs_and_shows(qapp, tmp_path, monkeypatch):
    import sys
    from PySide6.QtWidgets import QMessageBox, QPushButton
    from sbgui import errors
    shown = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.text()) or 0)
    old = (sys.excepthook, errors._state["quiet"])
    try:
        f = errors.install(str(tmp_path / "logs"))
        b = QPushButton("x"); b.clicked.connect(lambda: {}["fehlt"]); b.click()
        assert shown and "KeyError" in shown[0] and f in shown[0]
        assert "KeyError" in open(f, encoding="utf-8").read()
    finally:
        sys.excepthook = old[0]; errors._state["quiet"] = old[1]; errors._state["installed"] = False; errors.take_seen()
