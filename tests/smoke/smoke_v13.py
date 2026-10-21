"""Smoke-Tests Vertrag 1.3, aufgerufen aus smoke_m2.py (nutzt dessen cpu-/memory-Läufe).

  * tick.metric in jedem Tick, score in cpu-Ticks (= ops_per_s / meta.cpu_score.ref_ops_per_s × 1000)
  * phase-Event beim Teststart mit threads und cpus (group/number, numa_node, core, smt_sibling_of),
    auch bei threads = 0; SMT-Geschwister passen zu meta.cpu.cores
  * meta.cpu_score, summary.score_reference, summary.proc_cpu_pct, timeseries.proc_cpu_pct, tick_ms
  * preparing-Fortschritt memory: total = threads × buffer_size_bytes (auch copy), letztes done = total
  * Vertrag 1.3.1: Stopp -> aborted true ohne errors[].fatal (Laufzeitfehler: Unit-Test test_v131_fatal)
  * Vertrag 1.3.1: tests[].cpus im Ergebnis = phase.cpus (cpu, memory, auch Teilergebnisse)
  * Teilergebnis: Stopp in warmup (cpu) und früh in preparing/warmup (memory) -> partial: true,
    stopped_in = letzte Phase, Messwerte null, Listen leer
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path


def _job(path: Path, name: str, tests: list[dict]) -> Path:
    path.write_text(json.dumps({"schema": "sb-job/1", "name": name, "tests": tests}, indent=2), encoding="utf-8")
    return path


def _stop_run(h, sbench: Path, work: Path, name: str, tests: list[dict], wait_for: str, extra_wait: float):
    """Lauf starten, warten bis phase wait_for erscheint (+extra_wait s), stoppen. -> (rc, events, result)"""
    jp = _job(work / f"job-{name}.json", name, tests)
    prog, out = work / f"progress-{name}.jsonl", work / f"result-{name}.json"
    proc = subprocess.Popen([str(sbench), "run", "--job", str(jp), "--out", str(out), "--progress", f"file:{prog}",
                             "--id", name], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.time() + 60
    while time.time() < deadline and proc.poll() is None:
        if prog.exists() and f'"name":"{wait_for}"' in prog.read_text(encoding="utf-8"):
            break
        time.sleep(0.02)
    time.sleep(extra_wait)
    h.run(sbench, "stop", "--id", name)
    try:
        rc = proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
        rc = None
    evs = h.read_jsonl(prog) if prog.exists() else []
    h.check_progress(evs, f"Fortschritt ({name})", expect_aborted=True)
    res = h.check_result(out, f"Teilergebnis ({name})", expect_aborted=True)
    return rc, evs, res


def _check_nulls(h, t: dict, what: str, keys: tuple[str, ...]) -> None:
    s = t["summary"]
    h.check(all(s.get(k, None) is None for k in keys) and "cpu_pct" in s and s["cpu_pct"] is None,
            f"{what}: Messwerte null statt 0 ({ {k: s.get(k) for k in keys} })")
    h.check(not t.get("per_worker") and not t.get("timeseries"), f"{what}: per_worker/timeseries leer")


def run_v13(h, sbench: Path, work: Path, info: dict, quick: bool, cprog: Path, cout: Path, mprog: Path, mout: Path) -> None:
    check = h.check
    m = info["meta"]
    print("\n== Vertrag 1.3: info ==")
    cs = m.get("cpu_score") or {}
    refs = cs.get("ref_ops_per_s", {})
    check(set(refs) == {"int", "float", "crypto", "mixed"} and cs.get("points") == 1000 and "formula" in cs,
          f"info meta.cpu_score mit Formel und Referenzen ({refs})")

    print("\n== Vertrag 1.3: cpu-Lauf (Ticks, phase, Score) ==")
    if cout.exists() and cprog.exists():
        evs = h.read_jsonl(cprog)
        res = json.loads(cout.read_text(encoding="utf-8"))
        check(res.get("tick_ms") == 500, f"Ergebnis tick_ms = 500 ({res.get('tick_ms')})")
        check(res["meta"].get("cpu_score", {}).get("ref_ops_per_s") == refs, "Ergebnis meta.cpu_score = info")
        for t in res["tests"]:
            i, s = t["index"], t["summary"]
            nm = t["config"].get("name")
            ticks = [e for e in evs if e["ev"] == "tick" and e["test"] == i]
            ph = [e for e in evs if e["ev"] == "phase" and e["test"] == i]
            ref = refs.get(s["workload"], 0)
            check(t.get("partial") is False and "stopped_in" not in t, f"cpu {nm}: partial false")
            check(s.get("score_reference") == ref and abs(s["score"] - s["ops_per_s"] / ref * 1000) <= 0.1 + 1e-6 * s["score"],
                  f"cpu {nm}: score_reference {s.get('score_reference')}, score = ops_per_s / ref × 1000")
            check(ticks and all(e.get("metric") == "ops_per_s" and abs(e["score"] - e["ops_per_s"] / ref * 1000) <= 0.1 + 1e-6 * e["score"]
                                for e in ticks), f"cpu {nm}: {len(ticks)} Ticks mit metric ops_per_s und score")
            check(ph and ph[0]["name"] == "preparing" and ph[0].get("threads") == s["threads"]
                  and [c["cpu"] for c in ph[0].get("cpus", [])] == s["cpus"],
                  f"cpu {nm}: erstes phase-Event mit threads {ph[0].get('threads') if ph else None} und cpus = summary.cpus")
            check(ph and t.get("cpus") == ph[0].get("cpus"), f"cpu {nm}: tests[].cpus = phase.cpus (Vertrag 1.3.1)")
            check(all(len(e.get("workers", [])) == s["threads"] for e in ticks), f"cpu {nm}: workers[] vollständig")
            check("proc_cpu_pct" in s and all("proc_cpu_pct" in p and "score" in p for p in t["timeseries"]),
                  f"cpu {nm}: summary.proc_cpu_pct {s.get('proc_cpu_pct')}, timeseries mit proc_cpu_pct/score")
        allt = next((t for t in res["tests"] if t["config"].get("name") == "mixed alle"), None)
        if allt:
            ph = next(e for e in evs if e["ev"] == "phase" and e["test"] == allt["index"])
            cpus = ph.get("cpus", [])
            cores = {c["core"] for c in cpus}
            sib = [c for c in cpus if c["smt_sibling_of"] is not None]
            check(ph.get("threads") == m["cpu"]["threads"] and len(cores) == m["cpu"]["cores"]
                  and len(sib) == len(cpus) - len(cores),
                  f"threads = 0: phase.threads {ph.get('threads')}, {len(cores)} Kerne (meta.cpu.cores {m['cpu']['cores']}), "
                  f"{len(sib)} SMT-Geschwister")
            first = {c["core"]: c["cpu"] for c in cpus if c["smt_sibling_of"] is None}
            check(all(first.get(c["core"]) == c["smt_sibling_of"] for c in sib), "smt_sibling_of = erster Prozessor desselben Kerns")
            nodes = {n["id"]: set(n["cpus"]) for n in m.get("numa_nodes", [])}
            check(all(c["cpu"] in nodes.get(c["numa_node"], set()) and c["cpu"] == c["group"] * 64 + c["number"] for c in cpus),
                  "phase.cpus: numa_node passt zu meta.numa_nodes, cpu = group × 64 + number")
            h.SUMMARY.append(f"Vertrag 1.3 phase.cpus (threads=0): {len(cpus)} Threads, {len(cores)} Kerne, {len(sib)} SMT-Geschwister")

    print("\n== Vertrag 1.3: memory-Lauf (metric, preparing-Fortschritt) ==")
    if mout.exists() and mprog.exists():
        evs = h.read_jsonl(mprog)
        res = json.loads(mout.read_text(encoding="utf-8"))
        for t in res["tests"]:
            i, s = t["index"], t["summary"]
            nm = t["config"].get("name")
            key = "latency_ns" if s["mode"] == "latency" else "gbps"
            ticks = [e for e in evs if e["ev"] == "tick" and e["test"] == i]
            prep = [e for e in evs if e["ev"] == "progress" and e["test"] == i and e["phase"] == "preparing"]
            tot = s["threads"] * s["buffer_size_bytes"]
            check(ticks and all(e.get("metric") == key for e in ticks), f"memory {nm}: metric {key} in allen Ticks")
            check(bool(prep) and all(e["total"] == tot for e in prep) and prep[-1]["done"] == tot,
                  f"memory {nm}: preparing total = threads × buffer_size_bytes = {tot} ({len(prep)} Events)")
            if s["mode"] == "latency":
                check(all("workers" not in e for e in ticks), f"memory {nm}: latency ohne workers[] (1 Thread)")
            else:
                check(all([w["id"] for w in e["workers"]] == list(range(s["threads"])) for e in ticks),
                      f"memory {nm}: workers[] vollständig, nach id sortiert")
            check(s.get("proc_cpu_pct") is not None and t.get("partial") is False, f"memory {nm}: proc_cpu_pct, partial false")
            mph = [e for e in evs if e["ev"] == "phase" and e["test"] == i]
            check(mph and t.get("cpus") == mph[0].get("cpus") and len(t.get("cpus") or []) == s["threads"],
                  f"memory {nm}: tests[].cpus = phase.cpus, ein Eintrag je Thread (Vertrag 1.3.1)")

    print("\n== Vertrag 1.3: Stopp in warmup (cpu) ==")
    rc, evs, res = _stop_run(h, sbench, work, "v13-stop-warmup",
                             [{"type": "cpu", "name": "cpu warmup", "workload": "int", "threads": 1, "warmup_s": 60, "duration_s": 5},
                              {"type": "cpu", "name": "nie gestartet", "workload": "int", "threads": 1, "duration_s": 1}],
                             "warmup", 0.5)
    check(rc == 3, f"Stopp in warmup: Exit 3 (ist {rc})")
    if res:
        check(res.get("aborted") is True and not any(e.get("fatal") for t in res["tests"] for e in t["errors"]),
              "Stopp: aborted true, kein errors[].fatal (Vertrag 1.3.1)")
        dn = [e for e in evs if e["ev"] == "done"]
        check(dn and dn[-1].get("aborted") is True, "Stopp: done.aborted true")
        T = res["tests"]
        check(len(T) == 1 and T[0]["index"] == 0, f"nicht begonnener Test fehlt im Ergebnis ({[t['index'] for t in T]})")
        if T:
            t = T[0]
            check(t.get("partial") is True and t.get("stopped_in") == "warmup", f"partial true, stopped_in warmup ({t.get('stopped_in')})")
            _check_nulls(h, t, "Stopp in warmup", ("ops_per_s", "score", "ops", "proc_cpu_pct"))
            check(t["summary"].get("threads") == 1 and t["summary"].get("cpus"), "Stopp in warmup: threads/cpus bleiben gesetzt")
            check([c["cpu"] for c in t.get("cpus", [])] == t["summary"].get("cpus"), "Stopp in warmup: tests[].cpus bleibt gesetzt (Vertrag 1.3.1)")
        check(not any(e["ev"] == "tick" for e in evs), "Stopp in warmup: keine Ticks")

    print("\n== Vertrag 1.3: früher Stopp (memory preparing/warmup) ==")
    rc, evs, res = _stop_run(h, sbench, work, "v13-stop-early",
                             [{"type": "memory", "name": "mem früh", "mode": "bandwidth", "op": "copy", "threads": 0,
                               "buffer_size": "256MiB" if quick else "512MiB", "warmup_s": 60, "duration_s": 5}],
                             "preparing", 0)
    check(rc == 3, f"früher Stopp: Exit 3 (ist {rc})")
    last = [e["name"] for e in evs if e["ev"] == "phase" and e["name"] != "cleanup"]
    if res and res["tests"]:
        t = res["tests"][0]
        check(t.get("partial") is True and t.get("stopped_in") == (last[-1] if last else None),
              f"früher Stopp: stopped_in {t.get('stopped_in')} = letzte Phase {last[-1:]}")
        _check_nulls(h, t, f"Stopp in {t.get('stopped_in')}", ("gbps", "bytes", "proc_cpu_pct"))
        h.SUMMARY.append(f"Vertrag 1.3 früher Stopp memory: stopped_in {t.get('stopped_in')}")
