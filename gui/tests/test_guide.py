"""--guide helpers and GUI changes used by the README guide: guide job validity, real-start result list without mock
demo data, distinguishable compare labels for runs of the same host, result reload after a run."""
import json, os

from sbgui import app
from sbgui.guide import guide_job


def test_guide_job_valid(tmp_path):
    from sb_contract import validators
    for wk, qd in ((2, 8), (1, 1)):
        j = guide_job("x", str(tmp_path / "t.dat"), wk, qd)
        assert not list(validators()["sb-job/1"].iter_errors(j))
        assert [t["name"] for t in j["tests"]] == ["4K random 70/30", "64K sequenziell lesen"]   # same names -> comparable


def test_real_start_has_no_demo_results(tmp_path):
    (tmp_path / "r1.json").write_text("{}")
    assert app.result_files(None, [str(tmp_path)]) == [str(tmp_path / "r1.json")]
    assert any("result-oltp" in f for f in app.result_files(app.DEMO))


def test_build_window_real_mode_excludes_demo(qapp, mock_env):
    win = app.build_window(app.parse([]), deterministic=False, extra_results=[str(mock_env / "none")])
    assert win.pages["compare"].results == []
    win.close()


def test_run_label_distinguishes_same_host():
    from sbgui.pages.compare import run_label
    r = json.load(open(os.path.join(app.DEMO, "result-quad-host.json"), encoding="utf-8"))
    a, b = json.loads(json.dumps(r)), json.loads(json.dumps(r))
    b["tests"][0]["config"]["queue_depth"] = 1; b["tests"][0]["config"]["workers"] = 1
    la, lb = run_label(a, [a, b]), run_label(b, [a, b])
    assert la != lb and "QD 1" in lb
    assert run_label(a) == run_label(a, [a])          # alone: plain host label


def test_reload_results_after_run(qapp, mock_env):
    from conftest import demo
    win = app.build_window(app.parse([]), deterministic=False, extra_results=[os.path.join(os.environ["SBENCH_GUI_DIR"], "results")])
    os.makedirs(os.path.join(os.environ["SBENCH_GUI_DIR"], "results"), exist_ok=True)
    dst = os.path.join(os.environ["SBENCH_GUI_DIR"], "results", "new.json")
    open(dst, "w", encoding="utf-8").write(open(demo("result-quad-vm.json"), encoding="utf-8").read())
    win.reload_results()
    assert dst in [f for f, _ in win.pages["compare"].results] and dst in win.pages["export"].selected_files() + [f for f, _ in win.pages["export"].results]
    win.close()
