"""M2 / contracts 1.2 + 1.3: cpu + memory tests in mock, model helpers, live view, results, compare, profile editor;
1.3: tick.metric/score, phase.threads/cpus, partial/stopped_in with null summary values (every consumer null-safe)."""
import json, os, subprocess, sys, time

import pytest

import sb_contract
from conftest import GUI, demo, profile
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

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


def test_mock_run_compute_13(mock_env):
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
    assert not any(e["ev"] == "progress" and e["phase"] == "preparing" and e["test"] == 0 for e in evs)  # cpu: nothing to prepare
    ticks = [e for e in evs if e["ev"] == "tick"]
    assert {e["metric"] for e in ticks} == {"ops_per_s", "gbps", "latency_ns"}                     # 1.3: explicit metric
    assert {model.tick_metric(e) for e in ticks} == {"ops_per_s", "gbps", "latency_ns"}
    assert all(len(e["workers"]) == 4 and [w["id"] for w in e["workers"]] == [0, 1, 2, 3] for e in ticks if e["metric"] != "latency_ns")
    assert not any("workers" in e for e in ticks if e["metric"] == "latency_ns")
    assert all(e["score"] > 0 for e in ticks if e["metric"] == "ops_per_s")
    first = {}
    for e in evs:
        if e["ev"] == "phase":
            first.setdefault(e["test"], e)
    assert [first[i]["threads"] for i in range(3)] == [4, 4, 1]
    assert [c["cpu"] for c in first[0]["cpus"]] == [0, 64, 2, 66] and first[0]["cpus"][1]["group"] == 1
    prep = [e for e in evs if e["ev"] == "progress" and e["phase"] == "preparing" and e["test"] == 1]
    assert prep[-1]["done"] == prep[-1]["total"] == 4 * 256 * 1024**2                 # threads x actual buffer size (copy too)
    assert res["tick_ms"] == 250 and len(cpu["timeseries"]) == 8                       # one point per tick
    assert cpu["summary"]["score_reference"] == res["meta"]["cpu_score"]["ref_ops_per_s"]["int"]
    assert all(t["partial"] is False and "stopped_in" not in t for t in res["tests"])
    assert all(t["summary"]["proc_cpu_pct"] is not None for t in res["tests"])
    # flat CSV like core export.c: 18 columns, CRLF, total + worker rows
    csvp = mock_env / "r.csv"
    assert mock("export", str(out), "--csv", str(csvp)).returncode == 0
    raw = csvp.read_bytes()
    assert b"\r\n" in raw
    cols, rows = model.read_flat_csv(str(csvp))
    assert cols == model.FLAT_COLUMNS_13 and len(cols) == 20
    tot = [r for r in rows if r["scope"] == "total"]
    assert [r["type"] for r in tot] == ["cpu", "memory", "memory"]
    assert tot[0]["score"] and tot[1]["gbps"] and tot[2]["latency_ns"]
    assert all(r["partial"] is False and r["stopped_in"] is None for r in rows)
    wk = [r for r in rows if r["scope"] == "worker"]
    assert wk and all(r["score"] is None and r["errors"] is None for r in wk)        # 1.3: empty = no value
    assert all(r["errors"] is None for r in tot)                                       # cpu/memory have no errors column value


def test_mock_contract_12_compat(mock_env):
    """SBENCH_MOCK_CONTRACT=1.2: no 1.3 fields, 18 CSV columns, stop before measure -> test absent."""
    jp = write_job(mock_env, [{"type": "cpu", "threads": 2, "workload": "int", "warmup_s": 0, "duration_s": 1}])
    out, prog = mock_env / "r.json", mock_env / "p.jsonl"
    p = mock("run", "--job", jp, "--out", str(out), "--progress", f"file:{prog}", env={"SBENCH_MOCK_CONTRACT": "1.2"})
    assert p.returncode == 0, p.stderr
    res = json.loads(out.read_text(encoding="utf-8"))
    assert not _doc_errs(res) and "tick_ms" not in res and "cpu_score" not in res["meta"]
    assert "partial" not in res["tests"][0] and "score_reference" not in res["tests"][0]["summary"]
    evs = [json.loads(line) for line in prog.read_text(encoding="utf-8").splitlines()]
    assert not any("metric" in e or "threads" in e for e in evs)
    csvp = mock_env / "r.csv"
    assert mock("export", str(out), "--csv", str(csvp), env={"SBENCH_MOCK_CONTRACT": "1.2"}).returncode == 0
    cols, rows = model.read_flat_csv(str(csvp))
    assert cols == model.FLAT_COLUMNS_12 and rows[0]["partial"] is None


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


# ------------------------------------------------------------------ contract 1.3
EX = os.path.join(sb_contract.base_dir(), "examples")


def _partial_storage(tmp):
    """Storage test stopped in warmup by the mock (1.3): present, partial, every measured summary value null."""
    jp = write_job(tmp, [{"type": "storage", "name": "4K Teil", "targets": [{"kind": "file", "path": str(tmp / "t.dat"), "file_size": "64MiB"}],
                          "access_specs": [{"block_size": "4KiB", "read_pct": 70, "random_pct": 100, "weight": 100}],
                          "workers": 2, "queue_depth": 4, "warmup_s": 5, "duration_s": 5}])
    out = tmp / "rp.json"
    p = mock("run", "--job", jp, "--out", str(out), env={"SBENCH_MOCK_STOP_AT": "0:warmup:2"})
    assert p.returncode == 3, p.stderr
    return str(out)


def test_tick_metric_prefers_field():
    assert model.tick_metric({"metric": "gbps", "gbps": 1.0, "iops": 5}) == "gbps"        # 1.3 field wins
    assert model.tick_metric({"metric": "bogus", "ops_per_s": 1.0}) == "ops_per_s"          # unknown -> fallback guessing


