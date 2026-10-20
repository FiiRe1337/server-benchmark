#!/usr/bin/env python3
"""Validiert alle JSON-Schemas in schema/ und alle Beispiele in schema/examples/.

Prüft:
  * jedes Schema gegen das Metaschema (JSON Schema draft 2020-12),
  * jedes Beispiel gegen sein Schema (Zuordnung über den Dateinamen),
  * einige semantische Regeln, die JSON Schema nicht ausdrücken kann
    (z. B. Summe der access_specs-Gewichte = 100, allow_destructive bei
    Schreibtests auf volume/physical).

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
}


def load_json(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def semantic_checks(kind: str, doc) -> list[str]:
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
        for i, t in enumerate(doc["tests"]):
            if t["index"] != i:
                errs.append(f"/tests/{i}/index: erwartet {i}, gefunden {t['index']}")
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
    return errs


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
            extra = semantic_checks(kind, doc)

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

    print()
    print("Ergebnis:", "alles gültig" if failures == 0 else f"{failures} Datei(en) fehlerhaft")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
