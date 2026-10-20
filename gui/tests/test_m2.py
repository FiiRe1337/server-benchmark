"""M2 / contract 1.2: cpu + memory tests in mock, model helpers, live view, results, compare, profile editor."""
import json, os, subprocess, sys, time

import pytest

import sb_contract
from conftest import GUI, demo, profile

MOCK = os.path.join(GUI, "mock_sbench.py")
sys.path.insert(0, os.path.join(GUI, "tools"))
from check_mock import _doc_errs, _stream_errs  # noqa: E402

from sbgui import model  # noqa: E402


def mock(*args, env=None, timeout=120):
    return subprocess.run([sys.executable, MOCK, *args], capture_output=True, text=True, timeout=timeout, encoding="utf-8",
                          env=dict(os.environ, **(env or {})))


def write_job(tmp, tests, name="M2-Test"):
    p = tmp / "job.json"
    p.write_text(json.dumps({"schema": "sb-job/1", "name": name, "tests": tests}), encoding="utf-8")
    return str(p)


def validate(tmp, tests, host="host"):
    p = mock("validate", "--job", write_job(tmp, tests), env={"SBENCH_MOCK_HOST": host})
    doc = json.loads(p.stdout)
    assert not _doc_errs(doc), _doc_errs(doc)
    return p.returncode, {e["code"] for e in doc["errors"]}, {w["code"] for w in doc["warnings"]}


# ------------------------------------------------------------------ model helpers
def test_tick_metric_and_labels():
    assert model.tick_metric({"iops": 1, "ops_per_s": 2}) == "iops"
    assert model.tick_metric({"ops_per_s": 2}) == "ops_per_s"
    assert model.tick_metric({"gbps": 1.0}) == "gbps"
    assert model.tick_metric({"latency_ns": 90.0}) == "latency_ns"
    assert model.tick_metric({}, {"type": "memory", "mode": "latency"}) == "latency_ns"
    assert model.tick_metric({}, {"type": "cpu"}) == "ops_per_s"
    assert model.derived_test_label({"type": "cpu"}) == "CPU"
    assert "mixed" in model.derived_test_label({"type": "cpu", "workload": "mixed", "threads": 0})
    lab = model.derived_test_label({"type": "memory", "mode": "bandwidth", "op": "copy", "nontemporal": True, "buffer_size": "256MiB",
                                    "threads": 4, "simd": "avx2", "numa_node": 0, "mem_node": 1})
    assert "copy" in lab and "NT" in lab and "avx2" in lab and "0→1" in lab
    assert model.lower_is_better("latency_ns") and not model.lower_is_better("gbps")


def test_fmt_metric_german():
    assert model.fmt_metric("gbps", 220.61) == "220,6"
    assert model.fmt_metric("gbps", 1.724) == "1,72"
    assert model.fmt_metric("latency_ns", 96.71) == "96,7"
    assert model.fmt_metric("ops_per_s", 30_550_000) == "30,55 M"
    assert model.fmt_metric("score", 80389.2) == "80.389"
    assert model.fmt_metric("gbps", None) == "–"


def test_estimated_score_reference():
    assert model.estimated_score("int", 2.8e8) == pytest.approx(1000)
    assert model.estimated_score("unknown", 1.0) is None


def test_read_flat_csv_11_and_12():
    c11 = ",".join(model.FLAT_COLUMNS_11) + "\r\n" + ",".join(["x"] * 0) + "\r\n"
    assert len(model.FLAT_COLUMNS_12) == len(model.FLAT_COLUMNS_11) + 4
    assert model.FLAT_COLUMNS_12[-4:] == ["ops_per_s", "gbps", "latency_ns", "score"]
    cols, rows = model.read_flat_csv(c11)
    assert cols == model.FLAT_COLUMNS_11 and rows == []
    vals = {k: "" for k in model.FLAT_COLUMNS_12}
    vals.update(type="cpu", scope="total", ops_per_s="1500000", score="5357.1")
    txt = ",".join(model.FLAT_COLUMNS_12) + "\r\n" + ",".join(vals[k] for k in model.FLAT_COLUMNS_12) + "\r\n"
    _, rows = model.read_flat_csv(txt)
    assert rows[0]["ops_per_s"] == 1500000 and rows[0]["score"] == pytest.approx(5357.1) and rows[0]["gbps"] is None
    with pytest.raises(ValueError):
        model.read_flat_csv("foo,bar\r\n1,2\r\n")


