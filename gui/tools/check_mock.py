#!/usr/bin/env python3
"""Validate mock sbench output / recorded files against the contract (main, Vertrag 1.1; sb-bundle/1 see sb_contract.py).

  python tools/check_mock.py <file.json|file.jsonl|bundle.zip> ...
JSON files are matched by their "schema" field; .jsonl = sb-progress/1 stream; .zip = sbench bundle (manifest + sha256)."""
import hashlib, json, sys, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import sb_contract  # noqa: E402

V = sb_contract.validators()
sys.path.insert(0, str(Path(sb_contract.base_dir()).parent / "tools"))
from validate_examples import semantic_checks  # noqa: E402

SEM = {"sb-job/1": "sb-job-1.schema.json", "sb-result/1": "sb-result-1.schema.json"}


def _doc_errs(d):
    k = d.get("schema")
    if k not in V:
        return [f"unknown schema {k!r}"]
    errs = [f"/{'/'.join(map(str, x.absolute_path))}: {x.message}" for x in V[k].iter_errors(d)]
    if not errs and k in SEM:
        errs += semantic_checks(SEM[k], d)
    if not errs and k == "sb-validate/1" and "warnings" not in d:
        errs.append("warnings missing (contract 1.1: always emitted)")
    if not errs and k in ("sb-result/1", "sb-info/1"):
        miss = [f for f in ("elevated", "data_dir", "ram_free_bytes", "numa_nodes") if f not in d["meta"]]
        if miss:
            errs.append(f"meta lacks {miss} (contract 1.1: always emitted)")
    return errs


def _stream_errs(text):
    errs = []
    evs = [json.loads(l) for l in text.splitlines() if l.strip()]
    for n, e in enumerate(evs, 1):
        errs += [f"line {n}: {x.message}" for x in V["sb-progress/1"].iter_errors(e)]
    names = [e["ev"] for e in evs]
    if not names or (names[0] != "start" and set(names) != {"error"}):  # pre-start failures: only error events
        errs.append("first event must be start")
    if "done" in names and (names.count("done") != 1 or names[-1] != "done"):
        errs.append("done must be the single last event")
    phase = {}
    for e in evs:
        if e["ev"] == "phase":
            phase[e["test"]] = e["name"]
        if e["ev"] == "tick" and phase.get(e["test"]) != "measure":
            errs.append(f"tick outside measure (test {e['test']}, t={e['t']})")
        if e["ev"] == "progress" and phase.get(e["test"]) != e["phase"]:
            errs.append(f"progress for {e['phase']} while phase is {phase.get(e['test'])}")
        if e["ev"] == "progress" and e["done"] > e["total"]:
            errs.append("progress done > total")
    return errs


def check(path):
    p = Path(path); errs = []
    if p.suffix == ".jsonl":
        errs = _stream_errs(p.read_text(encoding="utf-8"))
    elif p.suffix == ".zip":
        z = zipfile.ZipFile(p)
        man = json.loads(z.read("manifest.json"))
        errs = _doc_errs(man)
        listed = {f["path"] for f in man["files"]}
        if listed != set(z.namelist()) - {"manifest.json"}:
            errs.append(f"manifest/zip mismatch: {sorted(listed ^ (set(z.namelist()) - {'manifest.json'}))}")
        for f in man["files"]:
            b = z.read(f["path"])
            if hashlib.sha256(b).hexdigest() != f["sha256"] or len(b) != f["size_bytes"]:
                errs.append(f"{f['path']}: sha256/size mismatch")
            if f["path"].endswith(".json") and not f["path"].startswith("extra/"):
                errs += [f"{f['path']}: {e}" for e in _doc_errs(json.loads(b))]
            if f["path"].endswith(".jsonl"):
                errs += [f"{f['path']}: {e}" for e in _stream_errs(b.decode("utf-8"))]
    else:
        errs = _doc_errs(json.loads(p.read_text(encoding="utf-8")))
    print(("[OK]  " if not errs else "[FAIL]"), path)
    for e in errs[:20]:
        print("       -", e)
    return not errs


if __name__ == "__main__":
    print(f"contract: {sb_contract.base_dir()}")
    ok = all([check(a) for a in sys.argv[1:]])
    sys.exit(0 if ok else 1)
