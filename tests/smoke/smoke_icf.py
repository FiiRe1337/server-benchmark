"""Smoke-Tests sbench import-icf (Iometer-ICF -> sb-job/1), aufgerufen aus smoke.py.

  * GUI-Vertrag: import-icf <icf> --out <job> -> sb-validate/1 auf stdout, Job-Datei gültig gegen sb-job/1
  * -o / ohne -o (Job auf stdout, Meldungen auf stderr) / --json ohne -o (nur Bericht)
  * --target ersetzt die ICF-Targets; falsche Anzahl -> Exit 2
  * kaputte Dateien -> Exit 2 mit E_ICF_*, fehlende Datei -> Exit 1, keine Job-Datei
  * Warnungen W_ICF_* der Fixtures, Schreibtest auf PhysicalDrive ohne allow_destructive -> W_ICF_VALIDATE
  * der importierte Standardprofil-Job läuft (verkürzt) mit sbench run; die I/O-Anteile je Access Spec
    entsprechen den Iometer-Gewichten (% of size)
"""
from __future__ import annotations

import json
from pathlib import Path

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "icf"


def _codes(doc: dict, kind: str = "warnings") -> set[str]:
    return {e["code"] for e in doc.get(kind, [])}


def run_icf(h, sbench: Path, work: Path, quick: bool) -> dict:
    check, run, validate, SUMMARY = h.check, h.run, h.validate, h.SUMMARY
    print("\n== import-icf ==")
    out: dict = {}

    def imp(icf: str, *args, expect: int = 0, what: str = ""):
        p = run(sbench, "import-icf", FIX / icf, *args)
        check(p.returncode == expect, f"import-icf {what or icf}: Exit {expect} (ist {p.returncode})")
        return p

    def report(p, what: str) -> dict:
        try:
            doc = json.loads(p.stdout.decode("utf-8"))
        except ValueError:
            check(False, f"{what}: stdout ist sb-validate/1")
            return {}
        validate(doc, "sb-validate-1.schema.json", what)
        return doc

    # GUI-Aufruf (gui/sbgui/sbench.py): import-icf <icf> --out <job>
    gjob = work / "icf-gui-demo.json"
    p = imp("gui-demo.icf", "--out", gjob, what="gui-demo --out (GUI-Vertrag)")
    d = report(p, "import-icf --out")
    check(d.get("ok") is True and {"W_ICF_NO_TARGET", "W_ICF_REPLY"} <= _codes(d), f"gui-demo: ok, W_ICF_NO_TARGET, W_ICF_REPLY ({_codes(d)})")
    if gjob.exists():
        validate(json.loads(gjob.read_text(encoding="utf-8")), "sb-job-1.schema.json", "gui-demo Job")

    # Standardprofile mit eigenem Target, -o
    target = work / "icf-target.dat"
    sjob = work / "icf-standard.json"
    p = imp("standard-profiles.icf", "-o", sjob, "--target", target, what="standard-profiles -o --target")
    d = report(p, "import-icf -o")
    job = json.loads(sjob.read_text(encoding="utf-8")) if sjob.exists() else None
    if check(job is not None and d.get("ok") is True, "standard-profiles: Job geschrieben, ok = true"):
        validate(job, "sb-job-1.schema.json", "standard-profiles Job")
        T = job["tests"]
        check([t["name"] for t in T] == ["File Server", "Web Server", "Workstation", "Database"], "4 Tests in ICF-Reihenfolge")
        check([len(t["access_specs"]) for t in T] == [8, 10, 1, 1], "Access Specs 8/10/1/1")
        check(all(t["targets"][0]["path"] == str(target) and t["queue_depth"] == 32 for t in T), "--target ersetzt das Target, QD 32")
        p = run(sbench, "validate", "--job", sjob)
        check(p.returncode == 0, f"validate des importierten Jobs: Exit 0 (ist {p.returncode})")

        # verkürzt laufen lassen: 256 MiB, 2 s je Test, ohne Ramp Up
        for t in T:
            t["targets"][0]["file_size"] = "256MiB"
            t["duration_s"] = 1 if quick else 2
            t.pop("warmup_s", None)
        rjob = work / "icf-standard-short.json"
        rjob.write_text(json.dumps(job, indent=2), encoding="utf-8")
        rout = work / "result-icf.json"
        p = run(sbench, "run", "--job", rjob, "--out", rout, "--progress", f"file:{work / 'progress-icf.jsonl'}", timeout=300)
        check(p.returncode == 0, f"run des importierten Jobs: Exit 0 (ist {p.returncode})")
        res = h.check_result(rout, "Ergebnis (import-icf)", expect_aborted=False, job=rjob)
        if res:
            out["result"] = rout
            fs = res["tests"][0]
            n = sum(s["io_count"] for s in fs["per_spec"])
            shares = [100.0 * s["io_count"] / n if n else 0 for s in fs["per_spec"]]
            dev = max(abs(sh - s["weight"]) for sh, s in zip(shares, fs["per_spec"]))
            check(n > 1000 and dev <= 5.0, f"File Server: I/O-Anteile je Spec ~ Gewichte (max. Abweichung {dev:.1f} Prozentpunkte, {n} I/Os)")
            line = " / ".join(f"{t['name']} {t['summary']['iops']:.0f} IOPS" for t in res["tests"])
            SUMMARY.append(f"import-icf Standardprofile (256 MiB, QD32, {T[0]['duration_s']} s): {line}")

    # ohne -o: Job auf stdout, Meldungen auf stderr
    p = imp("iometer-default.icf", what="iometer-default ohne -o")
    try:
        j = json.loads(p.stdout.decode("utf-8"))
        ok = j.get("schema") == "sb-job/1" and j["tests"][0]["access_specs"][0]["read_pct"] == 67
    except ValueError:
        ok = False
    err = p.stderr.decode("utf-8", "replace")
    check(ok and "W_ICF_RUN_TIME" in err and "W_ICF_DISK_SIZE" in err, "ohne -o: Job auf stdout, W_ICF_RUN_TIME/W_ICF_DISK_SIZE auf stderr")
    # --json ohne -o: nur der Bericht
    d = report(imp("multi-worker.icf", "--json", what="multi-worker --json"), "import-icf --json")
    want = {"W_ICF_MANAGERS", "W_ICF_NETWORK_WORKER", "W_ICF_DATA_PATTERN", "W_ICF_WORKER_SPLIT"}
    check(d.get("ok") is True and want <= _codes(d), f"multi-worker: {sorted(want)} ({sorted(_codes(d))})")
    d = report(imp("physical-2006.icf", "--json", what="physical-2006 --json"), "import-icf physical")
    check(any(w["code"] == "W_ICF_VALIDATE" and "E_DESTRUCTIVE_NOT_ALLOWED" in w["msg"] for w in d.get("warnings", []))
          and "W_ICF_CYCLING" in _codes(d), "physical-2006: W_ICF_VALIDATE (E_DESTRUCTIVE_NOT_ALLOWED), W_ICF_CYCLING")

    # Fehler
    for icf, code in (("broken-weights.icf", "E_ICF_SPEC"), ("broken-truncated.icf", "E_ICF_FORMAT"),
                      ("broken-no-version.icf", "E_ICF_FORMAT"), ("broken-values.icf", "E_ICF_VALUE")):
        bj = work / f"icf-{icf}.json"
        d = report(imp(icf, "--out", bj, expect=2), f"import-icf {icf}")
        check(d.get("ok") is False and code in _codes(d, "errors") and not bj.exists(), f"{icf}: {code}, keine Job-Datei")
    d = report(imp("standard-profiles.icf", "--json", "--target", work / "a.dat", "--target", work / "b.dat", expect=2,
                   what="--target zweimal bei einem ICF-Target"), "import-icf --target")
    check("E_ICF_TARGET_COUNT" in _codes(d, "errors"), "E_ICF_TARGET_COUNT")
    p = run(sbench, "import-icf", FIX / "gibt-es-nicht.icf", "--out", work / "x.json")
    check(p.returncode == 1, f"fehlende ICF-Datei: Exit 1 (ist {p.returncode})")
    p = run(sbench, "import-icf")
    check(p.returncode == 2, f"import-icf ohne Datei: Exit 2 (ist {p.returncode})")
    return out