def test_compare_compute_rows():
    from sbgui.pages.compare import compute_rows, rel_delta
    host = json.load(open(demo("result-m2-host.json"), encoding="utf-8"))
    vm = json.load(open(demo("result-m2-vm.json"), encoding="utf-8"))
    rows = compute_rows([host, vm])
    keys = {k for _, k, _, _ in rows}
    assert keys <= {"score", "gbps", "latency_ns"} and {"score", "gbps", "latency_ns"} <= keys
    assert all(v[0] is not None for _, _, v, _ in rows)
    assert rel_delta(110, 100, "gbps") == pytest.approx(10)
    assert rel_delta(110, 100, "latency_ns") == pytest.approx(-10)       # slower latency = worse
    assert rel_delta(None, 100, "gbps") is None and rel_delta(1, 0, "gbps") is None


# ------------------------------------------------------------------ mock, contract 1.2
def test_m2_demo_and_profiles_valid():
    for f in ("result-m2-host.json", "result-m2-vm.json", "result-m2-numa.json", "job-m2.json"):
        assert not _doc_errs(json.load(open(demo(f), encoding="utf-8"))), f
    assert not _stream_errs(open(demo("progress-m2.jsonl"), encoding="utf-8").read())
    for f in ("cpu-ram.json", "numa-lokal-remote.json"):
        assert not _doc_errs(json.load(open(profile(f), encoding="utf-8"))), f


def test_profile_presets_schema_valid():
    from sbgui.pages.profiles import CPU_PRESETS, MEM_PRESETS, preset_test
    tests = [preset_test("cpu", i) for i in range(len(CPU_PRESETS))] + [preset_test("memory", i) for i in range(len(MEM_PRESETS))]
    doc = {"schema": "sb-job/1", "name": "Vorlagen", "tests": tests}
    assert not _doc_errs(doc), _doc_errs(doc)


def test_mock_run_compute_12(mock_env):
    jp = write_job(mock_env, [
        {"type": "cpu", "name": "cpu", "threads": 4, "workload": "int", "warmup_s": 0, "duration_s": 2},
        {"type": "memory", "name": "bw", "mode": "bandwidth", "op": "copy", "threads": 4, "buffer_size": "256MiB",
         "warmup_s": 0, "duration_s": 2, "nontemporal": True},
        {"type": "memory", "name": "lat", "mode": "latency", "buffer_size": "1GiB", "warmup_s": 0, "duration_s": 2, "numa_node": 0, "mem_node": 1},
    ])
    out, prog = mock_env / "r.json", mock_env / "p.jsonl"
    p = mock("run", "--job", jp, "--out", str(out), "--progress", f"file:{prog}", "--tick-ms", "250", "--id", "m2-run")
    assert p.returncode == 0, p.stderr
    res = json.loads(out.read_text(encoding="utf-8"))
    assert not _doc_errs(res), _doc_errs(res)
    assert not _stream_errs(prog.read_text(encoding="utf-8"))
    cpu, bw, lat = res["tests"]
    assert cpu["summary"]["ops_per_s"] > 0 and cpu["summary"]["score"] > 0 and len(cpu["per_worker"]) == 4
    assert [w["cpu"] for w in cpu["per_worker"]] == [0, 64, 2, 66]          # one per core, NUMA nodes alternating
    assert bw["summary"]["gbps"] > 0 and bw["summary"]["nontemporal"] is True
    assert lat["summary"]["latency_ns"] > 95 and lat["summary"]["mem_node"] == 1    # remote is slower
    assert all(n.get("ram_method") in ("total", "srat", "estimate") for n in res["meta"]["numa_nodes"])
    evs = [json.loads(line) for line in prog.read_text(encoding="utf-8").splitlines()]
    assert any(e["ev"] == "progress" and e["phase"] == "preparing" and e["test"] == 1 for e in evs)
    assert not any(e["ev"] == "phase" and e["name"] == "preparing" and e["test"] == 0 for e in evs)     # cpu: no preparing
    ticks = [e for e in evs if e["ev"] == "tick"]
    assert {model.tick_metric(e) for e in ticks} == {"ops_per_s", "gbps", "latency_ns"}
    assert all(len(e["workers"]) == 4 for e in ticks if "latency_ns" not in e and "workers" in e)
    # flat CSV like core export.c: 18 columns, CRLF, total + worker rows
    csvp = mock_env / "r.csv"
    assert mock("export", str(out), "--csv", str(csvp)).returncode == 0
    raw = csvp.read_bytes()
    assert b"\r\n" in raw
    cols, rows = model.read_flat_csv(str(csvp))
    assert cols == model.FLAT_COLUMNS_12
    tot = [r for r in rows if r["scope"] == "total"]
    assert [r["type"] for r in tot] == ["cpu", "memory", "memory"]
    assert tot[0]["score"] and tot[1]["gbps"] and tot[2]["latency_ns"]
    assert any(r["scope"] == "worker" and r["type"] == "cpu" for r in rows)