def test_partial_helpers():
    r = json.load(open(os.path.join(EX, "result-partial.json"), encoding="utf-8"))
    done, part = r["tests"][0], r["tests"][1]
    assert not model.is_partial(done, r) and model.has_values(done) and model.partial_text(done, r) == ""
    assert model.is_partial(part, r) and not model.has_values(part)
    assert model.partial_text(part, r) == "abgebrochen in Warmup · keine Messwerte"
    meas = {"type": "cpu", "partial": True, "stopped_in": "measure", "summary": {"ops_per_s": 5.0}}
    assert model.partial_text(meas) == "abgebrochen in Messung · Werte über die gemessene Zeit"
    assert model.score_reference("mixed", r["meta"], part["summary"]) == (380000.0, True)
    assert model.score_reference("int", {"cpu_score": {"ref_ops_per_s": {"int": 1e8}}}) == (1e8, True)
    assert model.score_reference("int") == (model.CPU_REFERENCE["int"], False)           # cores < 1.3: docs fallback
    assert model.estimated_score("int", 1e8, {"cpu_score": {"ref_ops_per_s": {"int": 1e8}}}) == pytest.approx(1000)
    old = {"aborted": True, "tests": [{"type": "cpu", "summary": {"ops_per_s": 1}}]}        # 1.2 result: last test of aborted run
    assert model.is_partial(old["tests"][0], old) and "Teilergebnis" in model.partial_text(old["tests"][0], old)


def test_read_flat_csv_13_empty_vs_zero():
    hdr = ",".join(model.FLAT_COLUMNS_13)
    rows = ["1,memory,total,,,,,,,,,,,,,,,,true,warmup", "0,storage,worker,0,0,0,0,0,,,,,,0,,,,,true,measure"]
    cols, rr = model.read_flat_csv(hdr + "\r\n" + "\r\n".join(rows) + "\r\n")
    assert len(cols) == 20
    assert rr[0]["partial"] is True and rr[0]["stopped_in"] == "warmup" and rr[0]["gbps"] is None
    assert rr[1]["iops"] == 0 and rr[1]["errors"] == 0 and rr[1]["lat_mean_us"] is None and rr[1]["stopped_in"] == "measure"


def test_mock_partial_storage_and_csv(mock_env):
    out = _partial_storage(mock_env)
    res = json.load(open(out, encoding="utf-8"))
    assert not _doc_errs(res), _doc_errs(res)
    t = res["tests"][0]
    assert t["partial"] is True and t["stopped_in"] == "warmup"
    assert t["summary"]["iops"] is None and t["summary"]["lat_us"] is None and t["per_worker"] == [] and t["histogram"] == []
    csvp = mock_env / "rp.csv"
    assert mock("export", out, "--csv", str(csvp)).returncode == 0
    _, rows = model.read_flat_csv(str(csvp))
    assert len(rows) == 1 and rows[0]["iops"] is None and rows[0]["partial"] is True and rows[0]["stopped_in"] == "warmup"
    assert mock("export", out, "--csv", str(mock_env / "rp-iom.csv"), "--style", "iometer").returncode == 0


def test_partial_consumers_null_safe(qapp, mock_env):
    """Devend: report.py / guide.py read summary.lat_us and iops directly -> every consumer must survive null values."""
    from sbgui.report import build_html
    from sbgui.pages.results import ResultsPage
    from sbgui.pages.compare import Compare, compute_rows
    from sbgui.pages.export import ExportPage
    files = [os.path.join(EX, "result-partial.json"), demo("result-m2-partial.json"), _partial_storage(mock_env),
             demo("result-m2-host.json"), demo("result-quad-host.json")]
    runs = [json.load(open(f, encoding="utf-8")) for f in files]
    for r in runs:
        assert not _doc_errs(r), _doc_errs(r)
    h = build_html(runs)
    assert "abgebrochen in Warmup · keine Messwerte" in h and "abgebrochen in Vorbereitung" in h and "–" in h
    build_html(runs, compact=True)
    rp = ResultsPage(files); rp.resize(1300, 700); rp.show()
    for f in files:
        assert rp.select_file(f)
    rp.select_file(files[0])
    assert rp.partial_note.isVisible() and "Warmup" in rp.partial_note.text() and rp.state.text().startswith("abgebrochen")
    assert rp.cpu_tab.item(1, 0).text().endswith("†") and rp.cpu_tab.item(1, 5).text() == "–"
    rp.tsel.setCurrentIndex(1)
    assert "Keine Messwerte" in rp.th_info.text()
    rp.select_file(files[2])
    assert rp.st_tab.item(0, 1).text() == "–"
    rp.select_file(files[3]); assert not rp.partial_note.isVisible() and rp.state.text() == "vollständig"
    rp.close()
    cp = Compare(files); cp.resize(1300, 800); cp.show()
    for metric in range(4):
        cp.metric_box.setCurrentIndex(metric)
        for i in range(cp.list.count()):
            for j in range(cp.list.count()):
                for k in range(cp.list.count()):
                    cp.list.item(k).setCheckState(Qt.Checked if k in (i, j) else Qt.Unchecked)
    rows = compute_rows([runs[1], runs[3]])
    assert any(v[0] is None and "Vorbereitung" in det for _, _, v, det in rows)
    cp.close()
    ep = ExportPage(files, str(mock_env))
    for i in range(ep.list.count()):
        ep.list.item(i).setCheckState(Qt.Checked)
    assert "1000 ms" in ep.ts_cb.text() and "gewollt" in ep.iom_hint.toolTip()
    ep.fmt["CSV Iometer"].setChecked(True)
    assert "CPU/RAM-Test(s) fehlen" in ep.iom_hint.text()


