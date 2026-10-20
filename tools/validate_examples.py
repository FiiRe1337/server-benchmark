#!/usr/bin/env python3
"""Validiert alle JSON-Schemas in schema/ und alle Beispiele in schema/examples/.

Prüft:
  * jedes Schema gegen das Metaschema (JSON Schema draft 2020-12),
  * jedes Beispiel gegen sein Schema (Zuordnung über den Dateinamen),
  * einige semantische Regeln, die JSON Schema nicht ausdrücken kann
    (z. B. Summe der access_specs-Gewichte = 100, allow_destructive bei
    Schreibtests auf volume/physical, tests[].index streng monoton steigend und
    < Anzahl Tests im Job, disjunkte NUMA-CPU-Listen),
  * einen Selbsttest der semantischen Regeln mit absichtlich falschen Dokumenten.

Die Funktionen check_result_indices() und check_numa() nutzen auch die Smoke-Tests
(tests/smoke/smoke.py) für echte Ergebnisse und Bundles.

Aufruf:  python tools/validate_examples.py      (Exit 0 = alles gültig, 1 = Fehler)
Benötigt: pip install jsonschema
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "schema"
EXAMPLE_DIR = SCHEMA_DIR / "examples"

# Dateinamen-Präfix des Beispiels -> Schema-Datei
MAPPING = {
    "job-": "sb-job-1.schema.json",
    "result-": "sb-result-1.schema.json",
    "progress-": "sb-progress-1.schema.json",
    "info": "sb-info-1.schema.json",
    "validate-": "sb-validate-1.schema.json",
    "runs": "sb-runs-1.schema.json",
    "bundle-": "sb-bundle-1.schema.json",
}

# Ergebnis-Beispiel -> zugehöriger Job (für die Obergrenze von tests[].index)
RESULT_JOBS = {
    "result-storage.json": "job-storage-mix.json",
    "result-gaps.json": "job-mixed-gaps.json",
    "result-compute.json": "job-compute.json",
}


def load_json(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def check_result_indices(result: dict, job: dict | None = None) -> list[str]:
    """tests[].index: ganzzahlig, >= 0, streng monoton steigend und kleiner als die Anzahl
    der Tests im Job. Lücken sind erlaubt: nicht implementierte oder übersprungene Tests
    (z. B. E_NOT_IMPLEMENTED) fehlen im Ergebnis. Ohne Job entfällt die Obergrenze; ist
    der Job da, muss config.type zum Job-Test an dieser Position passen."""
    errs: list[str] = []
    prev = -1
    njob = len(job["tests"]) if job and isinstance(job.get("tests"), list) else None
    for i, t in enumerate(result.get("tests", [])):
        idx = t.get("index")
        if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0:
            errs.append(f"/tests/{i}/index: {idx!r} ist keine Ganzzahl >= 0")
            continue
        if idx <= prev:
            errs.append(f"/tests/{i}/index: {idx} nicht streng steigend (vorher {prev})")
        prev = max(prev, idx)
        if njob is not None:
            if idx >= njob:
                errs.append(f"/tests/{i}/index: {idx} >= Anzahl Tests im Job ({njob})")
            elif job["tests"][idx].get("type") != t.get("type"):
                errs.append(f"/tests/{i}: type {t.get('type')} passt nicht zu Job-Test {idx} ({job['tests'][idx].get('type')})")
    return errs


def check_numa(meta: dict, prefix: str = "/meta") -> list[str]:
    """numa_nodes: CPU-Listen (globale Indizes Gruppe*64+Bit) eindeutig, nicht überlappend,
    insgesamt höchstens cpu.threads; Knoten-RAM-Summe <= ram_bytes, frei <= gesamt."""
    errs: list[str] = []
    nodes = meta.get("numa_nodes") or []
    seen: dict[int, int] = {}
    ids = [n.get("id") for n in nodes]
    if len(set(ids)) != len(ids):
        errs.append(f"{prefix}/numa_nodes: doppelte Knoten-IDs {ids}")
    for k, n in enumerate(nodes):
        cpus = n.get("cpus", [])
        if len(set(cpus)) != len(cpus):
            errs.append(f"{prefix}/numa_nodes/{k}/cpus: doppelte Einträge")
        for c in cpus:
            if c in seen and seen[c] != k:
                errs.append(f"{prefix}/numa_nodes/{k}/cpus: CPU {c} auch in Knoten {nodes[seen[c]].get('id')}")
                break
            seen[c] = k
        if "ram_free_bytes" in n and "ram_bytes" in n and n["ram_free_bytes"] > n["ram_bytes"]:
            errs.append(f"{prefix}/numa_nodes/{k}: ram_free_bytes > ram_bytes")
    threads = (meta.get("cpu") or {}).get("threads")
    if nodes and threads and len(seen) > threads:
        errs.append(f"{prefix}/numa_nodes: {len(seen)} CPUs in Knoten > cpu.threads {threads}")
    total = sum(n.get("ram_bytes", 0) for n in nodes)
    if nodes and meta.get("ram_bytes") and total > meta["ram_bytes"]:
        errs.append(f"{prefix}/numa_nodes: Summe ram_bytes {total} > meta.ram_bytes {meta['ram_bytes']}")
    return errs


def check_compute(i: int, t: dict, meta: dict) -> list[str]:
    """cpu/memory (Vertrag 1.2): Threads, gepinnte CPUs, NUMA-Zuordnung, Summen."""
    errs: list[str] = []
    s, cfg, pw = t["summary"], t["config"], t.get("per_worker", [])
    pre = f"/tests/{i}"
    latency = t["type"] == "memory" and s.get("mode") == "latency"
    key = "ops_per_s" if t["type"] == "cpu" else ("latency_ns" if latency else "gbps")
    if latency and s["threads"] != 1:
        errs.append(f"{pre}/summary/threads: latency misst immer mit 1 Thread")
    if "cpus" in s and len(s["cpus"]) != s["threads"]:
        errs.append(f"{pre}/summary/cpus: {len(s['cpus'])} Einträge, threads = {s['threads']}")
    if pw:
        if len(pw) != s["threads"]:
            errs.append(f"{pre}/per_worker: {len(pw)} Einträge, threads = {s['threads']}")
        if [w["id"] for w in pw] != list(range(len(pw))):
            errs.append(f"{pre}/per_worker: id muss 0..n-1 sein")
        if "cpus" in s and [w.get("cpu") for w in pw] != s["cpus"]:
            errs.append(f"{pre}/per_worker: cpu passt nicht zu summary.cpus")
        if key in s and all(key in w for w in pw) and not latency:
            tot = sum(w[key] for w in pw)
            if abs(tot - s[key]) > max(1e-3 * s[key], 0.01):
                errs.append(f"{pre}/per_worker: Summe {key} {tot:.3f} != summary.{key} {s[key]}")
    nodes = {n["id"]: set(n["cpus"]) for n in meta.get("numa_nodes", [])}
    if nodes:
        for k, w in enumerate(pw):
            if "node" in w and "cpu" in w and w["cpu"] not in nodes.get(w["node"], set()):
                errs.append(f"{pre}/per_worker/{k}: cpu {w['cpu']} liegt nicht in NUMA-Knoten {w['node']}")
        for f in ("numa_node", "mem_node"):
            if f in cfg and cfg[f] not in nodes:
                errs.append(f"{pre}/config/{f}: Knoten {cfg[f]} fehlt in meta.numa_nodes (E_NUMA_NODE)")
        if "numa_node" in cfg:
            bad = [c for c in s.get("cpus", []) if c not in nodes.get(cfg["numa_node"], set())]
            if bad:
                errs.append(f"{pre}/summary/cpus: {bad} liegen außerhalb von numa_node {cfg['numa_node']}")
        if "mem_node" in cfg and any(w.get("mem_node", cfg["mem_node"]) != cfg["mem_node"] for w in pw):
            errs.append(f"{pre}/per_worker: mem_node weicht von config.mem_node ab")
    return errs


def semantic_checks(kind: str, doc, job: dict | None = None) -> list[str]:
    """Regeln außerhalb von JSON Schema (entsprechen den Prüfungen von 'sbench validate')."""
    errs: list[str] = []

    def check_tests(tests, prefix):
        for i, t in enumerate(tests):
            if t.get("type") == "storage":
                s = sum(spec["weight"] for spec in t["access_specs"])
                if s != 100:
                    errs.append(f"{prefix}/{i}/access_specs: Summe der Gewichte {s} != 100 (E_WEIGHT_SUM)")
                writes = any(spec["read_pct"] < 100 for spec in t["access_specs"])
                for k, tg in enumerate(t["targets"]):
                    if writes and tg["kind"] in ("volume", "physical") and not tg.get("allow_destructive", False):
                        errs.append(f"{prefix}/{i}/targets/{k}/allow_destructive: Schreibtest auf {tg['kind']} "
                                    "ohne allow_destructive: true (E_DESTRUCTIVE_NOT_ALLOWED)")

    if kind == "sb-job-1.schema.json":
        check_tests(doc["tests"], "/tests")
    elif kind == "sb-result-1.schema.json":
        check_tests([t["config"] for t in doc["tests"]], "/tests/*/config")
        errs += check_result_indices(doc, job)
        errs += check_numa(doc.get("meta", {}))
        for i, t in enumerate(doc["tests"]):
            if t["type"] != t["config"]["type"]:
                errs.append(f"/tests/{i}: type passt nicht zu config.type")
            if t["type"] == "storage":
                n = sum(b["count"] for b in t["histogram"])
                if n != t["summary"]["io_count"]:
                    errs.append(f"/tests/{i}/histogram: Summe {n} != summary.io_count {t['summary']['io_count']}")
                if t["per_worker"] and sum(w.get("io_count", 0) for w in t["per_worker"]) != t["summary"]["io_count"]:
                    errs.append(f"/tests/{i}/per_worker: Summe io_count != summary.io_count")
                if sum(s["io_count"] for s in t["per_spec"]) != t["summary"]["io_count"]:
                    errs.append(f"/tests/{i}/per_spec: Summe io_count != summary.io_count")
            if t["type"] in ("cpu", "memory"):
                errs += check_compute(i, t, doc.get("meta", {}))
    elif kind == "sb-info-1.schema.json":
        errs += check_numa(doc.get("meta", {}))
    return errs


_M2 = {"numa_nodes": [{"id": 0, "cpus": [0, 1]}, {"id": 1, "cpus": [64, 65]}]}


def _ct(ops: float = 3.0, cfg: dict | None = None, node: int = 0) -> dict:
    return {"type": "cpu", "config": cfg or {"type": "cpu"},
            "summary": {"threads": 2, "ops_per_s": ops, "cpus": [0, 1]},
            "per_worker": [{"id": 0, "cpu": 0, "node": node, "ops_per_s": 1.0}, {"id": 1, "cpu": 1, "node": 0, "ops_per_s": 2.0}]}


def selftest() -> list[str]:
    """Die semantischen Regeln müssen falsche Dokumente erkennen und Lücken zulassen."""
    bad: list[str] = []
    job = {"tests": [{"type": "cpu"}, {"type": "storage"}, {"type": "memory"}, {"type": "storage"}]}
    res = lambda *ix: {"tests": [{"index": i, "type": "storage"} for i in ix]}  # noqa: E731
    cases = [
        ("Lücke 1,3", check_result_indices(res(1, 3), job), False),
        ("ohne Job", check_result_indices(res(0, 7)), False),
        ("doppelt 1,1", check_result_indices(res(1, 1), job), True),
        ("fallend 3,1", check_result_indices(res(3, 1), job), True),
        ("negativ", check_result_indices(res(-1)), True),
        ("index >= Anzahl", check_result_indices(res(1, 4), job), True),
        ("type passt nicht", check_result_indices(res(0), job), True),
        ("NUMA disjunkt", check_numa({"cpu": {"threads": 4}, "numa_nodes": [{"id": 0, "cpus": [0, 1]}, {"id": 1, "cpus": [2, 3]}]}), False),
        ("NUMA überlappend", check_numa({"numa_nodes": [{"id": 0, "cpus": [0, 1]}, {"id": 1, "cpus": [1, 2]}]}), True),
        ("NUMA > threads", check_numa({"cpu": {"threads": 2}, "numa_nodes": [{"id": 0, "cpus": [0, 1, 2]}]}), True),
        ("cpu ok", check_compute(0, _ct(), _M2), False),
        ("cpu Worker-Summe falsch", check_compute(0, _ct(ops=5.0), _M2), True),
        ("cpu außerhalb numa_node", check_compute(0, _ct(cfg={"type": "cpu", "numa_node": 1}), _M2), True),
        ("cpu Knoten passt nicht", check_compute(0, _ct(node=1), _M2), True),
        ("latency mit 2 Threads", check_compute(0, {"type": "memory", "config": {"type": "memory"},
                                                     "summary": {"mode": "latency", "threads": 2}}, _M2), True),
    ]
    for name, errs, expect_err in cases:
        if bool(errs) != expect_err:
            bad.append(f"{name}: erwartet {'Fehler' if expect_err else 'gültig'}, erhalten {errs}")
    return bad


def main() -> int:
    failures = 0
    schemas: dict[str, dict] = {}
    for p in sorted(SCHEMA_DIR.glob("*.schema.json")):
        schema = load_json(p)
        try:
            Draft202012Validator.check_schema(schema)
            print(f"[OK]   Schema   {p.relative_to(ROOT)}")
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] Schema   {p.relative_to(ROOT)}: {e}")
            failures += 1
        schemas[p.name] = schema

    registry = Registry().with_resources(
        (s["$id"], Resource.from_contents(s)) for s in schemas.values()
    )

    examples = sorted(p for p in EXAMPLE_DIR.iterdir() if p.suffix in (".json", ".jsonl"))
    if not examples:
        print("[FAIL] keine Beispiele gefunden")
        return 1

    for p in examples:
        rel = p.relative_to(ROOT)
        kind = next((v for k, v in MAPPING.items() if p.name.startswith(k)), None)
        if kind is None or kind not in schemas:
            print(f"[FAIL] Beispiel {rel}: kein Schema zugeordnet")
            failures += 1
            continue
        validator = Draft202012Validator(schemas[kind], registry=registry, format_checker=FormatChecker())

        if p.suffix == ".jsonl":
            docs = []
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if line.strip():
                    docs.append((f"Zeile {n}", json.loads(line)))
            evs = [d["ev"] for _, d in docs]
            extra = []
            if not evs or evs[0] != "start":
                extra.append("erstes Event muss 'start' sein")
            if not evs or evs[-1] != "done" or evs.count("done") != 1:
                extra.append("letztes (und einziges) 'done'-Event fehlt")
        else:
            doc = load_json(p)
            docs = [("", doc)]
            jn = RESULT_JOBS.get(p.name)
            extra = semantic_checks(kind, doc, load_json(EXAMPLE_DIR / jn) if jn else None)

        errs = []
        for where, doc in docs:
            for e in sorted(validator.iter_errors(doc), key=lambda e: list(e.path)):
                ptr = "/" + "/".join(str(x) for x in e.absolute_path)
                errs.append(f"{where} {ptr}: {e.message}".strip())
        errs += extra
        if errs:
            failures += 1
            print(f"[FAIL] Beispiel {rel} ({kind})")
            for m in errs[:50]:
                print(f"         - {m}")
        else:
            print(f"[OK]   Beispiel {rel} ({kind})")

    st = selftest()
    if st:
        failures += 1
        print("[FAIL] Selbsttest semantische Regeln")
        for m in st:
            print(f"         - {m}")
    else:
        print("[OK]   Selbsttest semantische Regeln (Index-Lücken, NUMA, cpu/memory)")

    print()
    print("Ergebnis:", "alles gültig" if failures == 0 else f"{failures} Datei(en) fehlerhaft")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