def test_mock_contract_11_compat(mock_env):
    jp = write_job(mock_env, [{"type": "cpu", "threads": 1, "duration_s": 1}, {"type": "storage", "targets": [
        {"kind": "file", "path": str(mock_env / "t.dat"), "file_size": "64MiB"}], "access_specs": [
        {"block_size": "4KiB", "read_pct": 100, "random_pct": 100, "weight": 100}], "workers": 1, "queue_depth": 1, "duration_s": 1, "warmup_s": 0}])
    out = mock_env / "r.json"
    p = subprocess.run([sys.executable, MOCK, "run", "--job", jp, "--out", str(out)], capture_output=True, text=True,
                       env=dict(os.environ, SBENCH_MOCK_CONTRACT="1.1"), timeout=120)
    assert p.returncode == 0, p.stderr
    res = json.loads(out.read_text(encoding="utf-8"))
    assert [t["type"] for t in res["tests"]] == ["storage"]                  # M1: cpu skipped
    assert all("ram_method" not in n for n in res["meta"]["numa_nodes"])


def test_mock_validate_compute_codes(mock_env):
    rc, err, _ = validate(mock_env, [{"type": "memory", "mode": "bandwidth", "buffer_size": "4KiB", "duration_s": 1}])
    assert rc == 2 and err & {"E_BUFFER_SIZE", "E_SCHEMA"}
    rc, err, _ = validate(mock_env, [{"type": "cpu", "numa_node": 7, "duration_s": 1}])
    assert rc == 2 and "E_NUMA_NODE" in err
    rc, err, _ = validate(mock_env, [{"type": "memory", "mode": "bandwidth", "simd": "neon", "buffer_size": "1GiB", "duration_s": 1}])
    assert rc == 2 and "E_SIMD_UNSUPPORTED" in err
    rc, err, warn = validate(mock_env, [{"type": "memory", "mode": "latency", "threads": 4, "buffer_size": "1GiB", "duration_s": 1}])
    assert rc == 0 and not err and "W_THREADS_IGNORED" in warn
    rc, err, warn = validate(mock_env, [{"type": "memory", "mode": "bandwidth", "op": "read", "nontemporal": True, "buffer_size": "1GiB",
                                         "duration_s": 1}])
    assert "W_NONTEMPORAL_IGNORED" in warn
    rc, err, warn = validate(mock_env, [{"type": "cpu", "threads": 4096, "duration_s": 1}])
    assert "W_OVERSUBSCRIBED" in warn