def test_guide_summary_null_safe():
    """guide.py builds its run report with sval (was t['summary']['lat_us']['mean'] -> TypeError on null)."""
    src = open(os.path.join(GUI, "sbgui", "guide.py"), encoding="utf-8").read()
    assert 't["summary"]["lat_us"]' not in src and 't["summary"]["iops"]' not in src
    part = json.load(open(os.path.join(EX, "result-partial.json"), encoding="utf-8"))["tests"][1]
    assert model.sval(part, "summary", "lat_us", "mean") is None and model.sval(part, "summary", "ops_per_s") is None


def test_live_phase_threads_cpus_smt_and_score(qapp):
    from sbgui.pages.live import LiveView
    live = LiveView(); live.resize(1300, 700); live.show()
    # replay up to the first warmup of the 128-thread cpu test: thread count known before any tick (phase.threads)
    _replay(live, lambda e: e.get("ev") == "progress" and e["test"] == 0 and e["phase"] == "warmup")
    assert live.test_threads[0] == 128 and live.k_lat.value.text() == "128"
    assert "alle Threads (128)" in live.subtitle.text()
    _replay(live, lambda e: e.get("ev") == "tick" and e["test"] == 0 and e["t"] >= 4)
    cells = [live.ttable.item(r, 1).text() for r in range(live.ttable.rowCount())]
    assert len(cells) == 128 and cells[0].startswith("0 · N0") and sum("SMT" in c for c in cells) == 64
    assert live.k_mbps.title_lbl.text().lower() == "score"
    assert "vom Kern" in live.k_mbps.sub.text()
    assert "SMT" in live.c_lat.subtitle_label.text()
    live.close()


def test_live_partial_stream_and_estimate_fallback(qapp, tmp_path):
    """progress-partial.jsonl (1.3 example) replays; a 1.2 stream without tick.score falls back to a labelled estimate."""
    from sbgui.pages.live import LiveView
    from sbgui.sbench import Replay
    live = LiveView(); live.resize(1300, 700); live.show()
    job = {"schema": "sb-job/1", "name": "x", "tests": [{"type": "cpu", "threads": 1, "workload": "int", "duration_s": 5},
                                                        {"type": "cpu", "threads": 0, "workload": "mixed", "numa_node": 1, "duration_s": 5}]}
    rep = Replay(os.path.join(EX, "progress-partial.jsonl")); live.attach(rep, job, tick_ms=1000)
    rep.feed_until(lambda e: e.get("ev") == "done"); live.refresh(); live.timer.stop()
    assert live.test_threads == {0: 1, 1: 8} and "alle Threads (8)" in live.subtitle.text()
    # 1.2 core: no tick.metric / tick.score -> key guessing + estimate labelled as such
    evs = [json.loads(line) for line in open(demo("progress-m2.jsonl"), encoding="utf-8") if line.strip()]
    old = tmp_path / "p12.jsonl"
    old.write_text("".join(json.dumps({k: v for k, v in e.items() if k not in ("metric", "score", "threads", "cpus")}) + "\n"
                           for e in evs), encoding="utf-8")
    rep = Replay(str(old)); live.attach(rep, json.load(open(demo("job-m2.json"), encoding="utf-8")), tick_ms=250)
    rep.feed_until(lambda e: e.get("ev") == "tick" and e["test"] == 0 and e["t"] >= 3); live.refresh(); live.timer.stop()
    assert live.mode == "ops_per_s" and "schätzung" in live.k_mbps.title_lbl.text().lower() and "Kern < 1.3" in live.k_mbps.sub.text()
    live.close()


def test_live_storage_worker_lag(qapp):
    from sbgui.pages.live import LiveView
    from PySide6.QtCore import QObject, Signal

    class Src(QObject):
        event = Signal(dict); finished = Signal(int, str)
    live = LiveView(); live.resize(1300, 700); live.show()
    src = Src(); job = {"schema": "sb-job/1", "name": "s", "tests": [{"type": "storage", "targets": [{"kind": "file", "path": "D:\\t.dat"}],
                                                                       "access_specs": [], "workers": 2, "duration_s": 3}]}
    live.attach(src, job, tick_ms=1000)
    src.event.emit({"ev": "phase", "test": 0, "name": "preparing", "threads": 2})
    src.event.emit({"ev": "phase", "test": 0, "name": "measure", "duration_s": 3})
    lat = {"mean": 100, "p50": 90, "p99": 300, "max": 900}
    w = lambda i, n: {"id": i, "iops": n, "mbps": n / 250, **({"lat_us": {"mean": 100, "p99": 300}} if n else {})}   # noqa: E731
    src.event.emit({"ev": "tick", "test": 0, "t": 1.0, "metric": "iops", "iops": 2000, "mbps": 8, "lat_us": lat, "cpu_pct": 5,
                    "workers": [w(0, 1000), w(1, 1000)]})
    src.event.emit({"ev": "tick", "test": 0, "t": 2.0, "metric": "iops", "iops": 1000, "mbps": 4, "lat_us": lat, "cpu_pct": 5,
                    "workers": [w(0, 0), w(1, 1000)]})
    live.refresh(); live.timer.stop()
    assert live.lagging == {0} and "↻" in live.wtable.item(0, 0).text() and "250 ms" in live.wtable.item(0, 0).toolTip()
    assert live.wtable.item(0, 2).text() == "500"                                                # smoothed IOPS
    assert live.wtable.item(0, 4).text() == "–" and live.wtable.item(0, 5).text() == "–"       # no lat_us in this interval: "–", not the last value
    src.event.emit({"ev": "tick", "test": 0, "t": 3.0, "metric": "iops", "iops": 3000, "mbps": 12, "lat_us": lat, "cpu_pct": 5,
                    "workers": [w(0, 2000), w(1, 1000)]})
    live.refresh()
    assert live.lagging == set() and "↻" not in live.wtable.item(0, 0).text()
    live.close()


