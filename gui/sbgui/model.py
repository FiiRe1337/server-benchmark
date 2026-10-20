"""Pure helpers on sb-*/1 documents (no Qt). Unknown major versions are rejected (docs/cli.md)."""
import re

SUPPORTED = {"sb-job/1", "sb-result/1", "sb-info/1", "sb-validate/1", "sb-runs/1", "sb-bundle/1"}
UNITS = {"KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}


class SchemaError(ValueError):
    pass


def check_schema(doc, expected):
    s = doc.get("schema", "")
    if s != expected:
        raise SchemaError(f"Format {s!r} nicht unterstützt (erwartet {expected})")
    return doc


def size_bytes(v):
    if isinstance(v, int):
        return v
    m = re.fullmatch(r"([1-9][0-9]*)\s?(KiB|MiB|GiB)", str(v).strip())
    if not m:
        raise ValueError(f"ungültige Größe {v!r}")
    return int(m.group(1)) * UNITS[m.group(2)]


def size_str(b):
    """Bytes -> canonical job string ("4KiB") when exact, else integer bytes."""
    for u in ("GiB", "MiB", "KiB"):
        if b % UNITS[u] == 0:
            return f"{b // UNITS[u]}{u}"
    return b


def short_size(b):
    for u, n in (("G", 1024**3), ("M", 1024**2), ("K", 1024)):
        if b >= n and b % n == 0:
            return f"{b // n}{u}"
    return f"{b}B"


def human_bytes(b, unit="GiB"):
    n = b / UNITS.get(unit, 1)
    return f"{n:,.0f} {unit}".replace(",", ".") if n >= 100 else f"{n:,.1f} {unit}".replace(",", "X").replace(".", ",").replace("X", ".")


def derived_spec_label(s):
    bs = short_size(size_bytes(s["block_size"]))
    pat = "rand" if s["random_pct"] >= 50 else "seq"
    rd = s["read_pct"]
    io = "read" if rd == 100 else "write" if rd == 0 else f"{rd}/{100 - rd}"
    return f"{bs} {pat} {io}"


def spec_label(s):
    """Contract 1.1: optional access_spec.name, fallback derived label."""
    return s.get("name") or derived_spec_label(s)


TYPE_TXT = {"cpu": "CPU", "memory": "RAM", "net_client": "Netz-Client", "net_server": "Netz-Server", "hyperv_info": "Hyper-V Info"}


def derived_test_label(cfg):
    t = cfg["type"]
    if t == "storage":
        specs = cfg["access_specs"]
        return spec_label(specs[0]) if len(specs) == 1 else "Mix: " + " + ".join(f"{s['weight']}% {spec_label(s)}" for s in specs)
    return TYPE_TXT[t]


def test_label(cfg):
    """Contract 1.1: optional test.name (also the key for comparison matching), fallback derived label."""
    return cfg.get("name") or derived_test_label(cfg)


def match_key(cfg):
    """Comparison matching: name if present, else derived label (stable across runs of the same profile)."""
    return test_label(cfg)


def writes(test):
    return any(s["read_pct"] < 100 for s in test.get("access_specs", []))


def destructive_targets(job):
    """[(test_idx, target_idx, target)] : write tests on volume/physical (need double opt-in)."""
    return [(i, k, tg) for i, t in enumerate(job["tests"]) if t["type"] == "storage" and writes(t)
            for k, tg in enumerate(t["targets"]) if tg["kind"] in ("volume", "physical")]


def numa_cpus(node):
    c = node.get("cpus")
    return len(c) if isinstance(c, list) else (c or 0)


def meta_get(meta, key, default=None):
    """Contract 1.1 fields are always emitted by sbench >= 1.1, but tolerate older cores / results."""
    return meta.get(key, default)


def host_label(meta):
    role = meta["hypervisor"]["role"]
    return f"{meta['host']} ({ {'host': 'Host', 'guest': 'VM', 'none': 'physisch'}[role] })"


def storage_tests(res):
    return [t for t in res["tests"] if t["type"] == "storage"]