def test_mock_stop_gives_partial_compute(mock_env, monkeypatch):
    monkeypatch.setenv("SBENCH_MOCK_SPEED", "1")
    jp = write_job(mock_env, [{"type": "cpu", "threads": 2, "workload": "mixed", "warmup_s": 0, "duration_s": 30},
                              {"type": "memory", "mode": "latency", "buffer_size": "64MiB", "duration_s": 5}])
    out, prog = mock_env / "r.json", mock_env / "p.jsonl"
    p = subprocess.Popen([sys.executable, MOCK, "run", "--job", jp, "--out", str(out), "--progress", f"file:{prog}", "--tick-ms", "250",
                          "--id", "m2-stop"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        t0 = time.time()
        while time.time() - t0 < 20 and not (prog.exists() and prog.read_text(encoding="utf-8").count('"ev": "tick"') +
                                              prog.read_text(encoding="utf-8").count('"ev":"tick"') >= 3):
            time.sleep(0.2)
        mock("stop", "--id", "m2-stop")
        assert p.wait(30) == 3
    finally:
        if p.poll() is None:
            p.kill()
    res = json.loads(out.read_text(encoding="utf-8"))
    assert not _doc_errs(res), _doc_errs(res)
    assert res["aborted"] is True
    assert [t["type"] for t in res["tests"]] == ["cpu"] and res["tests"][0]["summary"]["ops_per_s"] > 0


# ------------------------------------------------------------------ GUI
def _replay(live, pred):
    from sbgui.sbench import Replay
    job = json.load(open(demo("job-m2.json"), encoding="utf-8"))
    rep = Replay(demo("progress-m2.jsonl")); live.attach(rep, job, tick_ms=250)
    rep.feed_until(pred); live.refresh(); live.timer.stop()
    return job


def test_live_view_compute_modes(qapp):
    from sbgui.pages.live import LiveView
    live = LiveView(); live.resize(1300, 700); live.show()
    _replay(live, lambda e: e.get("ev") == "tick" and e["test"] == 0 and e["t"] >= 4)
    assert live.mode == "ops_per_s" and live.prep_bar.isHidden()
    h = live.thread_bars.opts["height"]
    assert len(h) > 1 and live.ttable.rowCount() == len(h)
    _replay(live, lambda e: e.get("ev") == "progress" and e["phase"] == "preparing" and e["done"] >= e["total"] * 0.45)
    assert not live.prep_bar.isHidden() and "Puffer wird angelegt" in live.prep_bar.format()
    _replay(live, lambda e: e.get("ev") == "tick" and e["test"] == 2 and e["t"] >= 5)
    assert live.mode == "gbps" and live.prep_bar.isHidden()
    _replay(live, lambda e: e.get("ev") == "tick" and e["test"] == 5 and e["t"] >= 3)
    assert live.mode == "latency_ns" and "1 Thread" in live.bar_hint.textItem.toPlainText()
    live.close()


def test_results_page_compute(qapp):
    from sbgui.pages.results import ResultsPage
    files = [demo("result-m2-numa.json"), demo("result-m2-host.json"), demo("result-oltp.json")]
    rp = ResultsPage(files); rp.resize(1300, 700); rp.show()
    rp.select_file(demo("result-m2-host.json"))
    assert rp.cpu_card.isVisible() and rp.mem_card.isVisible()
    assert rp.cpu_tab.rowCount() == 2 and rp.mem_tab.rowCount() == 4
    rp.select_file(demo("result-m2-numa.json"))
    srcs = {rp.numa.item(r, 4).text() for r in range(rp.numa.rowCount())}
    assert srcs == {"ACPI SRAT"}
    rp.select_file(demo("result-oltp.json"))
    assert not rp.cpu_card.isVisible()
    rp.close()


def test_profile_editor_compute(qapp, tmp_path):
    from sbgui.pages.profiles import ProfileEditor
    info = json.load(open(demo("info.json"), encoding="utf-8"))
    pe = ProfileEditor([os.path.join(GUI, "profiles"), str(tmp_path)], numa_nodes=info["meta"]["numa_nodes"])
    pe._load_list()
    assert pe.open_quick("memory") and pe.is_compute()
    t = pe.to_job()["tests"][pe.ti]
    assert t["type"] == "memory" and t["mode"] == "bandwidth"
    pe.cf["mode"].setCurrentIndex(1)            # latency: op/threads/simd not emitted
    t = pe.to_job()["tests"][pe.ti]
    assert t["mode"] == "latency" and "op" not in t and "threads" not in t and not pe.cf["op"].isEnabled()
    pe.cf["mem_node"].setCurrentIndex(pe.cf["mem_node"].findData(1))
    assert pe.to_job()["tests"][pe.ti]["mem_node"] == 1
    n = len(pe.job["tests"])
    pe.add_test("cpu", 2)
    job = pe.to_job()
    assert len(job["tests"]) == n + 1 and job["tests"][-1]["workload"] == "float"
    assert any(x.get("mode") == "latency" and x.get("mem_node") == 1 for x in job["tests"])     # edits kept on test switch
    pe.cf["buffer_size"].setEditText("1G")      # invalid -> kept verbatim, schema reports it
    pe.remove_test(); assert len(pe.job["tests"]) == n
    assert not _doc_errs(pe.to_job())
    pe.write_job(); pe.validate()


def test_contract_version_12():
    v = sb_contract.validators()
    assert "sb-job/1" in v
    sch = json.load(open(os.path.join(sb_contract.base_dir(), "sb-job-1.schema.json"), encoding="utf-8"))
    assert "test_memory" in json.dumps(sch) or "memory" in json.dumps(sch)


def test_report_html_compute():
    from sbgui.report import build_html
    runs = [json.load(open(demo(f), encoding="utf-8")) for f in ("result-m2-numa.json", "result-oltp.json")]
    h = build_html(runs)
    assert "CPU-Test" in h and "RAM-Test" in h and "ACPI SRAT" in h and "IOPS" in h
    assert "RAM-Test" in build_html(runs, compact=True)
