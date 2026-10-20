#!/usr/bin/env python3
"""Regenerate demo/ (replay + result files for screenshots) with the mock sbench. Deterministic (fixed run ids/seeds).
Paths that would leak the build machine are rewritten to plausible Windows paths."""
import json, os, shutil, subprocess, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(ROOT, "demo")
FAKE_DATA = "C:\\Tools\\sbench\\data"


def mock(args, env):
    return subprocess.run([sys.executable, os.path.join(ROOT, "mock_sbench.py"), *args], env=env, capture_output=True, text=True)


def fix(path, tmp):
    txt = open(path, encoding="utf-8").read().replace(json.dumps(tmp)[1:-1], json.dumps(FAKE_DATA)[1:-1]).replace(tmp, FAKE_DATA)
    open(path, "w", encoding="utf-8", newline="\n").write(txt)


def main():
    tmp = tempfile.mkdtemp(prefix="sbdemo-")
    base = dict(os.environ, SBENCH_DATA_DIR=tmp, SBENCH_MOCK_SPEED="400")
    # info (host, not elevated -> dashboard shows the admin hint)
    open(os.path.join(DEMO, "info.json"), "w", encoding="utf-8").write(mock(["info", "--json"], base).stdout); fix(os.path.join(DEMO, "info.json"), tmp)
    # OLTP mix incl. raw disk: elevated run with double opt-in; also the replay stream for the live view
    job = json.load(open(os.path.join(ROOT, "profiles", "oltp-mixed.json"), encoding="utf-8"))
    for t in job["tests"]:
        for tg in t["targets"]:
            if tg["kind"] in ("volume", "physical"):
                tg["allow_destructive"] = True
    jp = os.path.join(DEMO, "job-oltp.json"); json.dump(job, open(jp, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    out = os.path.join(DEMO, "result-oltp.json")
    cp = mock(["run", "--job", jp, "--out", out, "--progress", "stdout", "--tick-ms", "250", "--id", "20261020-091500-demo",
               "--allow-destructive"], dict(base, SBENCH_MOCK_ELEVATED="1"))
    assert cp.returncode == 0, cp.stderr
    open(os.path.join(DEMO, "progress.jsonl"), "w", encoding="utf-8", newline="\n").write(cp.stdout.replace(ROOT + "/demo/", "C:\\\\Tools\\\\sbench\\\\results\\\\"))
    fix(out, tmp)
    # Four Corners on host / VM / VM on SAN (comparison)
    for kind, rid in (("host", "20261020-101200-a1b2"), ("vm", "20261020-104500-c3d4"), ("vm-san", "20261020-111800-e5f6")):
        o = os.path.join(DEMO, f"result-quad-{kind}.json")
        cp = mock(["run", "--job", os.path.join(ROOT, "profiles", "four-corners.json"), "--out", o, "--id", rid],
                  dict(base, SBENCH_MOCK_HOST=kind, SBENCH_MOCK_ELEVATED="0"))
        assert cp.returncode == 0, cp.stderr
        fix(o, tmp)
    # contract 1.2/1.3 (M2): CPU + RAM on host and VM (comparison), NUMA local/remote on the 2-socket host, replay stream
    jp = os.path.join(ROOT, "profiles", "cpu-ram.json")
    for kind, rid in (("host", "20261020-131500-m2h1"), ("vm", "20261020-134000-m2v1")):
        o = os.path.join(DEMO, f"result-m2-{kind}.json")
        args = ["run", "--job", jp, "--out", o, "--id", rid] + (["--progress", "stdout", "--tick-ms", "250"] if kind == "host" else [])
        cp = mock(args, dict(base, SBENCH_MOCK_HOST=kind))
        assert cp.returncode == 0, cp.stderr
        fix(o, tmp)
        if kind == "host":
            json.dump(json.load(open(jp, encoding="utf-8")), open(os.path.join(DEMO, "job-m2.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
            open(os.path.join(DEMO, "progress-m2.jsonl"), "w", encoding="utf-8", newline="\n").write(
                cp.stdout.replace(json.dumps(o)[1:-1], "C:\\\\Tools\\\\sbench\\\\results\\\\result-m2-host.json"))
    o = os.path.join(DEMO, "result-m2-numa.json")
    cp = mock(["run", "--job", os.path.join(ROOT, "profiles", "numa-lokal-remote.json"), "--out", o, "--id", "20261020-140500-m2n1"], base)
    assert cp.returncode == 0, cp.stderr
    fix(o, tmp)
    # contract 1.3: partial result (stop while RAM test 4 allocates its buffers -> present with partial/stopped_in, null values;
    # later tests absent). Deterministic via SBENCH_MOCK_STOP_AT instead of a timed `sbench stop`.
    o = os.path.join(DEMO, "result-m2-partial.json")
    cp = mock(["run", "--job", jp, "--out", o, "--id", "20261020-143000-m2p1"], dict(base, SBENCH_MOCK_STOP_AT="3:preparing:2"))
    assert cp.returncode == 3, (cp.returncode, cp.stderr)
    fix(o, tmp)
    shutil.rmtree(tmp)
    print("demo/ regenerated")


if __name__ == "__main__":
    main()
