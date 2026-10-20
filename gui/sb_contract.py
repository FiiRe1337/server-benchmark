"""Locate the sbench interface contract (JSON Schemas) and build the validator set.

Single source: <repo>/schema on main (contract 1.1 incl. sb-bundle/1 since core M1, 5670020):
sb-job/1, sb-result/1, sb-progress/1, sb-info/1, sb-validate/1, sb-runs/1, sb-bundle/1.

Schema lookup order: $SBENCH_SCHEMA_DIR, <repo>/schema (GUI lives in <repo>/gui), <here>/schema (frozen EXE).
"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))


def base_dir():
    for d in (os.environ.get("SBENCH_SCHEMA_DIR"), os.path.join(HERE, "..", "schema"), os.path.join(HERE, "schema")):
        if d and os.path.isfile(os.path.join(d, "sb-job-1.schema.json")):
            return os.path.abspath(d)
    raise FileNotFoundError("sbench schemas not found (set SBENCH_SCHEMA_DIR)")


def validators():
    """{schema-name: Draft202012Validator} for sb-job/1 ... sb-bundle/1. None if jsonschema missing."""
    try:
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource
    except ImportError:
        return None
    d = base_dir()
    sch = {f: json.load(open(os.path.join(d, f), encoding="utf-8")) for f in os.listdir(d) if f.endswith(".schema.json")}
    reg = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in sch.values())
    return {f.replace(".schema.json", "").replace("-1", "/1"): Draft202012Validator(s, registry=reg) for f, s in sch.items()}
