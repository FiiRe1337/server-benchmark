"""Locate the sbench interface contract (JSON Schemas) and build the validator set.

Base: FiiRe1337/server-benchmark@main, contract 1.1 (PR #3, cc46005): sb-job/1, sb-result/1, sb-progress/1,
sb-info/1, sb-validate/1, sb-runs/1. sb-bundle/1 is not on main yet (defined on feat/core-m1); a copy lives in
contract/ and is patched to what docs/cli.md 1.1 describes for 'sbench bundle' (runs/*.jsonl, extra/<ordner>/,
anonymized, includes). Marked PENDING; delete once the core branch updates the bundle schema.

Schema lookup order: $SBENCH_SCHEMA_DIR, <repo>/schema (GUI lives in <repo>/gui), ./upstream/repo/schema.
"""
import copy, json, os

HERE = os.path.dirname(os.path.abspath(__file__))
OVERLAY_ID = "pending-2026-10-20"


def base_dir():
    for d in (os.environ.get("SBENCH_SCHEMA_DIR"), os.path.join(HERE, "..", "schema"), os.path.join(HERE, "schema"), os.path.join(HERE, "upstream", "repo", "schema")):
        if d and os.path.isfile(os.path.join(d, "sb-job-1.schema.json")):
            return os.path.abspath(d)
    raise FileNotFoundError("sbench schemas not found (set SBENCH_SCHEMA_DIR)")


def _patch(fname, s):
    if fname == "sb-bundle-1.schema.json":
        it = s["properties"]["files"]["items"]["properties"]["path"]
        if "extra/" not in it["pattern"]:  # PENDING: cli.md 1.1 bundle layout
            it["pattern"] = it["pattern"][:-2] + "|runs/[^/\\\\]+\\.jsonl|extra/[^/\\\\]+/.+)$"
        s["properties"].setdefault("anonymized", {"type": "boolean", "description": "PENDING: --anonymize angewendet."})
        s["properties"].setdefault("includes", {"type": "array", "items": {"type": "string"}, "description": "PENDING: --include-Ordnernamen."})
    return s


def pending_dir():
    """Directory with main's schemas + sb-bundle/1 (patched). Regenerated when an input changes."""
    src = base_dir()
    out = os.path.join(HERE, "build", "schema-pending")
    os.makedirs(out, exist_ok=True)
    stamp = os.path.join(out, ".base")
    cdir = os.path.join(HERE, "contract")
    key = src + "|" + "|".join(f"{f}:{os.path.getmtime(os.path.join(d, f))}" for d in (src, cdir) for f in sorted(os.listdir(d))
                               if f.endswith(".json")) + f"|{os.path.getmtime(__file__)}"
    if os.path.exists(stamp) and open(stamp, encoding="utf-8").read() == key:
        return out
    for f in os.listdir(src):
        if f.endswith(".schema.json"):
            s = _patch(f, copy.deepcopy(json.load(open(os.path.join(src, f), encoding="utf-8"))))
            json.dump(s, open(os.path.join(out, f), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    for f in os.listdir(cdir):  # formats not on main yet (sb-bundle/1 from feat/core-m1)
        if f.endswith(".schema.json") and not os.path.exists(os.path.join(src, f)):
            s = _patch(f, json.load(open(os.path.join(cdir, f), encoding="utf-8")))
            json.dump(s, open(os.path.join(out, f), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    open(stamp, "w", encoding="utf-8").write(key)
    return out


def validators():
    """{schema-name: Draft202012Validator} for sb-job/1 ... incl. pending overlay. None if jsonschema missing."""
    try:
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource
    except ImportError:
        return None
    d = pending_dir()
    sch = {f: json.load(open(os.path.join(d, f), encoding="utf-8")) for f in os.listdir(d) if f.endswith(".schema.json")}
    reg = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in sch.values())
    return {f.replace(".schema.json", "").replace("-1", "/1"): Draft202012Validator(s, registry=reg) for f, s in sch.items()}
