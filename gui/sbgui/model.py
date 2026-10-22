"""Pure helpers on sb-*/1 documents (no Qt). Unknown major versions are rejected (docs/cli.md)."""
import json, re

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
METRIC_TXT = {"ops_per_s": ("Operationen", "ops/s"), "gbps": ("Bandbreite", "GB/s"), "latency_ns": ("Latenz", "ns"),
              "mbps": ("Durchsatz", "MB/s"), "latency_us": ("Latenz", "µs")}
# tick.metric values announced for M3 (network: mbps, ping: latency_us). Known, so tick_metric returns them; the live view
# has no dedicated mode yet and shows them in the event log only (no crash, no wrong chart)
FUTURE_METRICS = ("mbps", "latency_us")
WORKLOADS = ("int", "float", "crypto", "mixed")
MEM_OPS = ("read", "write", "copy")
SIMD = ("auto", "scalar", "sse2", "avx2", "avx512", "neon")
# FALLBACK ONLY for cores < 1.3 (contract 1.3: tick.score, summary.score_reference, meta.cpu_score in info + result).
# docs/core.md (1.2): ops/s of ONE thread on the reference machine (score = ops_per_s / ref * 1000).
CPU_REFERENCE = {"int": 2.8e8, "float": 1.5e8, "crypto": 3.4e6, "mixed": 3.8e5}
PHASE_TXT = {"preparing": "Vorbereitung", "warmup": "Warmup", "measure": "Messung"}
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
    """Which value a tick carries. Contract 1.3: tick.metric (always emitted). Fallback for older cores: storage ticks
    always have iops; otherwise the first present of ops_per_s/gbps/latency_ns; then the job config of that test."""
    m = ev.get("metric")
    if m in ("iops",) + METRICS + FUTURE_METRICS:
        return m
    if isinstance(m, str) and isinstance(ev.get(m), (int, float)):
        return m                      # unknown future metric that names a numeric field of the tick: trust it
    if "iops" in ev:
        return "iops"
    for k in METRICS:
        if k in ev:
            return k
    return expected_metric(cfg)


def sval(d, *path):
    """Null-safe nested get: contract 1.3 partial tests (stopped in preparing/warmup) have summary values null."""
    for k in path:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


# Run outcome. The exit code is authoritative (Devend): 0 ok, 1 runtime error, 2 invalid job, 3 stopped (sbench stop /
# Ctrl+C), 4 missing privileges. `aborted` alone is ambiguous: the 1.3 core writes aborted: true on a runtime error too,
# 1.3.1 (#17) only on stop. Sources: done.exit_code / <data_dir>/runs/index.jsonl {run_id, exit_code}.
EXIT_OUTCOME = {0: "ok", 1: "failed", 2: "invalid", 3: "aborted", 4: "privileges"}
OUTCOME_EXIT = {v: k for k, v in EXIT_OUTCOME.items()}
OUTCOME_TXT = {"ok": "vollständig", "failed": "Laufzeitfehler", "aborted": "abgebrochen", "invalid": "ungültiger Job",
               "privileges": "fehlende Rechte"}


def _has_run_error(res):
    """Heuristic without exit code. 1.3.1 (#17): an errors[] entry with fatal: true (at most one, in the last, partial test).
    1.3 core (no fatal flag; aborted: true on errors too): errors[] in a partial test = the error that ended the run.
    Plain storage I/O errors of a complete test do not make the run 'failed'."""
    tests = (res or {}).get("tests") or []
    errs = [(t, e) for t in tests for e in (t.get("errors") or []) if isinstance(e, dict)]
    if any(e.get("fatal") for _, e in errs):
        return True
    return any(is_partial(t, res) for t, _ in errs)


def run_outcome(res, exit_code=None):
    """'ok' | 'aborted' | 'failed' | 'invalid' | 'privileges'. Prefers the exit code; falls back to the result:
    errors => failed (even with aborted: true, 1.3 core), aborted => aborted, else ok."""
    if exit_code is not None and int(exit_code) in EXIT_OUTCOME:
        return EXIT_OUTCOME[int(exit_code)]
    if _has_run_error(res):
        return "failed"
    return "aborted" if (res or {}).get("aborted") else "ok"