# ------------------------------------------------------------------ Devend's answers (1.3 follow-up, 1.3.1 preview)
def test_future_metrics_known():
    assert model.tick_metric({"metric": "mbps", "mbps": 900.0}) == "mbps"            # M3 network
    assert model.tick_metric({"metric": "latency_us", "latency_us": 48.0}) == "latency_us"   # M3 ping
    assert model.tick_metric({"metric": "jitter_ms", "jitter_ms": 0.1}) == "jitter_ms"     # unknown but present field
    assert model.METRIC_TXT["latency_us"][1] == "µs" and model.fmt_metric("latency_us", 48.25) == "48,25"


def test_live_future_metric_no_crash(qapp):
    from sbgui.pages.live import LiveView
    from PySide6.QtCore import QObject, Signal

    class Src(QObject):
        event = Signal(dict); finished = Signal(int, str)
    live = LiveView(); live.show(); src = Src()
    live.attach(src, {"schema": "sb-job/1", "name": "n", "tests": [{"type": "net_client", "server": "h", "duration_s": 3}]}, tick_ms=1000)
    src.event.emit({"ev": "phase", "test": 0, "name": "measure", "duration_s": 3})
    src.event.emit({"ev": "tick", "test": 0, "t": 2.0, "metric": "mbps", "mbps": 940.0, "cpu_pct": 3})
    src.event.emit({"ev": "tick", "test": 0, "t": 2.0, "metric": "latency_us", "latency_us": 48.0, "cpu_pct": 3})
    live.refresh(); live.timer.stop()
    assert live.mode in (None, "storage") and live.buf.n == 0 and "mbps=940.0" in live.log.toPlainText()   # log only
    live.close()


def test_worker_score_exact():
    meta = {"cpu_score": {"points": 1000, "ref_ops_per_s": {"int": 2.8e8, "mixed": 3.8e5}}}
    assert model.worker_score(2.8e8, "int", meta) == pytest.approx(1000.0)
    assert model.worker_score(1.9e5, "mixed", meta) == pytest.approx(500.0)
    assert model.worker_score(1.9e5, "mixed", None, {"score_reference": 3.8e5}) == pytest.approx(500.0)
    assert model.worker_score(2.8e8, "int") is None                                  # no core reference: no per-thread estimate


def test_live_per_thread_score(qapp):
    from sbgui.pages.live import LiveView
    info = json.load(open(demo("info.json"), encoding="utf-8"))
    live = LiveView(); live.resize(1300, 700); live.show(); live.cpu_score = info["meta"]["cpu_score"]
    _replay(live, lambda e: e.get("ev") == "tick" and e["test"] == 0 and e["t"] >= 4)
    assert live.ttable.horizontalHeaderItem(3).text() == "Score"
    ops = live.last_workers[0]["ops_per_s"]; ref = info["meta"]["cpu_score"]["ref_ops_per_s"]["mixed"]
    assert live.ttable.item(0, 3).text() == model.fmt_metric("score", ops / ref * 1000)
    _replay(live, lambda e: e.get("ev") == "tick" and e["test"] == 2 and e["t"] >= 3)      # memory: share again
    assert live.ttable.horizontalHeaderItem(3).text() == "Anteil" and live.ttable.item(0, 3).text().endswith("%")
    live.cpu_score = None
    _replay(live, lambda e: e.get("ev") == "tick" and e["test"] == 0 and e["t"] >= 4)      # no core reference: no score column
    assert live.ttable.horizontalHeaderItem(3).text() == "Anteil"
    live.close()


def test_live_latency_gap_nan(qapp):
    """Tick without lat_us (interval without completed I/O): NaN in the buffer, the line breaks (connect finite), no 0."""
    from sbgui.pages.live import LiveView, COL
    from PySide6.QtCore import QObject, Signal
    import numpy as np

    class Src(QObject):
        event = Signal(dict); finished = Signal(int, str)
    live = LiveView(); live.resize(1300, 700); live.show(); src = Src()
    live.attach(src, {"schema": "sb-job/1", "name": "s", "tests": [{"type": "storage", "targets": [{"kind": "file", "path": "D:\\t.dat"}],
                                                                     "access_specs": [], "workers": 1, "duration_s": 4}]}, tick_ms=1000)
    src.event.emit({"ev": "phase", "test": 0, "name": "measure", "duration_s": 4})
    lat = {"mean": 100, "p50": 90, "p99": 300, "max": 900}
    for t, l in ((1.0, lat), (2.0, None), (3.0, lat)):
        ev = {"ev": "tick", "test": 0, "t": t, "metric": "iops", "iops": 1000 if l else 0, "mbps": 4 if l else 0, "cpu_pct": 5,
              "workers": [{"id": 0, "iops": 1000 if l else 0, "mbps": 4 if l else 0, **({"lat_us": {"mean": 100, "p99": 300}} if l else {})}]}
        if l:
            ev["lat_us"] = l
        src.event.emit(ev)
    live.refresh(); live.timer.stop()
    a = live.buf.array()
    assert np.isnan(a[1, COL["lat_mean"]]) and a[0, COL["lat_mean"]] == 100
    _, y = live.cur_mean.getData()
    assert np.isnan(y[1]) and live.cur_mean.opts["connect"] == "finite"
    assert live.k_lat.value.text() == "100"
    live.close()


