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


def _threads_txt(n):
    return "alle Threads" if not n else ("1 Thread" if n == 1 else f"{n} Threads")


def derived_test_label(cfg):
    t = cfg["type"]
    if t == "storage":
        specs = cfg["access_specs"]
        return spec_label(specs[0]) if len(specs) == 1 else "Mix: " + " + ".join(f"{s['weight']}% {spec_label(s)}" for s in specs)
    if t == "cpu" and "workload" in cfg:                        # contract 1.2: workload/threads/numa_node
        parts = [f"CPU {cfg['workload']}", _threads_txt(cfg.get("threads", 0))]
        if "numa_node" in cfg:
            parts.append(f"Knoten {cfg['numa_node']}")
        return " · ".join(parts)
    if t == "memory" and "mode" in cfg:
        if cfg["mode"] == "latency":
            parts = [f"RAM-Latenz {short_size(size_bytes(cfg['buffer_size']))}"]
        else:
            op = cfg.get("op", "read") + (" NT" if cfg.get("nontemporal") else "")
            parts = [f"RAM {op} {short_size(size_bytes(cfg['buffer_size']))}", _threads_txt(cfg.get("threads", 0))]
            if cfg.get("simd", "auto") != "auto":
                parts.append(cfg["simd"])
        if "numa_node" in cfg or "mem_node" in cfg:
            parts.append(f"Knoten {cfg.get('numa_node', '*')}→{cfg.get('mem_node', 'lokal')}")
        return " · ".join(parts)
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


# ------------------------------------------------------------------ contract 1.2: cpu / memory (M2)
COMPUTE_TYPES = ("cpu", "memory")
METRICS = ("ops_per_s", "gbps", "latency_ns")          # tick / timeseries / per_worker value keys
METRIC_TXT = {"ops_per_s": ("Operationen", "ops/s"), "gbps": ("Bandbreite", "GB/s"), "latency_ns": ("Latenz", "ns")}
WORKLOADS = ("int", "float", "crypto", "mixed")
MEM_OPS = ("read", "write", "copy")
SIMD = ("auto", "scalar", "sse2", "avx2", "avx512", "neon")
# docs/core.md (contract 1.2): ops/s of ONE thread on the reference machine (score = ops_per_s / ref * 1000).
# Only used for a live *estimate*; the result carries the authoritative summary.score.
CPU_REFERENCE = {"int": 2.8e8, "float": 1.5e8, "crypto": 3.4e6, "mixed": 3.8e5}
RAM_METHOD_TXT = {"total": "gesamt (1 Knoten)", "srat": "ACPI SRAT", "estimate": "geschätzt"}


def compute_tests(res):
    return [t for t in res["tests"] if t["type"] in COMPUTE_TYPES]


def expected_metric(cfg):
    """Metric a cpu/memory test reports (tick, timeseries, per_worker): ops_per_s | gbps | latency_ns, None otherwise."""
    if not cfg:
        return None
    if cfg.get("type") == "cpu":
        return "ops_per_s"
    if cfg.get("type") == "memory":
        return "latency_ns" if cfg.get("mode") == "latency" else "gbps"
    return None


def tick_metric(ev, cfg=None):
    """Which value a tick carries. Ticks have no explicit kind field: storage ticks always have iops; otherwise the
    first present of ops_per_s/gbps/latency_ns; fallback = job config of that test (e.g. re-attach without values)."""
    if "iops" in ev:
        return "iops"
    for k in METRICS:
        if k in ev:
            return k
    return expected_metric(cfg)


def summary_metric(t):
    """(key, value) of the headline value of a cpu/memory result test."""
    s = t["summary"]
    if t["type"] == "cpu":
        return "ops_per_s", s.get("ops_per_s")
    k = "latency_ns" if s.get("mode") == "latency" else "gbps"
    return k, s.get(k)


def lower_is_better(key):
    return key in ("latency_ns", "lat_us", "p99")


