"""Smoke-Tests Meilenstein 2 (cpu, memory, NUMA/Issue #8), aufgerufen aus smoke.py.

  * info: numa_nodes[].ram_method, Summe ram_bytes plausibel
  * validate: E_NUMA_NODE, E_SIMD_UNSUPPORTED, E_BUFFER_SIZE, E_OUT_OF_MEMORY und die Warnungen
    W_THREADS_IGNORED, W_NONTEMPORAL_IGNORED, W_OVERSUBSCRIBED
  * cpu: int/float/crypto/mixed mit 1 Thread und mit allen Threads, Ticks, Score
  * memory: read/write/copy (alle Threads), non-temporal, jede unterstützte SIMD-Stufe,
    Latenz L1-groß vs. RAM-groß, NUMA-Knoten 0 explizit
  * stop während eines cpu-Laufs -> Exit 3, Teilergebnis
  * export --style flat mit ops_per_s/gbps/latency_ns
  * Vertrag 1.3 (metric, phase.threads/cpus, Score, Teilergebnisse): siehe smoke_v13.py
Echte Messwerte gehen in die Zusammenfassung (SUMMARY).
"""
from __future__ import annotations

import csv
import io
import json
import platform
import subprocess
import time
from pathlib import Path


def _job(path: Path, name: str, tests: list[dict]) -> Path:
    path.write_text(json.dumps({"schema": "sb-job/1", "name": name, "tests": tests}, indent=2), encoding="utf-8")
    return path


def _codes(doc: dict, kind: str) -> list[tuple[str, str]]:
    return [(e["path"], e["code"]) for e in doc.get(kind, [])]