def test_results_runtime_error_and_targets(qapp):
    from sbgui.pages.results import ResultsPage
    f = demo("result-storage-error.json")
    rp = ResultsPage([f, demo("result-quad-host.json")]); rp.resize(1300, 700); rp.show()
    assert rp.select_file(f)
    assert rp.state.text().startswith("Laufzeitfehler") and rp.err_box.isVisible()
    head = rp.err_box.findChildren(type(rp.partial_note), "errHead")[0]
    assert "E_IO: ReadFile" in head.text() and "t = 3 s" in head.text()
    btn, rest = rp.err_more[0]
    assert "2 weitere Fehler" in btn.text() and not rest.isVisible()
    btn.setChecked(True); assert rest.isVisible() and "E_TIMEOUT" in rest.text() and btn.text().startswith("▾")
    assert rp.tgt_warn.isVisible() and "D:\\sbench\\testfile.dat" in rp.tgt_warn.text() and "keep-files" in rp.tgt_warn.text()
    assert "angelegt · nicht gelöscht" in rp.tgt_info.text()
    rp.select_file(demo("result-quad-host.json"))
    assert not rp.err_box.isVisible() and not rp.tgt_warn.isVisible()
    assert "angelegt · gelöscht" in rp.tgt_info.text()
    rp.close()


def test_error_texts_and_fatal():
    assert model.error_text({"code": "E_ACCESS_DENIED", "msg": "x"}) == "Adminrechte nötig (E_ACCESS_DENIED)"
    assert model.error_text({"code": "E_OUT_OF_MEMORY", "msg": "x"}).startswith("Nicht genug Arbeitsspeicher")
    assert model.error_text({"code": "E_NET_CONNECT", "msg": "Host nicht erreichbar", "worker": 2, "count": 4}) == \
        "E_NET_CONNECT: Host nicht erreichbar · Worker 2 · 4×"
    t = {"errors": [{"code": "E_IO", "msg": "a", "fatal": False}, {"code": "E_TIMEOUT", "msg": "b", "fatal": True}]}
    assert [e["code"] for e in model.fatal_errors(t)] == ["E_TIMEOUT"]                # 1.3.1: fatal flag filters
    assert len(model.fatal_errors({"errors": [{"code": "E_IO", "msg": "a"}, {"code": "E_IO", "msg": "b"}]})) == 2   # 1.3: all


def test_mock_runtime_error_oom(mock_env):
    jp = write_job(mock_env, [{"type": "cpu", "threads": 2, "workload": "int", "warmup_s": 0, "duration_s": 1},
                              {"type": "memory", "mode": "bandwidth", "op": "read", "threads": 4, "buffer_size": "256MiB", "warmup_s": 0, "duration_s": 2},
                              {"type": "cpu", "threads": 1, "workload": "float", "warmup_s": 0, "duration_s": 1}])
    out = mock_env / "r.json"
    p = mock("run", "--job", jp, "--out", str(out), env={"SBENCH_MOCK_FAIL_AT": "1:E_OUT_OF_MEMORY:0"})
    assert p.returncode == 1, p.stderr
    res = json.loads(out.read_text(encoding="utf-8"))
    assert not _doc_errs(res), _doc_errs(res)
    assert res["aborted"] is False and [t["type"] for t in res["tests"]] == ["cpu", "memory"]
    m = res["tests"][1]
    assert m["partial"] and m["stopped_in"] == "preparing" and m["summary"]["gbps"] is None
    assert [e["code"] for e in model.fatal_errors(m)] == ["E_OUT_OF_MEMORY"]


def test_mock_keep_files_and_131(mock_env):
    st = [{"type": "storage", "targets": [{"kind": "file", "path": str(mock_env / "t.dat"), "file_size": "64MiB"}],
           "access_specs": [{"block_size": "4KiB", "read_pct": 100, "random_pct": 100, "weight": 100}], "workers": 2, "warmup_s": 0, "duration_s": 1},
          {"type": "cpu", "threads": 4, "workload": "int", "warmup_s": 0, "duration_s": 1}]
    jp = write_job(mock_env, st)
    for keep, contract in ((True, "1.3"), (False, "1.3.1")):
        out = mock_env / f"r{keep}.json"
        p = mock("run", "--job", jp, "--out", str(out), *(["--keep-files"] if keep else []), env={"SBENCH_MOCK_CONTRACT": contract})
        assert p.returncode == 0, p.stderr
        res = json.loads(out.read_text(encoding="utf-8"))
        tg = res["tests"][0]["targets"][0]
        assert tg["created"] is True and tg["deleted"] is (not keep)
        assert bool(model.leftover_targets(res["tests"][0])) is keep
        cpus = model.test_cpus(res["tests"][1])
        if contract == "1.3":
            assert not _doc_errs(res) and cpus == {}
        else:                                          # 1.3.1 preview: cpus[] per test, mapped by worker id
            assert sorted(cpus) == [0, 1, 2, 3] and cpus[1]["cpu"] == res["tests"][1]["per_worker"][1]["cpu"]


def test_results_smt_from_test_cpus(qapp):
    from sbgui.pages.results import ResultsPage
    f131 = os.path.join(GUI, "demo", "contract-1.3.1", "result-m2-smt.json")
    rp = ResultsPage([demo("result-m2-host.json")]); rp.resize(1300, 700); rp.show()
    rp.select_file(demo("result-m2-host.json"))
    assert rp.thr_smt == set() and "SMT nur live" in rp.th_info.text()               # 1.3: no cpus[] per test
    assert rp.thr_scores and "Score je Thread" in rp.th_info.text()                     # per-thread score from meta.cpu_score
    r = rp.current(); t0 = r["tests"][0]; w0 = t0["per_worker"][0]
    assert rp.thr_scores[w0["id"]] == pytest.approx(w0["ops_per_s"] / t0["summary"]["score_reference"] * 1000)
    assert rp.add_result(f131) and rp.select_file(f131)
    assert len(rp.thr_smt) == 64 and "64 SMT-Geschwister" in rp.th_info.text()
    rp.close()


