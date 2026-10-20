"""Exit-code mapping (docs/cli.md 1.1) and the mock core's behaviour per code: 0 ok, 2 invalid, 3 aborted, 4 privileges."""
import json, os, subprocess, sys, time


from conftest import GUI, demo, local_job, profile
from sbgui import sbench

MOCK = os.path.join(GUI, "mock_sbench.py")


def mock(*args, timeout=60):
    return subprocess.run([sys.executable, MOCK, *args], capture_output=True, text=True, timeout=timeout, encoding="utf-8")


def test_exit_text_mapping():
    assert sbench.EXIT_TEXT == {0: "ok", 1: "Fehler", 2: "ungültiger Job", 3: "abgebrochen", 4: "fehlende Rechte"}


def test_run_args():
    a = sbench.run_args("j.json", "o.json", "rid", 250, True, "stdout")
    assert a[:2] == ["run", "--job"] and a[a.index("--progress") + 1] == "stdout" and a[-1] == "--allow-destructive"
    assert "--allow-destructive" not in sbench.run_args("j", "o", "r", 250, False, "stdout")


def test_sbench_command_falls_back_to_mock(mock_env):
    cmd = sbench.sbench_command()
    assert cmd[-1].endswith("mock_sbench.py") and os.path.isfile(cmd[-1])


def test_sbench_command_env(monkeypatch):
    monkeypatch.setenv("SBENCH_EXE", r"C:\x\sbench.exe")
    assert sbench.sbench_command() == [r"C:\x\sbench.exe"]


def test_new_run_id_format():
    import re
    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{4}", sbench.new_run_id())


def test_version_and_info(mock_env):
    from sb_contract import validators
    p = mock("version"); assert p.returncode == 0 and p.stdout.startswith("sbench ")
    p = mock("info", "--json"); assert p.returncode == 0
    info = json.loads(p.stdout)
    assert not list(validators()["sb-info/1"].iter_errors(info))
    assert info["meta"]["data_dir"] == os.environ["SBENCH_DATA_DIR"]


def test_validate_ok_and_invalid(mock_env):
    ok = mock("validate", "--job", local_job(profile("four-corners.json"), mock_env))
    assert ok.returncode == 0 and json.loads(ok.stdout)["errors"] == []
    bad = mock("validate", "--job", local_job(profile("oltp-mixed.json"), mock_env))
    assert bad.returncode == 2
    assert "E_DESTRUCTIVE_NOT_ALLOWED" in {e["code"] for e in json.loads(bad.stdout)["errors"]}


def test_bad_args_exit_2(mock_env):
    assert mock("run", "--job", demo("job-oltp.json")).returncode == 2           # --out missing
    assert mock("run", "--job", demo("job-oltp.json"), "--out", str(mock_env / "r.json"), "--tick-ms", "10").returncode == 2


def test_destructive_without_flag_exit_2(mock_env):
    p = mock("run", "--job", local_job(demo("job-oltp.json"), mock_env), "--out", str(mock_env / "r.json"), "--progress", "stdout")
    assert p.returncode == 2, p.stdout[-800:]
    evs = [json.loads(ln) for ln in p.stdout.splitlines() if ln.strip()]
    assert any(e.get("code") == "E_DESTRUCTIVE_NOT_ALLOWED" for e in evs if e["ev"] == "error")


def test_raw_disk_without_admin_exit_4(mock_env):
    p = mock("run", "--job", local_job(demo("job-oltp.json"), mock_env), "--out", str(mock_env / "r.json"), "--progress", "stdout", "--allow-destructive")
    assert p.returncode == 4, p.stdout[-800:]


def test_run_ok_exit_0_result_valid(mock_env):
    from sb_contract import validators
    out = mock_env / "r.json"
    p = mock("run", "--job", local_job(profile("four-corners.json"), mock_env, tests=1, warmup_s=0, duration_s=2), "--out", str(out), "--progress", "stdout", "--tick-ms", "100")
    assert p.returncode == 0, p.stdout[-800:] + p.stderr
    res = json.loads(out.read_text(encoding="utf-8"))
    assert not list(validators()["sb-result/1"].iter_errors(res)) and res["aborted"] is False


def test_stop_exit_3_partial_result(mock_env, monkeypatch):
    monkeypatch.setenv("SBENCH_MOCK_SPEED", "1")
    jp = local_job(profile("four-corners.json"), mock_env, tests=1, warmup_s=0, duration_s=30)
    out = mock_env / "r.json"
    p = subprocess.Popen([sys.executable, MOCK, "run", "--job", jp, "--out", str(out), "--id", "stop-test", "--progress", "stdout", "--tick-ms", "100"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
    try:
        t0 = time.time()
        for ln in p.stdout:                                           # wait for the first tick
            if '"ev":"tick"' in ln.replace(" ", "") or time.time() - t0 > 20:
                break
        assert mock("stop", "--id", "stop-test").returncode == 0
        assert p.wait(30) == 3
    finally:
        if p.poll() is None:
            p.kill()
    assert json.loads(out.read_text(encoding="utf-8"))["aborted"] is True
