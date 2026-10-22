"""Final contract on main (core M1, 5670020): schema/ is the single source; job_name in sb-runs/1, numa_nodes[].cpus always
an array, histogram with 32 classes per power of two, unimplemented test types absent from tests[], support export = --all."""
import json, math, os, subprocess, sys, time

import pytest

import sb_contract
from conftest import GUI, demo, local_job, profile

MOCK = os.path.join(GUI, "mock_sbench.py")


def mock(*args, timeout=60):
    return subprocess.run([sys.executable, MOCK, *args], capture_output=True, text=True, timeout=timeout, encoding="utf-8")


@pytest.fixture(scope="module")
def V():
    return sb_contract.validators()


def test_single_schema_source():
    assert not os.path.exists(os.path.join(GUI, "contract")), "gui/contract/ copy must be gone (schema/ on main is the source)"
    assert os.path.isfile(os.path.join(sb_contract.base_dir(), "sb-bundle-1.schema.json"))
    spec = open(os.path.join(GUI, "sbench-gui.spec"), encoding="utf-8").read()
    assert '("contract", "contract")' not in spec and '("../schema", "schema")' in spec


def test_numa_cpus_always_array(mock_env, V):
    from sbgui.model import numa_cpus
    for kind in ("host", "vm", "vm-san"):
        p = subprocess.run([sys.executable, MOCK, "info", "--json"], capture_output=True, text=True, encoding="utf-8",
                           env=dict(os.environ, SBENCH_MOCK_HOST=kind))
        info = json.loads(p.stdout)
        assert not list(V["sb-info/1"].iter_errors(info))
        nodes = info["meta"]["numa_nodes"]
        assert nodes and all(isinstance(n["cpus"], list) for n in nodes)
        assert all(numa_cpus(n) == len(n["cpus"]) for n in nodes)
    for f in ("info.json", "result-oltp.json", "result-quad-vm.json"):
        nodes = json.load(open(demo(f), encoding="utf-8"))["meta"]["numa_nodes"]
        assert all(isinstance(n["cpus"], list) for n in nodes), f


