#!/usr/bin/env python3
"""Smoke-Tests für sbench.exe auf echtem Windows (CI-Runner oder Testrechner).

Aufruf:  python tests/smoke/smoke.py <pfad-zu-sbench.exe> [--quick]

Prüft gegen die JSON-Schemas in schema/ (jsonschema):
  * version, info --json (sb-info/1), validate (sb-validate/1, Exit 0/2)
  * Storage-Lauf: 256 MiB Temp-Datei, 4K random 70/30, QD 8, 2 Worker, 5 s
    -> Exit 0, Ergebnis sb-result/1 + semantische Regeln, Fortschritt sb-progress/1
  * Fortschritt über Named Pipe (GUI-Rolle: Python legt die Pipe als Server an)
  * Stop: Lauf starten, nach 2 s 'sbench stop' -> Exit 3, aborted = true
  * nicht implementierte Testtypen -> error-Event E_NOT_IMPLEMENTED
  * export (flat/iometer), bundle (ZIP, manifest gegen sb-bundle/1, sha256,
    --all/--results/--include/--anonymize)
  * Vertrag 1.1: runs/<id>.jsonl, Lock-Datei, runs/index.jsonl, list --running --json,
    progress-Events, allow_destructive, E_PIPE_CONNECT, Pipe-Abbruch
  * mit Adminrechten: nur lesender Test auf \\\\.\\C: (volume)
Gibt am Ende eine Zusammenfassung mit IOPS, MB/s und p99 aus.
"""
from __future__ import annotations

import csv
import ctypes
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "schema"
sys.path.insert(0, str(ROOT / "tools"))

from jsonschema import Draft202012Validator, FormatChecker  # noqa: E402

try:
    from referencing import Registry, Resource  # jsonschema >= 4.18

    def make_validator(name: str, schemas: dict) -> Draft202012Validator:
        reg = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas.values())
        return Draft202012Validator(schemas[name], registry=reg, format_checker=FormatChecker())
except ImportError:  # ältere jsonschema-Versionen (z. B. ARM64 ohne rpds-Wheel)
    from jsonschema import RefResolver

    def make_validator(name: str, schemas: dict) -> Draft202012Validator:
        store = {s["$id"]: s for s in schemas.values()}
        res = RefResolver.from_schema(schemas[name], store=store)
        return Draft202012Validator(schemas[name], resolver=res, format_checker=FormatChecker())

try:
    from validate_examples import semantic_checks
except Exception:  # noqa: BLE001
    semantic_checks = None

SCHEMAS = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in SCHEMA_DIR.glob("*.schema.json")}
V = {k: make_validator(k, SCHEMAS) for k in SCHEMAS}

FAILS: list[str] = []
SUMMARY: list[str] = []


def check(cond: bool, msg: str) -> bool:
    print(("  [OK]   " if cond else "  [FAIL] ") + msg, flush=True)
    if not cond:
        FAILS.append(msg)
    return cond


def validate(doc, schema: str, what: str) -> bool:
    errs = sorted(V[schema].iter_errors(doc), key=lambda e: list(e.absolute_path))
    for e in errs[:15]:
        print(f"         {what}: /{'/'.join(map(str, e.absolute_path))}: {e.message[:300]}")
    return check(not errs, f"{what} gültig gegen {schema}")


def run(exe, *args, timeout=180, **kw):
    cmd = [str(exe), *map(str, args)]
    print("$ " + subprocess.list2cmdline(cmd), flush=True)
    p = subprocess.run(cmd, capture_output=True, timeout=timeout, **kw)
    if p.stderr:
        print("  stderr: " + p.stderr.decode("utf-8", "replace").strip()[:2000])
    return p


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def check_progress(events: list[dict], what: str, expect_aborted: bool | None = None) -> None:
    bad = 0
    for i, ev in enumerate(events):
        errs = list(V["sb-progress-1.schema.json"].iter_errors(ev))
        if errs:
            bad += 1
            if bad <= 5:
                print(f"         Zeile {i + 1}: {errs[0].message[:200]}")
    check(bad == 0, f"{what}: alle {len(events)} Events gültig gegen sb-progress-1")
    evs = [e["ev"] for e in events]
    check(bool(evs) and evs[0] == "start", f"{what}: erstes Event ist start")
    check(bool(evs) and evs[-1] == "done" and evs.count("done") == 1, f"{what}: letztes Event ist done (genau einmal)")
    if expect_aborted is not None and evs and evs[-1] == "done":
        check(events[-1]["aborted"] is expect_aborted, f"{what}: done.aborted = {expect_aborted}")
    # Ticks nur in measure
    phase = None
    tick_ok = True
    for e in events:
        if e["ev"] == "phase":
            phase = e["name"]
        if e["ev"] == "tick" and phase != "measure":
            tick_ok = False
    check(tick_ok, f"{what}: tick-Events nur in Phase measure")
    # progress-Events nur in preparing/warmup und passend zur aktuellen Phase
    phase = None
    prog_ok = True
    for e in events:
        if e["ev"] == "phase":
            phase = e["name"]
        if e["ev"] == "progress" and (e["phase"] != phase or e["done"] > e["total"] + 1e-9):
            prog_ok = False
    check(prog_ok, f"{what}: progress-Events passen zur Phase, done <= total")


def check_result(path: Path, what: str, expect_aborted: bool, job: Path | None = None) -> dict | None:
    if not check(path.exists(), f"{what}: Ergebnisdatei {path.name} existiert"):
        return None
    doc = json.loads(path.read_text(encoding="utf-8"))
    validate(doc, "sb-result-1.schema.json", what)
    if semantic_checks:
        jdoc = json.loads(job.read_text(encoding="utf-8")) if job and job.exists() else None
        extra = semantic_checks("sb-result-1.schema.json", doc, jdoc)
        for m in extra:
            print("         " + m)
        check(not extra, f"{what}: semantische Regeln (Summen = io_count, index steigend{' < Tests im Job' if jdoc else ''}, NUMA disjunkt)")
    check(doc["aborted"] is expect_aborted, f"{what}: aborted = {expect_aborted}")
    return doc


def storage_job(path: Path, name: str, target: Path, duration: float, warmup: float = 1, extra_tests=None,
                read_pct=70, workers=2, qd=8, size="256MiB") -> Path:
    job = {
        "schema": "sb-job/1",
        "name": name,
        "tests": [
            {
                "type": "storage",
                "name": name,
                "targets": [{"path": str(target), "kind": "file", "file_size": size}],
                "workers": workers,
                "queue_depth": qd,
                "warmup_s": warmup,
                "duration_s": duration,
                "cache": "unbuffered",
                "access_specs": [{"name": "4K random", "weight": 100, "block_size": "4KiB", "read_pct": read_pct,
                                  "random_pct": 100}],
            }
        ]
        + (extra_tests or []),
    }
    path.write_text(json.dumps(job, indent=2), encoding="utf-8")
    return path


