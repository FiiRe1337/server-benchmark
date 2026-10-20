"""sbgui.core: data_dir selection, copy + SHA-256 verification (mismatch refuses), --data-dir on every sbench call,
version resolution / build info, frozen resolution (never _MEIPASS, never the mock)."""
import hashlib, json, os, sys, types

import pytest

from conftest import GUI
from sbgui import core, sbench

sys.path.insert(0, os.path.join(GUI, "tools"))
import gen_build_info  # noqa: E402


def sha(b):
    return hashlib.sha256(b).hexdigest()


# ------------------------------------------------------------------ data_dir
def test_data_dir_portable_when_writable(tmp_path):
    exe_dir = tmp_path / "Tools"; exe_dir.mkdir()
    assert core.select_data_dir(str(exe_dir), env={"ProgramData": str(tmp_path / "pd")}) == str(exe_dir / "data")
    assert (exe_dir / "data").is_dir()


def test_data_dir_programdata_when_not_writable(tmp_path):
    exe_dir = str(tmp_path / "ro")
    env = {"ProgramData": str(tmp_path / "pd")}
    got = core.select_data_dir(exe_dir, env=env, writable=lambda d: not d.startswith(exe_dir))
    assert got == os.path.join(str(tmp_path / "pd"), "sbench")


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="POSIX permissions, non-root")
def test_data_dir_real_readonly_dir(tmp_path):
    ro = tmp_path / "ro"; ro.mkdir(); ro.chmod(0o555)
    try:
        assert core.select_data_dir(str(ro), env={"ProgramData": str(tmp_path / "pd")}) == str(tmp_path / "pd" / "sbench")
    finally:
        ro.chmod(0o755)


def test_data_dir_none_writable_raises(tmp_path):
    with pytest.raises(core.CoreError):
        core.select_data_dir(str(tmp_path), env={"ProgramData": str(tmp_path)}, writable=lambda d: False)


def test_program_data_default():
    assert core.program_data_dir({}) == os.path.join(r"C:\ProgramData", "sbench")


# ------------------------------------------------------------------ install + verify
@pytest.fixture
def embedded(tmp_path):
    p = tmp_path / "mei" / "core" / "sbench.exe"; p.parent.mkdir(parents=True)
    p.write_bytes(b"MZ real core v1"); return p


def test_install_copies_and_verifies(tmp_path, embedded):
    dd = tmp_path / "data"
    t = core.install_core(str(embedded), str(dd), "1.2.3", sha(embedded.read_bytes()))
    assert t == str(dd / "bin" / "sbench-1.2.3.exe") and open(t, "rb").read() == embedded.read_bytes()
    assert not [f for f in os.listdir(dd / "bin") if f.endswith(".tmp")]


def test_install_keeps_good_copy_and_replaces_tampered(tmp_path, embedded):
    dd = tmp_path / "data"; h = sha(embedded.read_bytes())
    t = core.install_core(str(embedded), str(dd), "1.2.3", h)
    os.utime(t, (1, 1))
    core.install_core(str(embedded), str(dd), "1.2.3", h)
    assert os.stat(t).st_mtime == 1                      # unchanged, not re-copied
    open(t, "wb").write(b"evil")
    core.install_core(str(embedded), str(dd), "1.2.3", h)
    assert sha(open(t, "rb").read()) == h


def test_install_refuses_bad_embedded_or_missing_hash(tmp_path, embedded):
    with pytest.raises(core.CoreIntegrityError):
        core.install_core(str(embedded), str(tmp_path / "d"), "1", "0" * 64)
    with pytest.raises(core.CoreIntegrityError):
        core.install_core(str(embedded), str(tmp_path / "d"), "1", None)
    with pytest.raises(core.CoreError):
        core.install_core(str(tmp_path / "nope.exe"), str(tmp_path / "d"), "1", "0" * 64)


def test_target_name_sanitized(tmp_path):
    assert core.core_target("D", "0.1.0-ci.7+abc/../x").endswith("sbench-0.1.0-ci.7_abc_.._x.exe")


def test_verify_mismatch_refuses_and_repairs(tmp_path, embedded):
    h = sha(embedded.read_bytes()); dd = str(tmp_path / "data")
    t = core.install_core(str(embedded), dd, "1.2.3", h)
    c = core.Core([t], dd, "embedded", h, str(embedded), "1.2.3")
    assert c.command(["info", "--json"]) == [t, "--data-dir", dd, "info", "--json"]
    c.release()                                           # (Windows: drop the share-read lock so we can tamper)
    open(t, "wb").write(b"tampered")
    with pytest.raises(core.CoreIntegrityError, match="Start verweigert"):
        c.command(["run", "--job", "j"])
    assert sha(open(t, "rb").read()) == h                # restored from the embedded copy
    assert c.command(["version", "--json"])[-2:] == ["version", "--json"]
    c.release()