def test_icf_warnings_german(qapp, mock_env):
    from sbgui import sbench
    from sbgui.overlay import IcfImportResult
    from PySide6.QtWidgets import QWidget, QLabel
    code, res = sbench.import_icf(os.path.join(GUI, "demo", "iometer-example.icf"), str(mock_env / "icf.json"))
    assert code == 0 and res["ok"]
    codes = {w["code"] for w in res["warnings"]}
    assert {"W_ICF_ALIGN", "W_ICF_REPLY", "W_ICF_NO_TARGET"} <= codes and codes <= set(model.WARN_TXT)
    assert len([c for c in model.WARN_TXT if c.startswith("W_ICF_")]) >= 17
    host = QWidget(); host.resize(1200, 800)
    dlg = IcfImportResult(host, "iometer-example.icf", res, json.load(open(mock_env / "icf.json", encoding="utf-8")))
    txt = " ".join(lb.text() for lb in dlg.findChildren(QLabel))
    assert model.WARN_TXT["W_ICF_REPLY"] in txt and "Zeile 16" in txt
    assert model.warning_text({"code": "W_ICF_ZERO_WEIGHT", "msg": "Zeile 3"}) == "Zeile mit 0 % weggelassen – Zeile 3"
    assert model.warning_text({"code": "W_NEW", "msg": "neu"}) == "neu"


def test_check_mock_storage_histogram_required():
    r = json.load(open(os.path.join(EX, "result-storage.json"), encoding="utf-8"))
    assert not _doc_errs(r)
    del r["tests"][0]["histogram"]
    assert any("histogram" in e for e in _doc_errs(r))


# ------------------------------------------------------------------ abort vs. failure via exit code (1.3 / 1.3.1)
ICF_CODES_19 = {"W_ICF_RUN_TIME", "W_ICF_DISK_SIZE", "W_ICF_ALIGN", "W_ICF_REPLY", "W_ICF_CYCLING", "W_ICF_QD_MIXED", "W_ICF_SPEC_MIXED",
                "W_ICF_WORKER_SPLIT", "W_ICF_TARGET_LAYOUT", "W_ICF_MANAGERS", "W_ICF_NETWORK_WORKER", "W_ICF_DATA_PATTERN", "W_ICF_SECTOR",
                "W_ICF_ZERO_WEIGHT", "W_ICF_NO_TARGET", "W_ICF_NO_WORKERS", "W_ICF_NO_ASSIGNMENT", "W_ICF_UNSUPPORTED", "W_ICF_VALIDATE"}


def test_icf_texts_cover_all_19():
    """docs/cli.md (#15) and core agree on 19 W_ICF_* codes; every one has a German text."""
    assert {c for c in model.WARN_TXT if c.startswith("W_ICF_")} == ICF_CODES_19
    assert all(model.WARN_TXT[c].strip() for c in ICF_CODES_19)


def _outcome_run(tmp, env, name):
    st = [{"type": "storage", "name": "4K", "targets": [{"kind": "file", "path": str(tmp / f"{name}.dat"), "file_size": "64MiB"}],
           "access_specs": [{"block_size": "4KiB", "read_pct": 70, "random_pct": 100, "weight": 100}], "workers": 2, "warmup_s": 0, "duration_s": 3},
          {"type": "cpu", "threads": 1, "workload": "int", "warmup_s": 0, "duration_s": 1}]
    jp = write_job(tmp, st, name=name); out = tmp / f"{name}.json"
    p = mock("run", "--job", jp, "--out", str(out), "--tick-ms", "250", "--id", name, env=env)
    res = json.loads(out.read_text(encoding="utf-8"))
    return p.returncode, res


@pytest.mark.parametrize("variant,env,exit_code,aborted,outcome", [
    ("v131", {"SBENCH_MOCK_FAIL_AT": "0:E_IO:3:2"}, 1, False, "failed"),                                    # 1.3.1 / mock default
    ("v13", {"SBENCH_MOCK_FAIL_AT": "0:E_IO:3:2", "SBENCH_MOCK_ABORT_ON_ERROR": "1"}, 1, True, "failed"),   # real 1.3 core
    ("stop", {"SBENCH_MOCK_STOP_AT": "0:measure:3"}, 3, True, "aborted"),
    ("ok", {}, 0, False, "ok"),
])
def test_run_outcome_exit_code_based(qapp, mock_env, variant, env, exit_code, aborted, outcome):
    from sbgui.pages.results import ResultsPage
    from sbgui.report import build_html
    code, res = _outcome_run(mock_env, env, variant)
    assert code == exit_code and res["aborted"] is aborted and not _doc_errs(res)
    idx = model.read_run_index(str(mock_env / "data" / "runs" / "index.jsonl"))
    assert idx[variant] == exit_code
    assert model.run_outcome(res, idx[variant]) == outcome          # exit code
    assert model.run_outcome(res) == outcome                         # heuristic fallback (errors[] beat aborted: true)
    assert model.fallback_exit_code(res) == exit_code
    f = str(mock_env / f"{variant}.json")
    for codes in (idx, None):                                        # with run index and without (heuristic)
        rp = ResultsPage([f], codes); rp.show(); rp.select_file(f)
        want = {"failed": "Laufzeitfehler · Teilergebnis", "aborted": "abgebrochen · Teilergebnis", "ok": "vollständig"}[outcome]
        assert rp.state.text() == want and rp.outcome == outcome
        assert rp.state.toolTip().startswith("Exit-Code") is (codes is not None)
        if outcome != "ok":
            assert "Nachfolgende Tests" in rp.partial_note.text()
        rp.close()
    h = build_html([res], exit_codes=idx)
    head = h.split("<h2>")[1].split("</h2>")[0]
    assert head.endswith({"failed": "(Laufzeitfehler)", "aborted": "(abgebrochen)", "ok": "(Host)"}[outcome]) and "(aborted)" not in h