def run_m2(h, sbench: Path, work: Path, info: dict, quick: bool) -> dict:
    """h = Modul smoke (check, validate, run, read_jsonl, check_progress, check_result, SUMMARY)."""
    check, run, SUMMARY = h.check, h.run, h.SUMMARY
    arm = platform.machine().upper() in ("ARM64", "AARCH64")
    out: dict = {}
    m = info["meta"]
    ncpu = m["cpu"]["threads"]

    print("\n== M2: info / NUMA (Issue #8) ==")
    nodes = m.get("numa_nodes", [])
    check(bool(nodes) and all("ram_method" in n for n in nodes), "numa_nodes[].ram_method vorhanden")
    meths = sorted({n.get("ram_method") for n in nodes})
    tot = sum(n["ram_bytes"] for n in nodes)
    check(tot <= m["ram_bytes"] * 1.05 + (1 << 30) and tot >= m["ram_bytes"] * 0.5,
          f"Summe numa_nodes.ram_bytes {tot / 2**30:.2f} GiB plausibel zu ram_bytes {m['ram_bytes'] / 2**30:.2f} GiB")
    if len(nodes) == 1:
        check(nodes[0]["ram_method"] == "total", "ein Knoten -> ram_method = total")
    SUMMARY.append(f"NUMA: {len(nodes)} Knoten, ram_method {meths}, "
                   + ", ".join(f"Knoten {n['id']}: {len(n['cpus'])} CPUs, {n['ram_bytes'] / 2**30:.2f} GiB" for n in nodes))

    print("\n== M2: validate ==")
    bad_simd = "avx2" if arm else "neon"
    vj = _job(work / "job-m2-invalid.json", "m2 invalid", [
        {"type": "cpu", "duration_s": 1, "numa_node": 99},
        {"type": "memory", "mode": "bandwidth", "buffer_size": "64MiB", "duration_s": 1, "simd": bad_simd},
        {"type": "memory", "mode": "bandwidth", "buffer_size": 4096, "duration_s": 1},
        {"type": "memory", "mode": "bandwidth", "threads": 1, "buffer_size": "4096GiB", "duration_s": 1},
        {"type": "memory", "mode": "latency", "buffer_size": "1MiB", "duration_s": 1, "mem_node": 99},
        {"type": "cpu", "duration_s": 1, "mem_node": 0},
    ])
    p = run(sbench, "validate", "--job", vj)
    doc = json.loads(p.stdout.decode("utf-8")) if p.stdout else {}
    h.validate(doc, "sb-validate-1.schema.json", "validate (M2-Fehler)")
    got = _codes(doc, "errors")
    print("         " + str(got))
    want = [("/tests/0/numa_node", "E_NUMA_NODE"), ("/tests/1/simd", "E_SIMD_UNSUPPORTED"),
            ("/tests/2/buffer_size", "E_BUFFER_SIZE"), ("/tests/3/buffer_size", "E_OUT_OF_MEMORY"),
            ("/tests/4/mem_node", "E_NUMA_NODE"), ("/tests/5/mem_node", "E_SCHEMA")]
    check(p.returncode == 2, f"validate M2-Fehler: Exit 2 (ist {p.returncode})")
    for w in want:
        check(w in got, f"validate meldet {w[1]} bei {w[0]}")

    wj = _job(work / "job-m2-warn.json", "m2 warn", [
        {"type": "memory", "mode": "latency", "threads": 4, "buffer_size": "1MiB", "duration_s": 1},
        {"type": "memory", "mode": "bandwidth", "op": "read", "nontemporal": True, "buffer_size": "1MiB", "duration_s": 1},
        {"type": "cpu", "threads": ncpu * 4, "duration_s": 1},
    ])
    p = run(sbench, "validate", "--job", wj)
    doc = json.loads(p.stdout.decode("utf-8")) if p.stdout else {}
    h.validate(doc, "sb-validate-1.schema.json", "validate (M2-Warnungen)")
    got = _codes(doc, "warnings")
    print("         " + str(got))
    check(p.returncode == 0, f"validate M2-Warnungen: Exit 0 (ist {p.returncode})")
    for w in [("/tests/0/threads", "W_THREADS_IGNORED"), ("/tests/1/nontemporal", "W_NONTEMPORAL_IGNORED"),
              ("/tests/2/threads", "W_OVERSUBSCRIBED")]:
        check(w in got, f"validate meldet {w[1]} bei {w[0]}")

    dur = 1 if quick else 2
    warm = 0.5

    print("\n== M2: cpu ==")
    ctests = []
    for wl in ("int", "float", "crypto", "mixed"):
        ctests.append({"type": "cpu", "name": f"{wl} 1T", "workload": wl, "threads": 1, "warmup_s": warm, "duration_s": dur})
    for wl in ("int", "mixed"):
        ctests.append({"type": "cpu", "name": f"{wl} alle", "workload": wl, "threads": 0, "warmup_s": warm, "duration_s": dur})
    ctests.append({"type": "cpu", "name": "mixed Knoten 0", "workload": "mixed", "threads": 0, "numa_node": nodes[0]["id"],
                   "warmup_s": 0, "duration_s": 1})
    cj = _job(work / "job-cpu.json", "m2 cpu", ctests)
    cprog, cout = work / "progress-cpu.jsonl", work / "result-cpu.json"
    p = run(sbench, "run", "--job", cj, "--out", cout, "--progress", f"file:{cprog}", "--tick-ms", 500, timeout=300)
    check(p.returncode == 0, f"cpu-Lauf: Exit 0 (ist {p.returncode})")
    evs = h.read_jsonl(cprog) if cprog.exists() else []
    h.check_progress(evs, "Fortschritt (cpu)", expect_aborted=False, rc=p.returncode)
    res = h.check_result(cout, "Ergebnis (cpu)", expect_aborted=False)
    out["cpu"] = cout
    if res:
        T = res["tests"]
        check([t["index"] for t in T] == list(range(len(ctests))), "alle cpu-Tests im Ergebnis")
        for t in T:
            s = t["summary"]
            ticks = [e for e in evs if e["ev"] == "tick" and e["test"] == t["index"]]
            check(s["ops_per_s"] > 0 and s["score"] > 0, f"cpu {t['config']['name']}: {s['ops_per_s']:.4g} ops/s, Score {s['score']:.0f}")
            check(all("ops_per_s" in e and "cpu_pct" in e for e in ticks) and len(ticks) >= 1,
                  f"cpu {t['config']['name']}: {len(ticks)} Ticks mit ops_per_s")
            if t["config"].get("threads") == 0 and ticks:
                # Alle Prozessoren ausgelastet: der Steuer-Thread muss trotzdem im Tick-Takt (500 ms)
                # laufen; früher 4-8 s Lücken und 2 s Messung -> 4-9 s auf manchen Runnern
                ts_ = [0.0] + [e["t"] for e in ticks]
                gap = max(b - a for a, b in zip(ts_, ts_[1:]))
                dur_ = t["config"]["duration_s"]
                check(gap <= 1.5 and ticks[-1]["t"] <= dur_ + 1.0,
                      f"cpu {t['config']['name']}: Steuer-Thread nicht ausgehungert (max. Tick-Abstand {gap:.2f} s, "
                      f"Messung {ticks[-1]['t']:.2f} s für {dur_} s)")
        st = {t["config"]["name"]: t["summary"] for t in T}
        n_all = st["mixed alle"]["threads"]
        check(n_all == ncpu, f"threads = 0 -> {n_all} Threads = logische Prozessoren {ncpu}")
        check(sorted(st["mixed alle"]["cpus"]) == sorted(c for n in nodes for c in n["cpus"]), "alle logischen Prozessoren je einmal belegt")
        k0 = set(nodes[0]["cpus"])
        check(set(st["mixed Knoten 0"]["cpus"]) <= k0 and st["mixed Knoten 0"]["threads"] == len(k0),
              f"numa_node {nodes[0]['id']}: nur CPUs des Knotens ({st['mixed Knoten 0']['threads']})")
        if ncpu >= 2:
            # int ist latenzgebunden und skaliert auch über SMT-Geschwister (Runner: 1 Kern, 2 Threads)
            sp = st["int alle"]["ops_per_s"] / st["int 1T"]["ops_per_s"]
            spm = st["mixed alle"]["ops_per_s"] / st["mixed 1T"]["ops_per_s"]
            check(sp > 1.3, f"int: Skalierung alle/1T = {sp:.2f} bei {ncpu} logischen Prozessoren (mixed {spm:.2f})")
            SUMMARY.append(f"SMOKE-ERGEBNIS cpu Skalierung alle/1T: int {sp:.2f}, mixed {spm:.2f} ({ncpu} logische Prozessoren)")
        SUMMARY.append("SMOKE-ERGEBNIS cpu 1 Thread: " + ", ".join(
            f"{w} {st[f'{w} 1T']['ops_per_s']:.4g} ops/s (Score {st[f'{w} 1T']['score']:.0f})" for w in ("int", "float", "crypto", "mixed")))
        SUMMARY.append(f"SMOKE-ERGEBNIS cpu alle {n_all} Threads: int {st['int alle']['ops_per_s']:.4g} ops/s (Score {st['int alle']['score']:.0f}), "
                       f"mixed {st['mixed alle']['ops_per_s']:.4g} ops/s (Score {st['mixed alle']['score']:.0f})")

    print("\n== M2: memory ==")
    bs = "64MiB" if quick else "256MiB"
    mtests = []
    for op in ("read", "write", "copy"):
        mtests.append({"type": "memory", "name": f"{op} alle", "mode": "bandwidth", "op": op, "buffer_size": bs,
                       "warmup_s": warm, "duration_s": dur})
    mtests.append({"type": "memory", "name": "copy alle NT", "mode": "bandwidth", "op": "copy", "buffer_size": bs,
                   "nontemporal": True, "warmup_s": warm, "duration_s": dur})
    simds = ["scalar", "neon"] if arm else ["scalar", "sse2", "avx2", "avx512"]
    # nicht unterstützte Stufen vorher per validate aussortieren
    ok_simd = []
    for sv in simds:
        pj = _job(work / f"job-simd-{sv}.json", "simd", [{"type": "memory", "mode": "bandwidth", "buffer_size": "1MiB",
                                                           "duration_s": 1, "simd": sv}])
        if run(sbench, "validate", "--job", pj).returncode == 0:
            ok_simd.append(sv)
    print(f"  unterstützte SIMD-Stufen: {ok_simd}")
    check("scalar" in ok_simd and (("neon" in ok_simd) if arm else ("sse2" in ok_simd)), "scalar und Basis-SIMD unterstützt")
    for sv in ok_simd:
        mtests.append({"type": "memory", "name": f"read 1T {sv}", "mode": "bandwidth", "op": "read", "threads": 1,
                       "buffer_size": bs, "simd": sv, "warmup_s": warm, "duration_s": dur})
    mtests.append({"type": "memory", "name": "Latenz 16KiB", "mode": "latency", "buffer_size": "16KiB", "warmup_s": warm, "duration_s": dur})
    mtests.append({"type": "memory", "name": "Latenz RAM", "mode": "latency", "buffer_size": "256MiB" if quick else "1GiB",
                   "warmup_s": warm, "duration_s": dur, "numa_node": nodes[0]["id"], "mem_node": nodes[-1]["id"]})
    mj = _job(work / "job-mem.json", "m2 memory", mtests)
    mprog, mout = work / "progress-mem.jsonl", work / "result-mem.json"
    p = run(sbench, "run", "--job", mj, "--out", mout, "--progress", f"file:{mprog}", "--tick-ms", 500, timeout=600)
    check(p.returncode == 0, f"memory-Lauf: Exit 0 (ist {p.returncode})")
    evs = h.read_jsonl(mprog) if mprog.exists() else []
    h.check_progress(evs, "Fortschritt (memory)", expect_aborted=False, rc=p.returncode)
    check(any(e["ev"] == "progress" and e["phase"] == "preparing" and e["unit"] == "bytes" for e in evs),
          "memory: progress-Events beim Anlegen der Puffer")
    res = h.check_result(mout, "Ergebnis (memory)", expect_aborted=False)
    out["memory"] = mout
    if res:
        T = res["tests"]
        check([t["index"] for t in T] == list(range(len(mtests))), "alle memory-Tests im Ergebnis")
        st = {t["config"]["name"]: t["summary"] for t in T}
        for t in T:
            s = t["summary"]
            key = "latency_ns" if s["mode"] == "latency" else "gbps"
            ticks = [e for e in evs if e["ev"] == "tick" and e["test"] == t["index"]]
            check(s[key] > 0 and ticks and all(key in e for e in ticks),
                  f"memory {t['config']['name']}: {key} {s[key]:.3f}, {len(ticks)} Ticks")
        for sv in ok_simd:
            check(st[f"read 1T {sv}"]["simd"] == sv, f"simd = {sv} tatsächlich genutzt")
        check(st["copy alle NT"]["nontemporal"] is (not arm), f"copy NT: nontemporal = {st['copy alle NT']['nontemporal']}")
        check(st["read alle"]["gbps"] > 1, f"read alle: {st['read alle']['gbps']:.2f} GB/s > 1")
        l1, lr = st["Latenz 16KiB"]["latency_ns"], st["Latenz RAM"]["latency_ns"]
        check(l1 < 10 and lr > 3 * l1 and 20 < lr < 2000, f"Latenz 16 KiB {l1:.2f} ns, RAM {lr:.1f} ns plausibel")
        check(st["Latenz RAM"]["threads"] == 1, "latency: 1 Thread")
        SUMMARY.append(f"SMOKE-ERGEBNIS memory alle Threads ({bs}/Thread): read {st['read alle']['gbps']:.2f} GB/s, "
                       f"write {st['write alle']['gbps']:.2f} GB/s, copy {st['copy alle']['gbps']:.2f} GB/s, "
                       f"copy NT {st['copy alle NT']['gbps']:.2f} GB/s (nontemporal={st['copy alle NT']['nontemporal']})")
        SUMMARY.append("SMOKE-ERGEBNIS memory read 1 Thread: " + ", ".join(f"{sv} {st[f'read 1T {sv}']['gbps']:.2f} GB/s" for sv in ok_simd))
        SUMMARY.append(f"SMOKE-ERGEBNIS Latenz: 16 KiB {l1:.2f} ns, {st['Latenz RAM']['buffer_size_bytes'] >> 20} MiB {lr:.1f} ns")

    print("\n== M2: stop während cpu ==")
    sj = _job(work / "job-cpu-stop.json", "cpu stop", [{"type": "cpu", "workload": "crypto", "warmup_s": 0, "duration_s": 120}])
    sprog, sout = work / "progress-cpu-stop.jsonl", work / "result-cpu-stop.json"
    proc = subprocess.Popen([str(sbench), "run", "--job", str(sj), "--out", str(sout), "--progress", f"file:{sprog}",
                             "--id", "cpu-stop"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(3)
    ts = time.time()
    p = run(sbench, "stop", "--id", "cpu-stop")
    try:
        rc = proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        rc = None
    react = time.time() - ts
    check(rc == 3, f"cpu nach stop: Exit 3 (ist {rc}, Reaktionszeit {react:.2f} s)")
    # inkl. Start von 'sbench stop' unter Volllast; früher 20-60 s auf manchen Runnern
    check(rc == 3 and react < 5, f"cpu nach stop: Reaktionszeit {react:.2f} s < 5 s")
    if sprog.exists():
        h.check_progress(h.read_jsonl(sprog), "Fortschritt (cpu stop)", expect_aborted=True, rc=rc)
    sres = h.check_result(sout, "Teilergebnis (cpu stop)", expect_aborted=True)
    if sres and sres["tests"]:
        check(sres["tests"][0]["summary"]["ops_per_s"] > 0, "Teilergebnis cpu enthält Messwerte")
        check(sres["tests"][0].get("partial") is True and sres["tests"][0].get("stopped_in") == "measure",
              "Teilergebnis cpu: partial true, stopped_in measure (Vertrag 1.3)")

    print("\n== M2: export flat ==")
    if res:
        csvp = work / "export-mem.csv"
        p = run(sbench, "export", mout, "--csv", csvp, "--style", "flat")
        if check(p.returncode == 0 and csvp.exists(), "export memory flat: Exit 0"):
            rows = list(csv.DictReader(io.StringIO(csvp.read_text(encoding="utf-8"))))
            tot = [r for r in rows if r["scope"] == "total"]
            check(len(tot) == len(mtests) and all(r["gbps"] or r["latency_ns"] for r in tot),
                  "flat: Spalten gbps/latency_ns gefüllt")
            check(all(r["partial"] == "false" and r["stopped_in"] == "" for r in rows), "flat: Spalten partial/stopped_in (Vertrag 1.3)")

    import smoke_v13  # noqa: E402
    smoke_v13.run_v13(h, sbench, work, info, quick, cprog, cout, mprog, mout)
    return out