def done_exit_code(ev):
    """Contract 1.3.1 (#17 a4287fa): optional done.exit_code, integer 0-4 (in practice 0/1/3). Anything else (missing,
    bool, float, string, out of range) -> None, i.e. the caller falls back to the derivation from the result."""
    c = (ev or {}).get("exit_code")
    return c if type(c) is int and c in EXIT_OUTCOME else None


def fallback_exit_code(res):
    """Exit code when index.jsonl has none (e.g. re-attach before the core wrote it): 1 / 3 / 0 from run_outcome."""
    return OUTCOME_EXIT[run_outcome(res)]


def read_run_index(path):
    """{run_id: exit_code} from <data_dir>/runs/index.jsonl (last entry per run wins); {} if missing/unreadable."""
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                try:
                    e = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(e, dict) and e.get("run_id") and isinstance(e.get("exit_code"), int):
                    out[e["run_id"]] = e["exit_code"]
    except OSError:
        pass
    return out


def is_partial(t, res=None):
    """Contract 1.3: test.partial. Older results: only the last test of an aborted run may be partial (unknown)."""
    if "partial" in t:
        return bool(t["partial"])
    return bool(res and res.get("aborted") and res["tests"] and res["tests"][-1] is t)


def has_values(t):
    """False for a 1.3 partial test that ended before measure (summary values null, per_worker/timeseries empty)."""
    if t.get("partial") and t.get("stopped_in") in ("preparing", "warmup"):
        return False
    s = t.get("summary") or {}
    if t["type"] == "storage":
        return s.get("iops") is not None
    if t["type"] in COMPUTE_TYPES:
        return summary_metric(t)[1] is not None
    return True


def partial_text(t, res=None):
    """'' for complete tests; 'abgebrochen in Warmup · keine Messwerte' etc. for partial ones."""
    if not is_partial(t, res):
        return ""
    ph = t.get("stopped_in")
    txt = f"abgebrochen in {PHASE_TXT.get(ph, ph)}" if ph else "abgebrochen · Teilergebnis"
    if not has_values(t):
        txt += " · keine Messwerte"
    elif ph == "measure":
        txt += " · Werte über die gemessene Zeit"
    return txt


def score_reference(workload, meta=None, summary=None):
    """(reference ops/s, from_core). Core values first (1.3: summary.score_reference, meta.cpu_score); docs fallback."""
    ref = sval(summary, "score_reference") or sval(meta, "cpu_score", "ref_ops_per_s", workload)
    if ref:
        return ref, True
    return CPU_REFERENCE.get(workload), False


def worker_score(ops_per_s, workload, meta=None, summary=None):
    """Per-thread CPU score, exactly like the core: ops_per_s / ref × points (points 1000) with ref from the core
    (meta.cpu_score.ref_ops_per_s / summary.score_reference). None without a core reference (no estimate per thread)."""
    ref, from_core = score_reference(workload, meta, summary)
    if not from_core or not ref or not isinstance(ops_per_s, (int, float)):
        return None
    return ops_per_s / ref * (sval(meta, "cpu_score", "points") or 1000)


# Runtime errors (errors[] of a test, error events). Fixed German texts only where agreed with the core; all other codes
# are shown generically as code + msg (docs/cli.md: new codes may appear without a contract change)
ERROR_TXT = {"E_ACCESS_DENIED": "Adminrechte nötig", "E_OUT_OF_MEMORY": "Nicht genug Arbeitsspeicher"}


def fatal_errors(t):
    """errors[] entries that ended the test. 1.3.1 marks them with fatal: true; without the flag every entry counts."""
    errs = [e for e in (t.get("errors") or []) if isinstance(e, dict)]
    if any("fatal" in e for e in errs):
        return [e for e in errs if e.get("fatal")]
    return errs


def error_text(e):
    """'Adminrechte nötig (E_ACCESS_DENIED)' for agreed codes, else 'E_IO: <msg>'; × count when aggregated."""
    code, msg = e.get("code", "E_?"), (e.get("msg") or "").strip()
    txt = f"{ERROR_TXT[code]} ({code})" if code in ERROR_TXT else (f"{code}: {msg}" if msg else code)
    extra = [f"Worker {e['worker']}" if e.get("worker") is not None else "", f"Ziel {e['target']}" if e.get("target") is not None else "",
             f"t = {e['t']:g} s".replace(".", ",") if isinstance(e.get("t"), (int, float)) else "",
             f"{e['count']}×" if (e.get("count") or 1) > 1 else ""]
    extra = [x for x in extra if x]
    return txt + (f" · {' · '.join(extra)}" if extra else "")