def fmt_metric(key, v):
    """Human value for ops_per_s / gbps / latency_ns / score (German decimal comma)."""
    if v is None or v != v:
        return "–"
    if key == "ops_per_s":
        for div, suf in ((1e9, " G"), (1e6, " M"), (1e3, " k")):
            if abs(v) >= div:
                return _de(f"{v / div:.2f}") + suf
        return _de(f"{v:.0f}")
    if key == "gbps":
        return _de(f"{v:.1f}" if v >= 10 else f"{v:.2f}")
    if key == "latency_ns":
        return _de(f"{v:.1f}" if v < 100 else f"{v:.0f}")
    if key == "score":
        return f"{v:,.0f}".replace(",", ".")
    return _de(f"{v:g}")


def _de(txt):
    return txt.replace(".", ",")


def estimated_score(workload, ops_per_s):
    ref = CPU_REFERENCE.get(workload)
    return ops_per_s / ref * 1000 if ref and ops_per_s is not None else None


def compute_details(t):
    """Short config/summary facts for result tables: threads, SIMD, NT, NUMA placement, buffer."""
    s, c = t["summary"], t["config"]
    out = [_threads_txt(s.get("threads", c.get("threads", 0)))]
    if t["type"] == "cpu":
        out.insert(0, s.get("workload", c.get("workload", "mixed")))
    else:
        if s.get("mode") == "bandwidth":
            op = s.get("op", c.get("op", "read"))
            out.insert(0, op + (" NT" if s.get("nontemporal") else ""))
            if s.get("simd"):
                out.append(s["simd"])
        else:
            out.insert(0, "Latenz")
        if s.get("buffer_size_bytes"):
            out.append(f"{short_size(s['buffer_size_bytes'])}/Thread" if s.get("mode") == "bandwidth" else short_size(s["buffer_size_bytes"]))
    pl = numa_placement(t)
    if pl:
        out.append(pl)
    return out


def numa_placement(t):
    """'Knoten 0 → RAM 1 (remote)' style text from summary/config numa_node + mem_node (None if neither set)."""
    s, c = t["summary"], t["config"]
    n = s.get("numa_node", c.get("numa_node")); mn = s.get("mem_node", c.get("mem_node"))
    if n is None and mn is None:
        return None
    if t["type"] == "cpu" or mn is None:
        return f"Knoten {n}"
    return f"Knoten {'*' if n is None else n} → RAM {mn}" + (" (remote)" if n is not None and n != mn else " (lokal)" if n == mn else "")


# ------------------------------------------------------------------ CSV (sbench export --style flat)
FLAT_COLUMNS_11 = ["test", "type", "scope", "id", "iops", "mbps", "read_iops", "write_iops", "read_mbps", "write_mbps",
                   "lat_mean_us", "lat_p99_us", "lat_max_us", "errors"]
FLAT_COLUMNS_12 = FLAT_COLUMNS_11 + ["ops_per_s", "gbps", "latency_ns", "score"]   # contract 1.2: appended at the end


def read_flat_csv(path_or_text):
    """Parse `sbench export --style flat` (contract 1.1: 14 columns, 1.2: + ops_per_s,gbps,latency_ns,score).
    Columns are matched by NAME (positions of old columns are stable, new ones are appended; unknown future columns
    are kept). Numbers -> float/int, empty -> None. Returns (columns, rows as dicts)."""
    import csv, io, os
    txt = open(path_or_text, encoding="utf-8-sig").read() if os.path.exists(str(path_or_text)) else str(path_or_text)
    rd = csv.reader(io.StringIO(txt))
    cols = next(rd, [])
    if cols[:len(FLAT_COLUMNS_11)] != FLAT_COLUMNS_11:
        raise ValueError(f"kein flat-CSV von sbench export: {cols[:4]}")
    rows = []
    for r in rd:
        if not r:
            continue
        d = {}
        for k, v in zip(cols, r + [""] * (len(cols) - len(r))):
            d[k] = _num(v) if k not in ("type", "scope") else v
        for k in FLAT_COLUMNS_12[len(FLAT_COLUMNS_11):]:
            d.setdefault(k, None)                  # 1.1 export: new columns absent -> None
        rows.append(d)
    return cols, rows


def _num(v):
    if v == "":
        return None
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v