def test_exit_code_beats_result_flags(qapp):
    """1.3 core on a runtime error: aborted: true -> the index exit code 1 makes it 'Laufzeitfehler'; exit 3 stays 'abgebrochen'
    even when a test carries errors[]."""
    from sbgui.pages.results import ResultsPage
    f = demo("result-m2-partial.json")
    r = json.load(open(f, encoding="utf-8"))
    for code, want in ((1, "Laufzeitfehler · Teilergebnis"), (3, "abgebrochen · Teilergebnis"), (None, "abgebrochen · Teilergebnis")):
        rp = ResultsPage([f], (lambda c=code: {r["run_id"]: c} if c is not None else {})); rp.show(); rp.select_file(f)
        assert rp.state.text() == want, (code, rp.state.text())
        rp.close()
    assert model.run_outcome({"aborted": True, "tests": []}, 2) == "invalid" and model.run_outcome({"tests": []}, 4) == "privileges"


def test_tail_session_exit_code_fallback(qapp, tmp_path):
    """Re-attach without an index.jsonl entry: exit code from the result (1.3: aborted + errors[] -> 1, not 3)."""
    from sbgui.sbench import TailSession
    res = {"aborted": True, "tests": [{"index": 0, "type": "storage", "partial": True, "stopped_in": "measure",
                                       "errors": [{"code": "E_IO", "msg": "x"}], "summary": {}}]}
    rp = tmp_path / "r.json"; rp.write_text(json.dumps(res), encoding="utf-8")
    (tmp_path / "runs").mkdir(); prog = tmp_path / "runs" / "x.jsonl"; prog.write_text("", encoding="utf-8")
    ts = TailSession({"run_id": "x", "progress_file": str(prog), "out": str(rp)})
    ts.done = {"ev": "done", "result": str(rp), "aborted": True}
    assert ts._exit_code() == 1
    res["tests"][0]["errors"] = []; rp.write_text(json.dumps(res), encoding="utf-8")
    assert ts._exit_code() == 3
    (tmp_path / "runs" / "index.jsonl").write_text(json.dumps({"run_id": "x", "exit_code": 1}) + "\n", encoding="utf-8")
    assert ts._exit_code() == 1                                       # index entry wins


# ------------------------------------------------------------------ contract 1.3.1 (PR #17, c4b9d69) fixtures
FIX131 = os.path.join(os.path.dirname(__file__), "fixtures", "contract-1.3.1")


@pytest.fixture
def V131(monkeypatch):
    """Validators for the #17 schema (only while building them; the mock keeps main's schema/examples)."""
    import sb_contract
    with monkeypatch.context() as m:
        m.setenv("SBENCH_SCHEMA_DIR", os.path.join(FIX131, "schema"))
        assert sb_contract.base_dir() == os.path.abspath(os.path.join(FIX131, "schema"))
        return sb_contract.validators()


def _errs(v, doc):
    return [e.message for e in v.iter_errors(doc)]


def test_contract_131_fixtures_valid(V131):
    """The #17 examples are valid against the #17 schema; our 1.3.1 demo result too. The announced done.exit_code is NOT
    allowed by the #17 schema yet (done: additionalProperties false) -> contract question, see README."""
    res = json.load(open(os.path.join(FIX131, "result-fatal.json"), encoding="utf-8"))
    assert not _errs(V131["sb-result/1"], res)
    evs = [json.loads(l) for l in open(os.path.join(FIX131, "progress-fatal.jsonl"), encoding="utf-8") if l.strip()]
    assert all(not _errs(V131["sb-progress/1"], e) for e in evs)
    assert "exit_code" not in evs[-1] and _errs(V131["sb-progress/1"], dict(evs[-1], exit_code=1))
    assert not _errs(V131["sb-result/1"], json.load(open(os.path.join(GUI, "demo", "contract-1.3.1", "result-m2-smt.json"), encoding="utf-8")))


def test_result_fatal_fixture_parsing(qapp):
    """result-fatal.json: aborted false, one fatal E_INTERNAL in the last (partial) test -> Laufzeitfehler, exit 1."""
    from sbgui.pages.results import ResultsPage
    from sbgui.report import build_html
    f = os.path.join(FIX131, "result-fatal.json")
    res = json.load(open(f, encoding="utf-8"))
    assert res["aborted"] is False
    assert model.run_outcome(res) == "failed" and model.fallback_exit_code(res) == 1
    assert model.run_outcome(res, 1) == "failed"
    last = res["tests"][-1]
    fe = model.fatal_errors(last)
    assert len(fe) == 1 and fe[0]["code"] == "E_INTERNAL" and model.error_text(fe[0]).startswith("E_INTERNAL: Thread 3")
    cp = model.test_cpus(last)
    assert len(cp) == 8 and cp[0]["cpu"] == 64 and all(c["numa_node"] == 1 for c in cp.values())
    assert len(model.test_cpus(res["tests"][0])) == 1
    for codes in ({res["run_id"]: 1}, None):
        rp = ResultsPage([f], codes); rp.show(); rp.select_file(f)
        assert rp.outcome == "failed" and rp.state.text() == "Laufzeitfehler · Teilergebnis"
        assert not rp.err_box.isHidden()
        txt = " ".join(w.text() for w in rp.err_box.findChildren(QLabel))
        assert "E_INTERNAL" in txt and "Thread 3" in txt
        rp.close()
    head = build_html([res]).split("<h2>")[1].split("</h2>")[0]
    assert head.endswith("(Laufzeitfehler)")