def test_verify_without_embedded_copy_refuses(tmp_path):
    p = tmp_path / "sbench.exe"; p.write_bytes(b"x")
    c = core.Core([str(p)], str(tmp_path), "env", "f" * 64)
    with pytest.raises(core.CoreIntegrityError):
        c.verify()


# ------------------------------------------------------------------ frozen resolution
def _fake_frozen(monkeypatch, tmp_path, embedded, expected):
    exe_dir = tmp_path / "Tools"; exe_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "sbench-gui.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(embedded.parent.parent), raising=False)
    monkeypatch.setattr(core, "build_info", lambda: {"version": "0.2.0", "commit": "c", "core_sha256": expected,
                                                      "core_version": "0.2.0-core", "built_utc": None})
    return exe_dir


def test_frozen_uses_verified_copy_never_meipass_or_mock(monkeypatch, tmp_path, embedded):
    exe_dir = _fake_frozen(monkeypatch, tmp_path, embedded, sha(embedded.read_bytes()))
    monkeypatch.setenv("SBENCH_EXE", "/should/be/ignored")
    c = core.resolve()
    assert c.mode == "embedded" and c.data_dir == str(exe_dir / "data")
    assert c.base_cmd == [str(exe_dir / "data" / "bin" / "sbench-0.2.0-core.exe")]
    assert not core.is_within(c.base_cmd[0], sys._MEIPASS)
    assert "mock_sbench.py" not in " ".join(c.command([], verify=False))
    c.release()


def test_frozen_refuses_corrupt_embedded(monkeypatch, tmp_path, embedded):
    _fake_frozen(monkeypatch, tmp_path, embedded, "a" * 64)
    with pytest.raises(core.CoreIntegrityError):
        core.resolve()


# ------------------------------------------------------------------ --data-dir on every call
class Recorder(core.Core):
    def __init__(self, base, dd):
        super().__init__(base, dd, "mock"); self.calls = []

    def command(self, args, verify=True):
        cmd = super().command(args, verify); self.calls.append(cmd); return cmd


@pytest.fixture
def rec(mock_env, monkeypatch):
    dd = str(mock_env / "explicit-dd")
    r = Recorder([sys.executable, os.path.join(GUI, "mock_sbench.py")], dd)
    monkeypatch.setattr(core, "_CORE", r)
    monkeypatch.setenv("SBENCH_DATA_DIR", str(mock_env / "env-dd-must-not-win"))   # flag must beat the env var
    return r


def _assert_dd(r, n=None):
    assert r.calls and (n is None or len(r.calls) == n)
    for c in r.calls:
        assert c[2:4] == ["--data-dir", r.data_dir], c


def test_data_dir_sync_calls(rec, mock_env):
    from conftest import local_job, profile
    assert sbench.info()["meta"]["data_dir"] == rec.data_dir
    sbench.validate(local_job(profile("four-corners.json"), mock_env))
    sbench.list_running()
    assert sbench.kern_version() == "0.1.0-mock"
    v = sbench.version_json(); assert len(v["sha256"]) == 64
    sbench.import_icf(os.path.join(GUI, "demo", "iometer-example.icf"), str(mock_env / "icf.json"))
    _assert_dd(rec)
    assert not (mock_env / "env-dd-must-not-win").exists()


class FakeQProcess:
    started = []

    def __init__(self, *a, **k):
        pass

    def start(self, prog, args):
        FakeQProcess.started.append([prog, *args])

    @staticmethod
    def startDetached(prog, args):
        FakeQProcess.started.append([prog, *args]); return True


def test_data_dir_async_builders(rec, qapp, monkeypatch):
    FakeQProcess.started = []
    s = sbench.RunSession("j.json", "o.json", "rid")
    monkeypatch.setattr(sbench, "QProcess", FakeQProcess)
    sbench.bundle("/tmp/x.zip")
    sbench.stop_run("rid")
    monkeypatch.setattr(s.proc, "start", lambda prog, args: FakeQProcess.started.append([prog, *args]), raising=False)
    s.start()
    _assert_dd(rec, 3)
    assert FakeQProcess.started == rec.calls
    assert "--all" in rec.calls[0]