# ---- Named Pipe (Server-Seite wie die GUI) ---------------------------------------
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateNamedPipeW.restype = wintypes.HANDLE
k32.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
k32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
k32.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
k32.CloseHandle.argtypes = [wintypes.HANDLE]
INVALID_HANDLE = wintypes.HANDLE(-1).value


def run_restricted(exe, *args, cwd: Path, timeout=120):
    """Startet exe mit eingeschränktem Token (Administratoren nur 'deny only', LUA_TOKEN,
    Integrität Medium) wie aus einer nicht erhöhten Konsole. Rückgabe (exit_code, stdout,
    stderr) oder None, wenn der Runner das nicht zulässt (dann wird der Test übersprungen)."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    adv = ctypes.WinDLL("advapi32", use_last_error=True)

    class SA(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class SI(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                    ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                    ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                    ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD), ("lpReserved2", ctypes.c_void_p),
                    ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]

    class PI(ctypes.Structure):
        _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                    ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]

    k32.GetCurrentProcess.restype = wintypes.HANDLE
    tok, rtok = wintypes.HANDLE(), wintypes.HANDLE()
    # TOKEN_ASSIGN_PRIMARY|DUPLICATE|QUERY|ADJUST_DEFAULT|ADJUST_SESSIONID
    if not adv.OpenProcessToken(k32.GetCurrentProcess(), 0x1 | 0x2 | 0x8 | 0x80 | 0x100, ctypes.byref(tok)):
        print(f"  OpenProcessToken: Fehler {ctypes.get_last_error()}")
        return None
    # DISABLE_MAX_PRIVILEGE | LUA_TOKEN
    if not adv.CreateRestrictedToken(tok, 0x1 | 0x4, 0, None, 0, None, 0, None, ctypes.byref(rtok)):
        print(f"  CreateRestrictedToken: Fehler {ctypes.get_last_error()}")
        return None
    sid = ctypes.c_void_p()
    if adv.ConvertStringSidToSidW("S-1-16-8192", ctypes.byref(sid)):  # Medium Mandatory Level
        tml = SA(sid, 0x20)  # SE_GROUP_INTEGRITY
        adv.SetTokenInformation(rtok, 25, ctypes.byref(tml), ctypes.sizeof(tml) + adv.GetLengthSid(sid))
    outp, errp = cwd / "restricted.out", cwd / "restricted.err"
    with open(outp, "wb") as fo, open(errp, "wb") as fe:
        import msvcrt
        ho, he = msvcrt.get_osfhandle(fo.fileno()), msvcrt.get_osfhandle(fe.fileno())
        os.set_handle_inheritable(ho, True)
        os.set_handle_inheritable(he, True)
        si = SI()
        si.cb = ctypes.sizeof(si)
        si.dwFlags = 0x100  # STARTF_USESTDHANDLES
        si.hStdOutput, si.hStdError = ho, he
        pi = PI()
        cmd = ctypes.create_unicode_buffer(subprocess.list2cmdline([str(exe), *map(str, args)]))
        print("$ [eingeschränkt] " + cmd.value, flush=True)
        ok = adv.CreateProcessAsUserW(rtok, None, cmd, None, None, True, 0x08000000, None, str(cwd),
                                      ctypes.byref(si), ctypes.byref(pi))  # CREATE_NO_WINDOW
        if not ok:
            print(f"  CreateProcessAsUserW: Fehler {ctypes.get_last_error()}")
            return None
        k32.WaitForSingleObject(pi.hProcess, timeout * 1000)
        code = wintypes.DWORD()
        k32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code))
        k32.CloseHandle(pi.hThread)
        k32.CloseHandle(pi.hProcess)
    k32.CloseHandle(rtok)
    k32.CloseHandle(tok)
    err = errp.read_bytes()
    if err:
        print("  stderr: " + err.decode("utf-8", "replace").strip()[:2000])
    return code.value, outp.read_bytes(), err


def pipe_reader(name: str, out: list, ready: threading.Event, close_after: int = 0):
    h = k32.CreateNamedPipeW(name, 0x00000001, 0, 1, 65536, 65536, 0, None)  # PIPE_ACCESS_INBOUND, Byte-Modus
    if h == INVALID_HANDLE or h is None:
        out.append(f"CreateNamedPipe fehlgeschlagen: {ctypes.get_last_error()}")
        ready.set()
        return
    ready.set()
    k32.ConnectNamedPipe(h, None)
    buf = ctypes.create_string_buffer(65536)
    data = b""
    n = wintypes.DWORD()
    while k32.ReadFile(h, buf, 65536, ctypes.byref(n), None) and n.value:
        data += buf.raw[: n.value]
        if close_after and data.count(b"\n") >= close_after:
            break   # GUI "stürzt ab": Pipe schließen, sbench muss weiterlaufen
    k32.CloseHandle(h)
    out.append(data)


def main() -> int:
    exe = Path(sys.argv[1]).resolve()
    quick = "--quick" in sys.argv
    work = Path(tempfile.mkdtemp(prefix="sbench-smoke-"))
    # portable Kopie: Datenverzeichnis = <exe-dir>\data (Vertrag 1.1, Regel 2)
    os.environ.pop("SBENCH_DATA_DIR", None)
    bindir = work / "bin"
    bindir.mkdir()
    shutil.copy2(exe, bindir / "sbench.exe")
    sbench = bindir / "sbench.exe"
    data_dir = bindir / "data"
    runs_dir = data_dir / "runs"
    testfile = work / "sbtest.dat"
    print(f"Arbeitsverzeichnis: {work}")

    print("\n== version ==")
    p = run(sbench, "version")
    lines = p.stdout.decode().splitlines()
    check(p.returncode == 0 and lines[0].startswith("sbench "), f"version: {lines[:1]}")
    check(all(f in lines for f in ("sb-job/1", "sb-result/1", "sb-progress/1", "sb-info/1", "sb-validate/1", "sb-runs/1")),
          "version listet Formatversionen inkl. sb-runs/1")
    kern_version = lines[0].split()[1]

    print("\n== version --json ==")
    p = run(sbench, "version", "--json")
    vj = json.loads(p.stdout.decode("utf-8")) if p.returncode == 0 else {}
    exe_hash = hashlib.sha256(sbench.read_bytes()).hexdigest()
    exp_arch = "arm64" if os.environ.get("PROCESSOR_ARCHITECTURE", "").upper() == "ARM64" else "x64"
    check(p.returncode == 0 and vj.get("version") == kern_version and vj.get("sha256") == exe_hash
          and vj.get("arch") == exp_arch and isinstance(vj.get("commit"), str) and "sb-runs/1" in vj.get("formats", []),
          f"version --json: {vj}")
    SUMMARY.append(f"version --json: version={vj.get('version')} commit={str(vj.get('commit'))[:12]} arch={vj.get('arch')}")

    print("\n== --data-dir ==")
    dd1 = work / "dd-option"
    os.environ["SBENCH_DATA_DIR"] = str(work / "dd-env")
    p = run(sbench, "--data-dir", dd1, "info", "--json")
    m1 = json.loads(p.stdout.decode("utf-8"))["meta"] if p.returncode == 0 else {}
    check(str(m1.get("data_dir", "")).lower() == str(dd1).lower() and (dd1 / "runs").is_dir(),
          f"--data-dir vor SBENCH_DATA_DIR: meta.data_dir = {m1.get('data_dir')}")
    p = run(sbench, "info", "--json")
    m2 = json.loads(p.stdout.decode("utf-8"))["meta"] if p.returncode == 0 else {}
    check(str(m2.get("data_dir", "")).lower() == str(work / "dd-env").lower(), f"SBENCH_DATA_DIR ohne Option ({m2.get('data_dir')})")
    os.environ.pop("SBENCH_DATA_DIR", None)
    p = run(sbench, "info", "--json", "--data-dir", "dd-rel", cwd=work)
    m3 = json.loads(p.stdout.decode("utf-8"))["meta"] if p.returncode == 0 else {}
    check(str(m3.get("data_dir", "")).lower() == str(work / "dd-rel").lower(),
          f"--data-dir nach dem Befehl, relativer Pfad ({m3.get('data_dir')})")
    blocker = work / "keine-datei-als-ordner"
    blocker.write_text("x", encoding="utf-8")
    p = run(sbench, "--data-dir", blocker / "sub", "info")
    check(p.returncode == 2 and b"E_ARGS" in p.stderr, "--data-dir nicht anlegbar: Exit 2, E_ARGS")
    p = run(sbench, "--data-dir")
    check(p.returncode == 2, "--data-dir ohne Pfad: Exit 2")

    print("\n== info --json ==")
    t0 = time.time()
    p = run(sbench, "info", "--json")
    check(p.returncode == 0, f"info Exit 0 ({time.time() - t0:.1f} s)")
    info = json.loads(p.stdout.decode("utf-8"))
    validate(info, "sb-info-1.schema.json", "info --json")
    m = info["meta"]
    check(str(m.get("data_dir", "")).lower() == str(data_dir).lower(), f"meta.data_dir = <exe>\\data ({m.get('data_dir')})")
    check(isinstance(m.get("elevated"), bool) and isinstance(m.get("ram_free_bytes"), int) and m.get("numa_nodes"),
          f"meta: elevated={m.get('elevated')}, ram_free_bytes, numa_nodes={len(m.get('numa_nodes') or [])}")
    allc = [c for n in m.get("numa_nodes") or [] for c in n["cpus"]]
    check(len(allc) == len(set(allc)) and all(isinstance(c, int) for c in allc),
          f"numa_nodes[].cpus: eindeutige globale Indizes (Gruppe*64+Bit) {allc[:8]}")
    ncpu = len(allc)
    check(ncpu == m["cpu"]["threads"], f"NUMA-CPUs ({ncpu}) = logische Prozessoren ({m['cpu']['threads']})")
    hv = info["meta"]["hypervisor"]
    print(f"  OS: {info['meta']['os']}, CPU: {info['meta']['cpu']['model']} ({info['meta']['cpu']['threads']} Threads)")
    print(f"  Hypervisor: present={hv['present']} vendor={hv['vendor']} role={hv['role']}")
    print(f"  partition_info: {json.dumps(hv.get('partition_info'), ensure_ascii=False)}")
    print(f"  Volumes: {[v['path'] for v in info['targets']['volumes']]}, Disks: {len(info['targets']['physical_disks'])}, NICs: {len(info['targets']['nics'])}")
    SUMMARY.append(f"info: {info['meta']['os']['name']} {info['meta']['os']['build']}, Hypervisor role={hv['role']} "
                   f"(Quelle {hv.get('partition_info', {}).get('role_source')})")

    print("\n== validate ==")
    good = storage_job(work / "job-smoke.json", "Smoke 4K random 70/30", testfile, 5)
    p = run(sbench, "validate", "--job", good)
    doc = json.loads(p.stdout.decode("utf-8"))
    validate(doc, "sb-validate-1.schema.json", "validate (gültig)")
    check(p.returncode == 0 and doc["ok"] is True, "validate gültiger Job: Exit 0, ok = true")
    check(isinstance(doc.get("warnings"), list), f"validate: warnings immer vorhanden ({doc.get('warnings')})")
    bad = json.loads(good.read_text(encoding="utf-8"))
    bad["tests"][0]["access_specs"] = [
        {"weight": 60, "block_size": "4KiB", "read_pct": 100, "random_pct": 100},
        {"weight": 30, "block_size": 1000, "read_pct": 100, "random_pct": 100},
    ]
    bad["tests"][0]["bogus"] = 1
    badp = work / "job-bad.json"
    badp.write_text(json.dumps(bad), encoding="utf-8")
    p = run(sbench, "validate", "--job", badp)
    doc = json.loads(p.stdout.decode("utf-8"))
    validate(doc, "sb-validate-1.schema.json", "validate (ungültig)")
    codes = {(e["path"], e["code"]) for e in doc["errors"]}
    check(p.returncode == 2 and doc["ok"] is False, "validate ungültiger Job: Exit 2, ok = false")
    check(("/tests/0/access_specs", "E_WEIGHT_SUM") in codes, "E_WEIGHT_SUM mit JSON Pointer")
    check(("/tests/0/access_specs/1/block_size", "E_BLOCK_ALIGN") in codes, "E_BLOCK_ALIGN mit JSON Pointer")
    check(("/tests/0/bogus", "E_SCHEMA") in codes, "E_SCHEMA für unbekanntes Feld")
    p = run(sbench, "run", "--job", badp, "--out", work / "bad-result.json", "--progress", "stdout")
    evs = [json.loads(l) for l in p.stdout.decode("utf-8").splitlines() if l.strip()]
    check(p.returncode == 2 and not (work / "bad-result.json").exists(), "run mit ungültigem Job: Exit 2, kein Ergebnis")
    check(any(e.get("ev") == "error" and e.get("field") == "/tests/0/access_specs" for e in evs), "run: error-Event mit field")
    # Schreibschutz für Raw-Ziele (es wird nie geschrieben: validate bzw. run ohne --allow-destructive)
    raw = {"schema": "sb-job/1", "name": "raw write", "tests": [{"type": "storage",
           "targets": [{"path": "\\\\.\\C:", "kind": "volume"}], "duration_s": 1,
           "access_specs": [{"weight": 100, "block_size": "4KiB", "read_pct": 70, "random_pct": 100}]}]}
    rawp = work / "job-raw.json"
    rawp.write_text(json.dumps(raw), encoding="utf-8")
    p = run(sbench, "validate", "--job", rawp)
    doc = json.loads(p.stdout.decode("utf-8"))
    validate(doc, "sb-validate-1.schema.json", "validate (raw ohne allow_destructive)")
    check(p.returncode == 2 and any(e["code"] == "E_DESTRUCTIVE_NOT_ALLOWED" and e["path"] == "/tests/0/targets/0/allow_destructive"
                                    for e in doc["errors"]),
          "Schreibtest auf volume ohne allow_destructive: E_DESTRUCTIVE_NOT_ALLOWED, Exit 2")
    raw["tests"][0]["targets"][0]["allow_destructive"] = True
    rawp.write_text(json.dumps(raw), encoding="utf-8")
    p = run(sbench, "validate", "--job", rawp)
    doc = json.loads(p.stdout.decode("utf-8"))
    validate(doc, "sb-validate-1.schema.json", "validate (raw mit allow_destructive)")
    check(p.returncode == 0 and doc["ok"] and any(w["code"] == "W_DESTRUCTIVE" for w in doc["warnings"]),
          "allow_destructive: true -> validate ok mit W_DESTRUCTIVE")
    p = run(sbench, "run", "--job", rawp, "--out", work / "raw-result.json", "--id", "raw-denied")
    check(p.returncode == 2 and b"E_DESTRUCTIVE_NOT_ALLOWED" in p.stderr and not (work / "raw-result.json").exists(),
          "run ohne --allow-destructive: Exit 2, E_DESTRUCTIVE_NOT_ALLOWED, nichts geschrieben")

    print("\n== run: Storage-Smoke (256 MiB, 4K random 70/30, QD 8, 2 Worker, 5 s) ==")
    prog = work / "progress.jsonl"
    out = work / "result-smoke.json"
    t0 = time.time()
    p = run(sbench, "run", "--job", good, "--out", out, "--progress", f"file:{prog}", "--id", "smoke-run",
            "--tick-ms", "1000", timeout=600)
    check(p.returncode == 0, f"run Exit 0 (ist {p.returncode}, {time.time() - t0:.1f} s)")
    events = read_jsonl(prog)
    check_progress(events, "Fortschritt (file:)", expect_aborted=False)
    ticks = [e for e in events if e["ev"] == "tick"]
    check(len(ticks) >= 4, f"mindestens 4 Ticks bei 5 s / 1000 ms (ist {len(ticks)})")
    progs = [e for e in events if e["ev"] == "progress"]
    prep = [e for e in progs if e["phase"] == "preparing"]
    warm = [e for e in progs if e["phase"] == "warmup"]
    check(bool(prep) and prep[-1]["unit"] == "bytes" and prep[-1]["done"] == prep[-1]["total"] == 256 * 2**20,
          f"progress preparing in Bytes bis 256 MiB ({len(prep)} Events)")
    check(bool(warm) and warm[-1]["unit"] == "s" and abs(warm[-1]["done"] - 1) < 0.05, f"progress warmup in s ({len(warm)} Events)")
    ph = {e["name"]: e.get("duration_s") for e in events if e["ev"] == "phase"}
    check(ph.get("warmup") == 1 and ph.get("measure") == 5, f"phase.duration_s für warmup/measure ({ph})")
    check(all({"read_iops", "write_iops", "read_mbps", "write_mbps", "proc_cpu_pct"} <= set(t) for t in ticks),
          "Ticks mit read/write-Aufteilung und proc_cpu_pct")
    check(all(abs(t["read_iops"] + t["write_iops"] - t["iops"]) <= 0.2 for t in ticks), "Tick: read_iops + write_iops = iops")
    check(all("lat_us" in w for t in ticks for w in t["workers"] if w["iops"] > 0), "Tick: workers[].lat_us")
    sm = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {"tests": [{}]}
    tg = sm["tests"][0].get("targets", [])
    check(tg == [{"index": 0, "path": str(testfile), "kind": "file", "created": True, "deleted": True}] and not testfile.exists(),
          f"#5: angelegte Testdatei nach dem Lauf gelöscht, targets[] = {tg}")
    rj = runs_dir / "smoke-run.jsonl"
    check(rj.exists() and read_jsonl(rj) == events, "runs\\smoke-run.jsonl = Events des --progress-Ziels")
    check(not (runs_dir / "smoke-run.lock.json").exists(), "Lock-Datei nach Laufende gelöscht")
    idx = read_jsonl(runs_dir / "index.jsonl") if (runs_dir / "index.jsonl").exists() else []
    ent = [e for e in idx if e.get("run_id") == "smoke-run"]
    check(len(ent) == 1 and ent[0]["exit_code"] == 0 and ent[0]["out"].lower() == str(out).lower()
          and set(ent[0]) == {"run_id", "started_utc", "ended_utc", "out", "exit_code"},
          f"runs\\index.jsonl: Eintrag für smoke-run ({ent})")
    res = check_result(out, "Ergebnis Smoke", expect_aborted=False)
    if res:
        t = res["tests"][0]
        s = t["summary"]
        lat = s["lat_us"]
        check(s["io_count"] > 0 and s["errors"] == 0, f"I/Os > 0 ({s['io_count']}), keine Fehler")
        check(res.get("run_id") == "smoke-run", f"Ergebnis run_id = smoke-run ({res.get('run_id')})")
        check(res["meta"].get("elevated") is not None and str(res["meta"].get("data_dir", "")).lower() == str(data_dir).lower(),
              "Ergebnis meta: elevated, data_dir")
        rf = s["read_iops"] / max(s["iops"], 1e-9)
        check(0.6 < rf < 0.8, f"Leseanteil ~70 % (ist {rf * 100:.1f} %)")
        check(len(t["per_worker"]) == 2 and all(w["io_count"] > 0 for w in t["per_worker"]), "2 Worker mit I/Os")
        check(abs(s["mbps"] - s["iops"] * 4096 / 1e6) < 0.01 * s["mbps"] + 0.01, "MB/s = IOPS x 4 KiB / 10^6")
        check(lat["min"] <= lat["p50"] <= lat["p90"] <= lat["p99"] <= lat["p99_9"] <= lat["max"], "Perzentile monoton")
        check(len(t["timeseries"]) >= 4, f"Zeitreihe mit {len(t['timeseries'])} Punkten")
        SUMMARY.append(f"SMOKE-ERGEBNIS 4K random 70/30 QD8 x2 Worker 5 s: IOPS {s['iops']:.0f} "
                       f"(read {s['read_iops']:.0f} / write {s['write_iops']:.0f}), MB/s {s['mbps']:.2f}, "
                       f"Latenz mean {lat['mean']:.1f} us, p50 {lat['p50']:.1f} us, p99 {lat['p99']:.1f} us, "
                       f"p99.9 {lat['p99_9']:.1f} us, max {lat['max']:.1f} us, CPU-Ticks "
                       f"{[x.get('cpu_pct') for x in t['timeseries']]}")
    logf = data_dir / "logs" / "smoke-run.log"
    check(logf.exists() and "Phase measure" in logf.read_text(encoding="utf-8"), f"Lauf-Log {logf}")
    check((data_dir / "jobs" / "smoke-run.json").exists(), "Job-Kopie unter data\\jobs")

    print("\n== run mit --data-dir ==")
    dd4 = work / "dd-run"
    p = run(sbench, "run", "--data-dir", dd4, "--job", storage_job(work / "job-dd.json", "dd", testfile, 1, warmup=0),
            "--id", "dd-run")
    check(p.returncode == 0 and (dd4 / "results" / "dd-run.json").exists() and (dd4 / "logs" / "dd-run.log").exists()
          and (dd4 / "runs" / "dd-run.jsonl").exists() and not (data_dir / "results" / "dd-run.json").exists(),
          "run --data-dir: Ergebnis, Log und Fortschritt im angegebenen Verzeichnis")
    if (dd4 / "results" / "dd-run.json").exists():
        check(json.loads((dd4 / "results" / "dd-run.json").read_text(encoding="utf-8"))["meta"]["data_dir"].lower()
              == str(dd4).lower(), "Ergebnis meta.data_dir = --data-dir")
    p = run(sbench, "--data-dir", dd4, "bundle", "--all", "--out", work / "bundle-dd.zip")
    if check(p.returncode == 0, "bundle mit --data-dir: Exit 0"):
        with zipfile.ZipFile(work / "bundle-dd.zip") as z:
            check("results/dd-run.json" in z.namelist() and "results/smoke-run.json" not in z.namelist(),
                  "bundle --all nutzt das --data-dir-Verzeichnis")

    print("\n== run ohne --out (Standardablage results/<run_id>.json) + Named Pipe ==")
    pipe = r"\\.\pipe\sbench-smoke-pipe"
    got: list = []
    ready = threading.Event()
    th = threading.Thread(target=pipe_reader, args=(pipe, got, ready), daemon=True)
    th.start()
    ready.wait(5)
    short = storage_job(work / "job-short.json", "kurz", testfile, 2, warmup=0)
    p = run(sbench, "run", "--job", short, "--progress", f"pipe:{pipe}", "--id", "pipe-run", "--tick-ms", "500")
    th.join(10)
    check(p.returncode == 0, f"run über Pipe Exit 0 (ist {p.returncode})")
    if got and isinstance(got[0], bytes):
        evs = [json.loads(l) for l in got[0].decode("utf-8").splitlines() if l.strip()]
        check_progress(evs, "Fortschritt (pipe:)", expect_aborted=False)
        done = evs[-1] if evs else {}
        check(done.get("result", "").lower() == str(data_dir / "results" / "pipe-run.json").lower(),
              f"Standardablage {done.get('result')}")
    else:
        check(False, f"Pipe-Daten empfangen ({got})")
    check_result(data_dir / "results" / "pipe-run.json", "Ergebnis (Standardablage)", expect_aborted=False)

    print("\n== Pipe: Verbindungsfehler und Abbruch ==")
    t0 = time.time()
    p = run(sbench, "run", "--job", short, "--progress", r"pipe:\\.\pipe\sbench-gibt-es-nicht", "--id", "pipe-fail", timeout=60)
    dt = time.time() - t0
    check(p.returncode == 1 and b"E_PIPE_CONNECT" in p.stderr and 9 <= dt <= 20,
          f"Pipe nicht verbindbar: Exit 1, E_PIPE_CONNECT auf stderr nach {dt:.1f} s")
    pf = runs_dir / "pipe-fail.jsonl"
    check(pf.exists() and any(e.get("code") == "E_PIPE_CONNECT" for e in read_jsonl(pf)), "E_PIPE_CONNECT in runs\\pipe-fail.jsonl")
    pipe2 = r"\\.\pipe\sbench-smoke-pipe-break"
    got2: list = []
    ready2 = threading.Event()
    th2 = threading.Thread(target=pipe_reader, args=(pipe2, got2, ready2, 2), daemon=True)
    th2.start()
    ready2.wait(5)
    p = run(sbench, "run", "--job", short, "--progress", f"pipe:{pipe2}", "--id", "pipe-break", "--tick-ms", "500")
    th2.join(10)
    blog = data_dir / "logs" / "pipe-break.log"
    check(p.returncode == 0 and (data_dir / "results" / "pipe-break.json").exists(),
          f"Pipe-Abbruch: Lauf läuft weiter, Exit 0 (ist {p.returncode}), Ergebnis geschrieben")
    check(blog.exists() and "Pipe abgebrochen" in blog.read_text(encoding="utf-8"), "Pipe-Abbruch: Warnung im Log")
    bev = read_jsonl(runs_dir / "pipe-break.jsonl") if (runs_dir / "pipe-break.jsonl").exists() else []
    check(bool(bev) and bev[-1].get("ev") == "done", "Pipe-Abbruch: runs\\pipe-break.jsonl vollständig bis done")

    print("\n== stop: Lauf starten, nach 2 s 'sbench stop' ==")
    longj = storage_job(work / "job-long.json", "lang", testfile, 60, warmup=0)
    sprog = work / "progress-stop.jsonl"
    sout = work / "result-stop.json"
    proc = subprocess.Popen([str(sbench), "run", "--job", str(longj), "--out", str(sout), "--progress", f"file:{sprog}",
                             "--id", "stop-test"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # warten, bis die Messphase läuft (Datei existiert bereits -> kein Anlegen)
    deadline = time.time() + 30
    while time.time() < deadline and not (sprog.exists() and '"measure"' in sprog.read_text(encoding="utf-8")):
        time.sleep(0.1)
    time.sleep(2)
    # verwaiste Lock-Datei (Prozess existiert nicht) muss ignoriert und entfernt werden
    stale = runs_dir / "stale-run.lock.json"
    stale.write_text(json.dumps({"run_id": "stale-run", "pid": 4194300, "started_utc": "2026-01-01T00:00:00.000Z",
                                 "job": "x.json", "out": "x-result.json"}), encoding="utf-8")
    lk = runs_dir / "stop-test.lock.json"
    if check(lk.exists(), "Lock-Datei während des Laufs vorhanden"):
        lock = json.loads(lk.read_text(encoding="utf-8"))
        check(set(lock) == {"run_id", "job_name", "pid", "started_utc", "job", "out"} and lock["pid"] == proc.pid,
              f"Lock-Datei-Inhalt {lock}")
    p = run(sbench, "list", "--running", "--json")
    lst = json.loads(p.stdout.decode("utf-8")) if p.returncode == 0 else {}
    validate(lst, "sb-runs-1.schema.json", "list --running --json")
    ids = [r["run_id"] for r in lst.get("runs", [])]
    check(ids == ["stop-test"], f"list --running: nur stop-test ({ids})")
    if ids:
        r0 = lst["runs"][0]
        check(r0["pid"] == proc.pid and Path(r0["progress_file"]).exists(), "list: pid und progress_file stimmen")
        check(r0.get("job_name") == "lang", f"list: job_name aus job.name ({r0.get('job_name')})")
    check(not stale.exists(), "verwaiste Lock-Datei entfernt")
    ts = time.time()
    p = run(sbench, "stop", "--id", "stop-test")
    check(p.returncode == 0, "stop Exit 0")
    try:
        rc = proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        rc = None
    check(rc == 3, f"run nach stop: Exit 3 (ist {rc}, Reaktionszeit {time.time() - ts:.2f} s)")
    check_progress(read_jsonl(sprog), "Fortschritt (stop)", expect_aborted=True)
    sres = check_result(sout, "Teilergebnis (stop)", expect_aborted=True)
    if sres and sres["tests"]:
        s = sres["tests"][0]["summary"]
        check(s["io_count"] > 0, f"Teilergebnis enthält Messwerte ({s['io_count']} I/Os, {s['iops']:.0f} IOPS)")
        tg = sres["tests"][0].get("targets", [])
        check(len(tg) == 1 and tg[0]["created"] and tg[0]["deleted"] and not testfile.exists(),
              f"#5: Testdatei auch nach stop gelöscht ({tg})")
    p = run(sbench, "stop", "--id", "gibt-es-nicht")
    check(p.returncode == 1, "stop ohne laufenden Lauf: Exit 1")
    p = run(sbench, "list", "--running", "--json")
    check(p.returncode == 0 and json.loads(p.stdout.decode("utf-8"))["runs"] == [], "list --running nach Stop leer")

    print("\n== #5: --keep-files, vorhandene Dateien, Aufräumen nach Fehler ==")
    kj = storage_job(work / "job-keep.json", "keep", testfile, 1, warmup=0)
    p = run(sbench, "run", "--job", kj, "--out", work / "result-keep.json", "--keep-files")
    kt = json.loads((work / "result-keep.json").read_text(encoding="utf-8"))["tests"][0].get("targets", []) if p.returncode == 0 else []
    check(p.returncode == 0 and len(kt) == 1 and kt[0]["created"] and not kt[0]["deleted"] and testfile.exists(),
          f"--keep-files: angelegt, nicht gelöscht ({kt})")
    p = run(sbench, "run", "--job", kj, "--out", work / "result-pre.json")
    kt = json.loads((work / "result-pre.json").read_text(encoding="utf-8"))["tests"][0].get("targets", []) if p.returncode == 0 else []
    check(p.returncode == 0 and len(kt) == 1 and not kt[0]["created"] and not kt[0]["deleted"] and testfile.exists(),
          f"vorhandene Datei bleibt unangetastet ({kt})")
    huge = work / "sbtest-zu-gross.dat"
    hj = json.loads(kj.read_text(encoding="utf-8"))
    hj["tests"][0]["targets"][0].update({"path": str(huge), "file_size": 2**50})
    hjp = work / "job-huge.json"
    hjp.write_text(json.dumps(hj), encoding="utf-8")
    p = run(sbench, "run", "--job", hjp, "--out", work / "result-huge.json", "--id", "cleanup-error")
    hl = data_dir / "logs" / "cleanup-error.log"
    check(p.returncode == 1 and not huge.exists() and hl.exists() and "gelöscht (von sbench angelegt)" in hl.read_text(encoding="utf-8"),
          f"Fehler beim Anlegen (1 PiB): Exit 1 (ist {p.returncode}), angelegte Datei gelöscht")

    print("\n== nicht implementierte Testtypen ==")
    ni = storage_job(work / "job-ni.json", "ni", testfile, 1, warmup=0,
                     extra_tests=[{"type": "cpu", "duration_s": 1}, {"type": "memory", "mode": "latency", "buffer_size": "64MiB", "duration_s": 1},
                                  {"type": "net_client", "host": "127.0.0.1", "duration_s": 1}, {"type": "hyperv_info"}])
    # Storage an Position 1 (cpu davor): echte Ergebnisse haben Lücken in tests[].index
    nij = json.loads(ni.read_text(encoding="utf-8"))
    nij["tests"].insert(1, nij["tests"].pop(0))
    ni.write_text(json.dumps(nij), encoding="utf-8")
    nprog = work / "progress-ni.jsonl"
    nout = work / "result-ni.json"
    p = run(sbench, "run", "--job", ni, "--out", nout, "--progress", f"file:{nprog}")
    check(p.returncode == 0, f"Job mit cpu/memory/net/hyperv_info: Exit 0 (ist {p.returncode})")
    nevs = read_jsonl(nprog)
    check_progress(nevs, "Fortschritt (not implemented)", expect_aborted=False)
    nis = [e for e in nevs if e["ev"] == "error" and e["code"] == "E_NOT_IMPLEMENTED"]
    check(sorted(e["test"] for e in nis) == [0, 2, 3, 4], "E_NOT_IMPLEMENTED für Tests 0, 2-4")
    nres = check_result(nout, "Ergebnis (not implemented)", expect_aborted=False, job=ni)
    if nres:
        check([t["index"] for t in nres["tests"]] == [1], "nur der Storage-Test im Ergebnis, index = 1 (Lücke)")

    print("\n== #6: ohne Adminrechte (eingeschränkter Token) ==")
    rdd = work / "dd-restricted"
    rjob = work / "job-nonadmin.json"
    rjob.write_text(json.dumps({"schema": "sb-job/1", "name": "nonadmin", "tests": [{"type": "storage",
                    "targets": [{"path": "\\\\.\\C:", "kind": "volume"}], "duration_s": 1, "warmup_s": 0,
                    "access_specs": [{"weight": 100, "block_size": "4KiB", "read_pct": 100, "random_pct": 100}]}]}),
                    encoding="utf-8")
    rr = run_restricted(sbench, "--data-dir", rdd, "info", "--json", cwd=work)
    if rr is None:
        print("  [SKIP] eingeschränkter Token auf diesem Runner nicht möglich, Exit 4 manuell prüfen (docs/abnahme-m1.md)")
        SUMMARY.append("#6 Exit 4: übersprungen (kein eingeschränkter Token)")
    else:
        el = json.loads(rr[1].decode("utf-8"))["meta"].get("elevated") if rr[0] == 0 else None
        check(el is False, f"eingeschränkter Token: meta.elevated = false (ist {el})")
        rr = run_restricted(sbench, "--data-dir", rdd, "validate", "--job", rjob, cwd=work)
        vd = json.loads(rr[1].decode("utf-8")) if rr and rr[1] else {}
        check(rr is not None and rr[0] == 0 and any(w["code"] == "W_NEEDS_ADMIN" for w in vd.get("warnings", [])),
              f"validate ohne Admin: Exit 0 mit W_NEEDS_ADMIN ({vd.get('warnings')})")
        rr = run_restricted(sbench, "--data-dir", rdd, "run", "--job", rjob, "--out", work / "result-nonadmin.json",
                            "--progress", f"file:{work / 'progress-nonadmin.jsonl'}", "--id", "nonadmin", cwd=work)
        pev = read_jsonl(work / "progress-nonadmin.jsonl") if (work / "progress-nonadmin.jsonl").exists() else []
        check(rr is not None and rr[0] == 4 and not (work / "result-nonadmin.json").exists()
              and any(e.get("ev") == "error" and e.get("code") == "E_ACCESS_DENIED" for e in pev),
              f"run ohne Admin: Exit 4 (ist {rr and rr[0]}), E_ACCESS_DENIED, kein Ergebnis")
        SUMMARY.append(f"#6 Exit 4 ohne Admin: {'ok' if rr and rr[0] == 4 else 'FEHLER'}")

    print("\n== export ==")
    for style in ("flat", "iometer"):
        csvp = work / f"export-{style}.csv"
        p = run(sbench, "export", out, "--csv", csvp, "--style", style)
        ok = p.returncode == 0 and csvp.exists()
        check(ok, f"export --style {style}: Exit 0")
        if ok:
            text = csvp.read_text(encoding="utf-8")
            rows = list(csv.reader(io.StringIO(text)))
            if style == "flat":
                check(rows[0][:6] == ["test", "type", "scope", "id", "iops", "mbps"] and len(rows) == 1 + 1 + 2 + 1,
                      f"flat: Kopf + 4 Zeilen (total, 2 Worker, 1 Spec), ist {len(rows)}")
            else:
                hdr = next(r for r in rows if r and r[0] == "Target Type")
                allr = next(r for r in rows if r and r[0] == "ALL")
                iops = float(allr[hdr.index("IOps")])
                check(abs(iops - res["tests"][0]["summary"]["iops"]) < 1, f"iometer: ALL-Zeile IOps {iops:.0f}")
    print(text[:600])

    if not quick:
        print("\n== Volume-Ziel nur lesend (\\\\.\\C:, benötigt Admin) ==")
        is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
        volj = {"schema": "sb-job/1", "name": "volume read", "tests": [{"type": "storage",
                "targets": [{"path": "\\\\.\\C:", "kind": "volume"}], "workers": 1, "queue_depth": 4, "warmup_s": 1, "duration_s": 5,
                "access_specs": [{"weight": 100, "block_size": "4KiB", "read_pct": 100, "random_pct": 100}]}]}
        volp = work / "job-vol.json"
        volp.write_text(json.dumps(volj), encoding="utf-8")
        p = run(sbench, "run", "--job", volp, "--out", work / "result-vol.json", "--progress", "stdout")
        if is_admin:
            check(p.returncode == 0, f"volume-Lesetest als Admin: Exit 0 (ist {p.returncode})")
            vres = check_result(work / "result-vol.json", "Ergebnis volume", expect_aborted=False)
            if vres:
                vs = vres["tests"][0]["summary"]
                check(vs["errors"] == 0, f"volume: keine I/O-Fehler ({vres['tests'][0].get('errors')})")
                # Hinweis statt Fehler: der OS-Datenträger der Runner ist bei zufälligen Lesezugriffen
                # extrem langsam (p99 ~1 s); I/Os, die die Messphase überdauern, zählen nicht.
                print(f"         volume: io_count={vs['io_count']}, iops={vs['iops']:.1f}, lat={vs['lat_us']}")
                SUMMARY.append(f"volume \\\\.\\C: 4K random read QD4: IOPS {vres['tests'][0]['summary']['iops']:.0f}, "
                               f"p99 {vres['tests'][0]['summary']['lat_us']['p99']:.1f} us")
        else:
            check(p.returncode == 4, f"volume ohne Admin: Exit 4 (ist {p.returncode})")

    print("\n== bundle ==")

    def check_zip(zp: Path, what: str) -> tuple[set, dict]:
        with zipfile.ZipFile(zp) as z:
            names = set(z.namelist())
            man = json.loads(z.read("manifest.json"))
            validate(man, "sb-bundle-1.schema.json", f"{what}: manifest.json")
            check(man["kern_version"] == kern_version, f"{what}: manifest.kern_version = sbench version")
            listed = {f["path"] for f in man["files"]}
            check(listed == names - {"manifest.json"}, f"{what}: manifest listet genau die übrigen ZIP-Einträge")
            bad_hash = [f["path"] for f in man["files"]
                        if hashlib.sha256(z.read(f["path"])).hexdigest() != f["sha256"] or len(z.read(f["path"])) != f["size_bytes"]]
            check(not bad_hash, f"{what}: sha256/size stimmen für {len(man['files'])} Dateien {bad_hash}")
            idoc = json.loads(z.read("info.json"))
            validate(idoc, "sb-info-1.schema.json", f"{what}: info.json")
            if semantic_checks:
                ie = semantic_checks("sb-info-1.schema.json", idoc)
                check(not ie, f"{what}: info.json NUMA-Knoten disjunkt {ie[:3]}")
            for n in sorted(names):
                if n.startswith("results/"):
                    rdoc = json.loads(z.read(n))
                    validate(rdoc, "sb-result-1.schema.json", f"{what}: {n}")
                    jn = "jobs/" + n[len("results/"):]
                    if semantic_checks and jn in names:
                        extra = semantic_checks("sb-result-1.schema.json", rdoc, json.loads(z.read(jn)))
                        check(not extra, f"{what}: {n} semantisch gültig gegen {jn} {extra[:3]}")
                if n.startswith("runs/"):
                    bad = sum(1 for l in z.read(n).decode("utf-8").splitlines()
                              if l.strip() and list(V["sb-progress-1.schema.json"].iter_errors(json.loads(l))))
                    check(bad == 0, f"{what}: {n} gültig gegen sb-progress-1")
            return names, man

    zp0 = work / "bundle-leer.zip"
    p = run(sbench, "bundle", "--out", zp0)
    if check(p.returncode == 0 and zp0.exists(), "bundle ohne --all/--results: Exit 0"):
        names, man = check_zip(zp0, "bundle (leer)")
        check(names == {"manifest.json", "info.json"} and man["anonymized"] is False and man["includes"] == [],
              f"bundle ohne Auswahl: nur manifest + info ({sorted(names)})")

    zp = work / "bundle.zip"
    bprog = work / "progress-bundle.jsonl"
    p = run(sbench, "bundle", "--all", "--out", zp, "--progress", f"file:{bprog}")
    check(p.returncode == 0 and zp.exists(), "bundle --all: Exit 0, ZIP existiert")
    bevs = read_jsonl(bprog)
    check_progress(bevs, "Fortschritt (bundle)", expect_aborted=False)
    check(any(e["ev"] == "progress" for e in bevs), "bundle: progress-Events")
    if zp.exists():
        names, man = check_zip(zp, "bundle --all")
        for need in ("results/smoke-run.json", "runs/smoke-run.jsonl", "logs/smoke-run.log", "results/pipe-run.json",
                     "runs/stop-test.jsonl", "logs/stop-test.log", "results/stop-test.json", "runs/pipe-fail.jsonl"):
            check(need in names, f"bundle --all enthält {need}")

    zp2 = work / "bundle-one.zip"
    p = run(sbench, "bundle", "--out", zp2, "--results", data_dir / "results" / "pipe-run.json")
    if check(p.returncode == 0 and zp2.exists(), "bundle --results <datei>: Exit 0"):
        names, _ = check_zip(zp2, "bundle --results")
        check(sorted(names) == ["info.json", "jobs/pipe-run.json", "logs/pipe-run.log", "manifest.json",
                                "results/pipe-run.json", "runs/pipe-run.jsonl"], f"bundle --results: Inhalt {sorted(names)}")

    # --include + --anonymize: fremder Ordner mit Hostname, Benutzerpfad, IPs, MAC
    host = info["meta"]["host"]
    user = os.environ.get("USERNAME", "runneradmin")
    gui = work / "gui-logs"
    (gui / "sub").mkdir(parents=True)
    (gui / "gui.log").write_text(f"2026-10-20T08:00:00Z verbunden mit {host} (192.168.17.4, fe80::1c2b:3a4d:5e6f:7081%12)\n"
                                 f"Profil C:\\Users\\{user}\\AppData\\sbench MAC 00-15-5D-01-02-03 Zeit 12:34:56\n",
                                 encoding="utf-8")
    (gui / "sub" / "profil.json").write_text(json.dumps({"host": host, "ip": "192.168.17.4", "mac": "00:15:5d:01:02:03",
                                                          "pfad": f"C:\\Users\\{user}\\x.json", "version": "10.0.20348"}),
                                             encoding="utf-8")
    (gui / "bild.bin").write_bytes(b"\x00\x01" + host.encode())
    zp3 = work / "bundle-anon.zip"
    p = run(sbench, "bundle", "--all", "--include", gui, "--anonymize", "--out", zp3)
    if check(p.returncode == 0 and zp3.exists(), "bundle --include --anonymize: Exit 0"):
        names, man = check_zip(zp3, "bundle --anonymize")
        check(man["anonymized"] is True and man["includes"] == ["gui-logs"], f"manifest: anonymized, includes {man['includes']}")
        check({"extra/gui-logs/gui.log", "extra/gui-logs/sub/profil.json", "extra/gui-logs/bild.bin"} <= names,
              "extra/gui-logs/ rekursiv aufgenommen")
        with zipfile.ZipFile(zp3) as z:
            leaks = []
            for n in names:
                if n.endswith((".json", ".jsonl", ".log")):
                    t = z.read(n).decode("utf-8", "replace")
                    if host.lower() in t.lower():
                        leaks.append(f"{n}: Hostname")
                    if f"\\{user.lower()}\\" in t.lower() or f"\\\\{user.lower()}\\\\" in t.lower():
                        leaks.append(f"{n}: Benutzername")
                    if "192.168.17.4" in t or "00:15:5d:01:02:03" in t.lower() or "00-15-5d-01-02-03" in t.lower() or "fe80::1c2b" in t:
                        leaks.append(f"{n}: Adresse")
            check(not leaks, f"anonymisiert: keine Hostnamen/Benutzer/IPs/MACs ({leaks[:5]})")
            g = z.read("extra/gui-logs/gui.log").decode("utf-8")
            print("         gui.log anonymisiert: " + g.replace("\n", " | "))
            pj = json.loads(z.read("extra/gui-logs/sub/profil.json"))
            check(pj["host"].startswith("host-") and pj["ip"].startswith("10.0.") and pj["mac"].startswith("02:00:00:00:")
                  and "user-" in pj["pfad"] and pj["version"] == "10.0.20348" and "12:34:56" in g,
                  f"Pseudonyme konsistent, Versionen/Zeiten unverändert ({pj})")
            check(z.read("extra/gui-logs/bild.bin") == b"\x00\x01" + host.encode(), "Binärdateien unverändert")
            check(json.loads(z.read("info.json"))["meta"]["host"] == pj["host"], "gleicher Hostname -> gleiches Pseudonym")

    print("\n== Zusammenfassung ==")
    for s in SUMMARY:
        print(s)
    try:
        testfile.unlink()
    except OSError:
        pass
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("### sbench Smoke-Test\n\n" + "\n".join(f"- {s}" for s in SUMMARY) + "\n\n")
            f.write(f"Ergebnis: {'OK' if not FAILS else str(len(FAILS)) + ' Fehler'}\n")
    out_dir = os.environ.get("SBENCH_SMOKE_OUT")
    if out_dir:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        od = Path(out_dir)
        # feste Namen für das Artefakt sbench-smoke-<runner>-<arch>
        for src, name in [(out, "result.json"), (prog, "progress.jsonl"), (sout, "result-stop.json"), (nout, "result-ni.json"),
                          (zp, "bundle.zip"), (zp3, "bundle-anon.zip"),
                          (work / "result-vol.json", "result-volume.json")]:
            if src.exists():
                shutil.copy2(src, od / name)
        (od / "info.json").write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n{len(FAILS)} Fehler" if FAILS else "\nAlle Smoke-Tests bestanden.")
    for m in FAILS:
        print("  - " + m)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