def test_progress_fatal_fixture_replay(qapp):
    """progress-fatal.jsonl through the live view: phase threads/cpus, ticks with score, the fatal error event, done."""
    from sbgui.pages.live import LiveView
    from sbgui.sbench import Replay
    job = {"schema": "sb-job/1", "name": "fatal", "tests": [
        {"type": "cpu", "threads": 1, "workload": "int", "warmup_s": 1, "duration_s": 5},
        {"type": "cpu", "threads": 8, "workload": "mixed", "duration_s": 5, "numa_node": 1},
        {"type": "cpu", "threads": 8, "workload": "int", "duration_s": 5},
        {"type": "cpu", "threads": 8, "workload": "float", "duration_s": 5}]}
    live = LiveView(); live.resize(1300, 700); live.show()
    got = []; live.result_ready.connect(got.append)
    rep = Replay(os.path.join(FIX131, "progress-fatal.jsonl")); live.attach(rep, job, tick_ms=1000)
    rep.feed_until(lambda e: e.get("ev") == "tick" and e["t"] >= 5); live.refresh()
    assert live.mode == "ops_per_s" and live.k_mbps.title_lbl.text().lower() == "score"
    rep.feed_until(lambda e: e.get("ev") == "error"); live.refresh()
    assert live.test_threads[1] == 8
    assert live.run_chip.objectName() == "chipErr" and "E_INTERNAL" in live.run_chip.text() + live.run_chip.toolTip()
    rep.feed(); live.refresh(); live.timer.stop()
    assert got and got[0].endswith("20261020-152000-a7b8.json")
    live.close()


def test_mock_131_one_fatal_and_done_exit_code(mock_env, V131):
    """Mock in 1.3.1 mode: exit 1, aborted false, exactly one fatal: true (first entry of the last, partial test),
    done.exit_code = 1. Valid against the #17 schema except done.exit_code (not in #17 yet)."""
    st = [{"type": "cpu", "threads": 1, "workload": "int", "warmup_s": 0, "duration_s": 1},
          {"type": "cpu", "threads": 2, "workload": "int", "warmup_s": 0, "duration_s": 3},
          {"type": "cpu", "threads": 1, "workload": "float", "warmup_s": 0, "duration_s": 1}]
    jp = write_job(mock_env, st, name="f131"); out = mock_env / "f131.json"; prog = mock_env / "f131.jsonl"
    p = mock("run", "--job", jp, "--out", str(out), "--progress", f"file:{prog}", "--tick-ms", "250",
             env={"SBENCH_MOCK_CONTRACT": "1.3.1", "SBENCH_MOCK_FAIL_AT": "1:E_INTERNAL:2:3"})
    res = json.loads(out.read_text(encoding="utf-8"))
    assert p.returncode == 1 and res["aborted"] is False
    assert not _errs(V131["sb-result/1"], res)
    last = res["tests"][-1]
    assert last["index"] == 1 and last["partial"] is True
    fat = [(t["index"], e) for t in res["tests"] for e in (t.get("errors") or []) if e.get("fatal")]
    assert len(fat) == 1 and fat[0][0] == 1 and last["errors"][0]["fatal"] is True
    assert all("fatal" not in e for e in last["errors"][1:])
    evs = [json.loads(l) for l in prog.read_text(encoding="utf-8").splitlines() if l.strip()]
    done = evs[-1]
    assert done["ev"] == "done" and done["exit_code"] == 1 and done["aborted"] is False
    assert all(not _errs(V131["sb-progress/1"], e) for e in evs[:-1])
    assert not _errs(V131["sb-progress/1"], {k: v for k, v in done.items() if k != "exit_code"})
    assert model.run_outcome(res, done["exit_code"]) == "failed" and model.fallback_exit_code(res) == 1


def test_tail_session_exit_code_order(qapp, tmp_path):
    """index.jsonl exit_code > done.exit_code > fallback from the result (fatal -> 1, 1.3 errors in partial -> 1, aborted -> 3)."""
    from sbgui.sbench import TailSession
    res = json.load(open(os.path.join(FIX131, "result-fatal.json"), encoding="utf-8"))
    rp = tmp_path / "r.json"; rp.write_text(json.dumps(res), encoding="utf-8")
    (tmp_path / "runs").mkdir(); prog = tmp_path / "runs" / "x.jsonl"; prog.write_text("", encoding="utf-8")
    ts = TailSession({"run_id": "x", "progress_file": str(prog), "out": str(rp)})
    ts.done = {"ev": "done", "result": str(rp), "aborted": False}
    assert ts._exit_code() == 1                                       # fallback: fatal entry
    ts.done["exit_code"] = 4
    assert ts._exit_code() == 4                                       # done.exit_code beats the result
    (tmp_path / "runs" / "index.jsonl").write_text(json.dumps({"run_id": "x", "exit_code": 3}) + "\n", encoding="utf-8")
    assert ts._exit_code() == 3                                       # index entry beats done.exit_code
    # fallback variants without index/done.exit_code
    (tmp_path / "runs" / "index.jsonl").unlink(); del ts.done["exit_code"]
    for r, want in (({"aborted": True, "tests": [{"partial": True, "errors": [{"code": "E_IO", "msg": "x"}]}]}, 1),   # 1.3 core
                    ({"aborted": True, "tests": [{"partial": True, "errors": []}]}, 3),
                    ({"aborted": False, "tests": [{"partial": False, "errors": [{"code": "E_IO", "msg": "x"}]}]}, 0),
                    ({"aborted": True, "tests": [{"partial": True, "errors": [{"code": "E_IO", "msg": "x"}]},
                                                 {"partial": True, "errors": [{"code": "E_IO", "msg": "y", "fatal": True}]}]}, 1)):
        rp.write_text(json.dumps(r), encoding="utf-8")
        assert ts._exit_code() == want, r