def test_data_dir_elevated_runas(rec, qapp, monkeypatch):
    got = {}

    class Pipe:
        def __init__(self, *a):
            self.data = types.SimpleNamespace(connect=lambda f: None)

        def start(self):
            pass

        def close(self):
            pass

    class Runas:
        def __init__(self, cmd, parent=None):
            got["cmd"] = cmd; self.exited = types.SimpleNamespace(connect=lambda f: None)

        def start(self):
            pass

    import sbgui
    fake = types.SimpleNamespace(PipeServer=Pipe, RunasProcess=Runas)
    monkeypatch.setitem(sys.modules, "sbgui.pipe_win", fake)
    monkeypatch.setattr(sbgui, "pipe_win", fake, raising=False)
    s = sbench.ElevatedRunSession("j.json", "o.json", "rid")
    monkeypatch.setattr(sbench.os, "name", "nt")
    try:
        s.start()
    finally:
        monkeypatch.undo()
    assert got["cmd"][2:4] == ["--data-dir", rec.data_dir] and "pipe:\\\\.\\pipe\\sbench-rid" in got["cmd"]


def test_run_refused_on_mismatch(tmp_path, qapp, monkeypatch):
    p = tmp_path / "sbench.exe"; p.write_bytes(b"tampered")
    monkeypatch.setattr(core, "_CORE", core.Core([str(p)], str(tmp_path), "env", "0" * 64))
    s = sbench.RunSession("j.json", "o.json", "rid")
    started, fin = [], []
    monkeypatch.setattr(s.proc, "start", lambda *a: started.append(a), raising=False)
    s.finished.connect(lambda c, t: fin.append((c, t)))
    s.start()
    qapp.processEvents()
    assert not started and fin and fin[0][0] == 1
    with pytest.raises(core.CoreIntegrityError):
        sbench.run_sync(["info", "--json"])


def test_mock_data_dir_flag_positions(mock_env):
    import subprocess
    m = os.path.join(GUI, "mock_sbench.py")
    for argv in (["--data-dir", str(mock_env / "a"), "info", "--json"], ["info", "--json", "--data-dir", str(mock_env / "b")]):
        out = json.loads(subprocess.run([sys.executable, m, *argv], capture_output=True, text=True, check=True).stdout)
        assert out["meta"]["data_dir"] == argv[1] if argv[0] == "--data-dir" else argv[-1]
    bad = mock_env / "file"; bad.write_text("x")
    assert subprocess.run([sys.executable, m, "--data-dir", str(bad / "sub"), "info", "--json"], capture_output=True).returncode == 2


# ------------------------------------------------------------------ versions / build info
def test_resolve_version_order():
    rv = gen_build_info.resolve_version
    assert rv("1.4.0", env={"GITHUB_REF": "refs/tags/v9"}, describe="v8") == "1.4.0"
    assert rv("", env={"GITHUB_REF": "refs/tags/v0.3.0"}, describe="v8") == "0.3.0"
    assert rv(None, env={"GITHUB_REF": "refs/heads/main"}, describe="v0.1.0-beta.1-3-gabc1234") == "0.1.0-beta.1-3-gabc1234"
    assert rv("  ", env={"GITHUB_RUN_NUMBER": "42"}, describe="", short_sha="abc1234") == "0.1.0-ci.42+abc1234"
    assert rv(None, env={}, describe="", short_sha="abc1234") == "0.1.0-dev+abc1234"


def test_build_info_generated_module(tmp_path, monkeypatch):
    core_exe = tmp_path / "sbench.exe"; core_exe.write_bytes(b"MZ core")
    out = tmp_path / "_build_info.py"
    gen_build_info.main(["--core", str(core_exe), "--version", "0.5.0-ci.3+abc", "--commit", "deadbeef",
                         "--out", str(out), "--stage", str(tmp_path / "stage" / "sbench.exe")])
    mod = types.ModuleType("sbgui._build_info"); exec(out.read_text(encoding="utf-8"), mod.__dict__)
    import sbgui
    monkeypatch.setitem(sys.modules, "sbgui._build_info", mod)
    monkeypatch.setattr(sbgui, "_build_info", mod, raising=False)
    bi = core.build_info()
    assert bi["version"] == "0.5.0-ci.3+abc" and bi["commit"] == "deadbeef" and bi["core_sha256"] == sha(b"MZ core")
    assert bi["core_version"] == "0.5.0-ci.3+abc" and core.gui_version() == "0.5.0-ci.3+abc"
    assert (tmp_path / "stage" / "sbench.exe").read_bytes() == b"MZ core"


def test_build_info_dev_default():
    if "sbgui._build_info" in sys.modules or os.path.exists(os.path.join(GUI, "sbgui", "_build_info.py")):
        pytest.skip("generated build info present")
    assert core.build_info()["version"] == core.DEV_VERSION and core.build_info()["core_sha256"] is None


def test_selftest_job_uses_real_temp_path(tmp_path):
    from sbgui.app import selftest_job
    from sb_contract import validators
    j = selftest_job(str(tmp_path))
    assert j["tests"][0]["targets"][0]["path"] == os.path.join(str(tmp_path), "sbench-selftest.dat")
    assert "%" not in json.dumps(j)
    assert not list(validators()["sb-job/1"].iter_errors(j))