def test_runs_job_name_required(mock_env, monkeypatch, V):
    bad = {"schema": "sb-runs/1", "runs": [{"run_id": "x", "pid": 1, "started_utc": "2026-10-20T07:15:00Z", "out": "o.json",
                                            "progress_file": "p.jsonl"}]}
    assert any("job_name" in e.message for e in V["sb-runs/1"].iter_errors(bad))
    monkeypatch.setenv("SBENCH_MOCK_SPEED", "1")
    jp = local_job(profile("four-corners.json"), mock_env, tests=1, warmup_s=0, duration_s=20)
    p = subprocess.Popen([sys.executable, MOCK, "run", "--job", jp, "--out", str(mock_env / "r.json"), "--id", "runs-test"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        doc, t0 = {"runs": []}, time.time()
        while not doc["runs"] and time.time() - t0 < 15:
            time.sleep(0.2); doc = json.loads(mock("list", "--running", "--json").stdout)
        assert not list(V["sb-runs/1"].iter_errors(doc))
        assert [r["job_name"] for r in doc["runs"]] == ["Four Corners + 70/30"]       # job.name
    finally:
        mock("stop", "--id", "runs-test"); p.wait(30)


@pytest.fixture(scope="module", params=[("1.1", {"type": "cpu", "workload": "int", "duration_s": 5}),
                                        ("1.2", {"type": "hyperv_info"})], ids=["m1-cpu", "m2-hyperv"])
def mixed_run(tmp_path_factory, request):
    """storage + <unimplemented> + storage job against the mock: core M1 (contract 1.1) skips cpu, core M2 (1.2) still
    skips hyperv_info / net_* (E_NOT_IMPLEMENTED, absent from tests[], index keeps the job position)."""
    contract, skipped = request.param
    tmp = tmp_path_factory.mktemp("mixed")
    env = dict(os.environ, SBENCH_DATA_DIR=str(tmp / "data"), SBENCH_MOCK_SPEED="50", SBENCH_MOCK_CONTRACT=contract)
    env.pop("SBENCH_MOCK_ALL_TYPES", None); env.pop("SBENCH_EXE", None)
    job = json.loads(open(local_job(profile("four-corners.json"), tmp, tests=2, warmup_s=0, duration_s=1), encoding="utf-8").read())
    job["tests"][1].update(warmup_s=0, duration_s=1)
    job["tests"].insert(1, skipped)
    jp = tmp / "mixed.json"; jp.write_text(json.dumps(job), encoding="utf-8")
    out = tmp / "r.json"
    p = subprocess.run([sys.executable, MOCK, "run", "--job", str(jp), "--out", str(out), "--progress", "stdout", "--tick-ms", "100"],
                       capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
    evs = [json.loads(ln) for ln in p.stdout.splitlines() if ln.strip()]
    return p.returncode, evs, json.loads(out.read_text(encoding="utf-8")), out, job


def test_unimplemented_types_absent(mixed_run, V):
    sys.path.insert(0, os.path.join(GUI, "tools"))
    import check_mock
    code, evs, res, out, job = mixed_run
    assert code == 0
    assert [e for e in evs if e["ev"] == "error"] == [{"ev": "error", "code": "E_NOT_IMPLEMENTED", "test": 1, "msg": evs[[e["ev"] for e in evs].index("error")]["msg"]}]
    assert [t["index"] for t in res["tests"]] == [0, 2] and {t["type"] for t in res["tests"]} == {"storage"}
    assert res["run_id"] and not list(V["sb-result/1"].iter_errors(res))
    assert check_mock._doc_errs(res) == []                         # index gaps are valid (job position kept)
    assert check_mock._stream_errs("\n".join(json.dumps(e) for e in evs)) == []


def test_histogram_32_per_octave(mixed_run):
    res = mixed_run[2]
    for t in res["tests"]:
        h = t["histogram"]; le = [b["le_us"] for b in h if b["le_us"] is not None]
        assert all(b["count"] > 0 for b in h)                       # only non-empty buckets (like the core)
        assert all(b2 > b1 for b1, b2 in zip(le, le[1:]))
        steps = [math.log2(b2 / b1) * 32 for b1, b2 in zip(le, le[1:])]
        assert min(steps) == pytest.approx(1, abs=0.05)               # 32 classes per power of two
        assert all(abs(s - round(s)) < 0.05 for s in steps)
        assert all(b["le_us"] is not None for b in h[:-1])            # overflow (null) only last
        assert sum(b["count"] for b in h) == t["summary"]["io_count"]


def test_gui_handles_missing_tests(qapp, mixed_run, tmp_path):
    from sbgui.pages.compare import Compare
    from sbgui.pages.live import LiveView
    from sbgui.report import build_html
    from sbgui.sbench import Replay
    code, evs, res, out, job = mixed_run
    empty = dict(res, tests=[]); ep = tmp_path / "empty.json"; ep.write_text(json.dumps(empty), encoding="utf-8")
    cmp_ = Compare([str(out), str(ep), demo("result-quad-host.json")])   # update() runs in __init__
    for i in range(cmp_.list.count()):
        cmp_.list.item(i).setCheckState(cmp_.list.item(i).checkState())
    cmp_.update()
    assert "<table>" in build_html([res, empty])
    rp = tmp_path / "p.jsonl"; rp.write_text("\n".join(json.dumps(e) for e in evs), encoding="utf-8")
    live = LiveView(); rep = Replay(str(rp)); live.attach(rep, job, tick_ms=100)
    total_before = live.total; rep.feed(); live.refresh(); live.timer.stop()
    assert live.skipped == [1] and live.total == total_before - job["tests"][1].get("duration_s", 0)
    assert "übersprungen" in live.run_chip.text() or live.phase == "done"


def test_bundle_args():
    from sbgui.sbench import bundle_args
    assert bundle_args("o.zip")[:4] == ["bundle", "--out", "o.zip", "--all"]
    assert "--all" not in bundle_args("o.zip", ["r.json"]) and bundle_args("o.zip", ["r.json"])[3:5] == ["--results", "r.json"]
    a = bundle_args("o.zip", None, ["L", "P"], True)
    assert a.count("--include") == 2 and "--anonymize" in a and a[-2:] == ["--progress", "stdout"]


def test_export_button_bundles_all(qapp, mock_env, monkeypatch, V):
    """Export page 'Ergebnisse für Support exportieren' -> dialog (default --all) -> sbench bundle --all; ZIP valid."""
    from PySide6.QtCore import QCoreApplication
    from sbgui import main_window, sbench
    from sbgui.app import build_window, parse
    from sbgui.overlay import SupportDialog
    # one finished run in the data dir so --all has something to bundle
    assert mock("run", "--job", local_job(profile("four-corners.json"), mock_env, tests=1, warmup_s=0, duration_s=1),
                "--out", str(mock_env / "done.json"), "--id", "bundle-src").returncode == 0
    calls, orig = [], sbench.bundle
    monkeypatch.setattr(sbench, "bundle", lambda *a, **k: (calls.append(sbench.bundle_args(*a[:2], **{x: y for x, y in k.items() if x != "parent"})), orig(*a, **k))[1])
    zp = str(mock_env / "support.zip")
    monkeypatch.setattr(main_window.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (zp, "")))
    win = build_window(parse([]), deterministic=False)
    page = win.pages["export"]
    btn = next(b for b in page.findChildren(main_window.QPushButton) if "Support exportieren" in b.text())
    btn.click()
    dlg = win.centralWidget().findChildren(SupportDialog)[-1]
    assert dlg.r_all.isChecked() and not dlg.r_sel.isEnabled()
    next(b for b in dlg.findChildren(main_window.QPushButton) if b.text().startswith("Speichern")).click()
    assert calls and "--all" in calls[0] and "--results" not in calls[0]
    t0 = time.time()
    while not os.path.exists(zp) and time.time() - t0 < 20:
        QCoreApplication.processEvents(); time.sleep(0.05)
    time.sleep(0.3); QCoreApplication.processEvents()
    import zipfile
    man = json.loads(zipfile.ZipFile(zp).read("manifest.json"))
    assert not list(V["sb-bundle/1"].iter_errors(man))
    paths = {f["path"] for f in man["files"]}
    assert {"results/bundle-src.json", "runs/bundle-src.jsonl", "jobs/bundle-src.json"} <= paths
    win.close()