def test_cpus(t):
    """Contract 1.3.1: cpus[] per test in the result ({id, cpu, group, number, numa_node, core, smt_sibling_of}) -> dict by
    worker id. 1.3 results have none (summary.cpus is a plain list of logical processors without SMT information)."""
    out = {}
    for i, c in enumerate(t.get("cpus") or []):
        if isinstance(c, dict):
            out[c.get("id", i)] = c
    return out


def leftover_targets(t):
    """Storage targets[] that sbench created but did not delete (e.g. --keep-files or delete failed)."""
    return [g for g in (t.get("targets") or []) if g.get("created") and not g.get("deleted")]


# W_* texts. Import-icf (#15, docs/cli.md 'sbench import-icf'): German short texts; msg (with ICF line numbers) follows
WARN_TXT = {
    "W_ICF_RUN_TIME": "Laufzeit 0/fehlend → 60 s",
    "W_ICF_DISK_SIZE": "Dateigröße 0 → Testdatei 8 GiB",
    "W_ICF_ALIGN": "Ausrichtung 0 → an der Blockgröße",
    "W_ICF_REPLY": "Reply-Größe (nur Netzwerk) ignoriert",
    "W_ICF_CYCLING": "Cycling-Testtyp → ein Durchgang",
    "W_ICF_QD_MIXED": "unterschiedliche Queue Depth → Wert des ersten Workers",
    "W_ICF_SPEC_MIXED": "unterschiedliche Access Specs → Specs des ersten Workers",
    "W_ICF_WORKER_SPLIT": "Worker mit mehreren Zielen → ein Worker je Ziel",
    "W_ICF_TARGET_LAYOUT": "ungleiche Worker je Ziel → reihum verteilt",
    "W_ICF_MANAGERS": "mehrere Manager → nur der erste",
    "W_ICF_NETWORK_WORKER": "Netzwerk-Worker ignoriert",
    "W_ICF_DATA_PATTERN": "Datenmuster → sbench schreibt Zufallsdaten",
    "W_ICF_SECTOR": "Größe/Ausrichtung kein Vielfaches von 512",
    "W_ICF_ZERO_WEIGHT": "Zeile mit 0 % weggelassen",
    "W_ICF_NO_TARGET": "kein Ziel → Platzhalter D:\\sbtest.dat (8 GiB)",
    "W_ICF_NO_WORKERS": "keine Manager-Liste → 1 Worker je Ziel, QD 1",
    "W_ICF_NO_ASSIGNMENT": "Specs ohne Zuweisung → alle Specs",
    "W_ICF_UNSUPPORTED": "Einstellung ohne Entsprechung",
    "W_ICF_VALIDATE": "Job-Prüfung: vor dem Lauf anpassen",
}


def warning_text(w):
    """'<German short text> – <msg>' for known W_* codes, else the core's msg."""
    t = WARN_TXT.get(w.get("code"))
    msg = (w.get("msg") or "").strip()
    return f"{t} – {msg}" if t and msg else (t or msg or w.get("code", ""))


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


def estimated_score(workload, ops_per_s, meta=None):
    """Fallback for cores < 1.3 without tick.score: ops_per_s / reference * 1000 (reference from meta.cpu_score if known)."""
    ref, _ = score_reference(workload, meta)
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
FLAT_COLUMNS_13 = FLAT_COLUMNS_12 + ["partial", "stopped_in"]                       # contract 1.3


def read_flat_csv(path_or_text):
    """Parse `sbench export --style flat` (contract 1.1: 14 columns, 1.2: 18 (+ ops_per_s,gbps,latency_ns,score),
    1.3: 20 (+ partial,stopped_in)). Columns are matched by NAME (old positions are stable, new ones are appended;
    unknown future columns are kept). Empty = no value -> None, 0 = measured 0. partial -> bool/None.
    Returns (columns, rows as dicts)."""
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
            if k == "partial":
                d[k] = {"true": True, "false": False}.get(v)
            elif k in ("type", "scope", "stopped_in"):
                d[k] = v if (v or k != "stopped_in") else None
            else:
                d[k] = _num(v)
        for k in FLAT_COLUMNS_13[len(FLAT_COLUMNS_11):]:
            d.setdefault(k, None)                  # 1.1/1.2 export: new columns absent -> None
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
