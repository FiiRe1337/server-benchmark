#!/usr/bin/env python3
r"""Mock of the core CLI `sbench.exe`, implemented against the contract in
FiiRe1337/server-benchmark, contract 1.3 (main 020a2de, PR #16: docs/cli.md "Vertrag 1.3", schema/,
schema/examples/*-partial.*): tick.metric/score, phase.threads/cpus, tests[].partial/stopped_in (null summary values
when stopped before measure), meta.cpu_score, summary.score_reference/proc_cpu_pct, timeseries at --tick-ms, tick_ms,
CSV partial/stopped_in. Contract 1.2 (core M2, #13) / 1.1 (core M1 5670020) via SBENCH_MOCK_CONTRACT.

  sbench run --job <job.json> --out <result.json> [--progress stdout|file:<p.jsonl>|pipe:\\.\pipe\sbench-<id>]
             [--tick-ms <ms>=1000, min 100] [--id <id>] [--allow-destructive]
  sbench stop --id <id>
  sbench list --running --json        -> sb-runs/1
  sbench info --json                  -> sb-info/1
  sbench validate --job <f>           -> sb-validate/1 (errors[] + warnings[]), exit 0/2
  sbench export <result.json> --csv <out.csv> [--style iometer|flat]
  sbench bundle --out <zip> [--results ...] [--all] [--include <dir>]... [--anonymize] [--progress ...]
  sbench import-icf <file.icf> --out <job.json>   (M2; prints sb-validate/1 with W_ICF_* warnings, see import_icf)
  sbench version [--json]             -> --json: {version, commit, arch, sha256, formats} (PR #10)
Global option (every command, before or after the command name): --data-dir <path> (uncreatable -> exit 2, E_ARGS).

Every run additionally writes <data_dir>/runs/<run_id>.jsonl (all events) and runs/<run_id>.lock.json
(while running) so a restarted GUI can re-attach; finished runs are appended to runs/index.jsonl.
data_dir: --data-dir, else $SBENCH_DATA_DIR, else data\ next to sbench (writable), else %ProgramData%\sbench.
Exit codes: 0 ok, 1 runtime error, 2 invalid job/args, 3 aborted, 4 missing privileges.
Mock knobs (env): SBENCH_MOCK_SPEED (time compression), SBENCH_MOCK_HOST (host|vm|vm-san),
SBENCH_MOCK_ELEVATED=1 (pretend to be elevated), SBENCH_MOCK_CONTRACT=1.3|1.2|1.1 (default 1.3; 1.2 = core M2: cpu + memory
implemented, net_*/hyperv_info E_NOT_IMPLEMENTED, no 1.3 fields, 18 CSV columns; 1.1 = core M1: every non-storage test
E_NOT_IMPLEMENTED + skipped, no ram_method, 14 CSV columns), SBENCH_MOCK_LAG=1 (1.3: storage worker 0 misses the tick
ack now and then -> 0 in that tick, recounted next), SBENCH_MOCK_STOP_AT=<test>:<phase>[:<n>] (deterministic stop),
SBENCH_MOCK_FAIL_AT=<test>:<E_CODE>[:<ticks>[:<entries>]] (runtime error after <ticks> measure ticks, 0 = in preparing;
the test becomes partial, errors[] gets <entries> entries, later tests are absent, exit 1), SBENCH_MOCK_CONTRACT=1.3.1
(cpus[] per test + one errors[].fatal + done.exit_code; valid against the #17 schema in
gui/tests/fixtures/contract-1.3.1), run --keep-files (storage targets[] deleted=false),
SBENCH_MOCK_ABORT_ON_ERROR=1 (runtime error like the real 1.3 core: aborted: true, exit 1; default = 1.3.1: aborted: false),
SBENCH_MOCK_ALL_TYPES=1 (additionally simulate net_*/hyperv_info), SBENCH_DATA_DIR,
SBENCH_SCHEMA_DIR.
Stop: real core uses the named event Global\sbench-stop-<id> (fallback Local\); the mock uses a marker file.
"""
import argparse, copy, csv, datetime as dt, hashlib, json, math, os, random, re, signal, sys, time, zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sb_contract  # noqa: E402

VERSION = "0.1.0-mock"
FORMATS = ["sb-job/1", "sb-result/1", "sb-progress/1", "sb-info/1", "sb-validate/1", "sb-bundle/1", "sb-runs/1"]
HERE = os.path.dirname(os.path.abspath(__file__))


def _data_dir():
    """cli.md 1.1: SBENCH_DATA_DIR -> data\\ next to sbench.exe (if writable) -> %ProgramData%\\sbench."""
    if os.environ.get("SBENCH_DATA_DIR"):
        return os.path.abspath(os.environ["SBENCH_DATA_DIR"])
    d = os.path.join(HERE, "data")
    try:
        os.makedirs(d, exist_ok=True)
        if os.access(d, os.W_OK):
            return d
    except OSError:
        pass
    return os.path.join(os.environ.get("ProgramData", "/var/tmp"), "sbench")


DATA = _data_dir()
SPEED = float(os.environ.get("SBENCH_MOCK_SPEED", "1"))
HOSTKIND = os.environ.get("SBENCH_MOCK_HOST", "host")
PERF = {"host": (1.0, 1.0), "vm": (0.78, 1.25), "vm-san": (0.42, 2.4)}[HOSTKIND]  # IOPS factor, latency factor
SCHEMA_DIR = sb_contract.base_dir()
ELEVATED = os.environ.get("SBENCH_MOCK_ELEVATED") == "1"
ALL_TYPES = os.environ.get("SBENCH_MOCK_ALL_TYPES") == "1"  # simulate cpu/memory/net/hyperv_info regardless of contract
CONTRACT = os.environ.get("SBENCH_MOCK_CONTRACT", "1.3")
if CONTRACT not in ("1.1", "1.2", "1.3", "1.3.1"):
    raise SystemExit(f"SBENCH_MOCK_CONTRACT={CONTRACT!r}: 1.1, 1.2, 1.3 oder 1.3.1")
M2 = CONTRACT in ("1.2", "1.3", "1.3.1")
V13 = CONTRACT in ("1.3", "1.3.1")
# 1.3.1 (PR #17 c4b9d69, not on main yet -> output does NOT validate against main's 1.3 schema, but against
# gui/tests/fixtures/contract-1.3.1/schema): cpus[] per cpu/memory test, errors[].fatal on exactly one entry of the last
# (partial) test, aborted: false + exit 1 on a runtime error, done.exit_code (#17 a4287fa)
V131 = CONTRACT == "1.3.1"
LAG = os.environ.get("SBENCH_MOCK_LAG") == "1"
ABORT_ON_ERROR = os.environ.get("SBENCH_MOCK_ABORT_ON_ERROR") == "1"   # emulate the real 1.3 core: aborted: true on runtime error
IMPLEMENTED = {"storage"} | ({"cpu", "memory"} if M2 else set()) | ({"cpu", "memory", "net_client", "net_server", "hyperv_info"} if ALL_TYPES else set())
EXIT_OK, EXIT_ERR, EXIT_INVALID, EXIT_ABORTED, EXIT_PRIV = 0, 1, 2, 3, 4
UNITS = {"KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}
GIB = 1024**3


def parse_size(v):
    if isinstance(v, int):
        return v
    m = re.fullmatch(r"([1-9][0-9]*)\s?(KiB|MiB|GiB)", str(v))
    if not m:
        raise ValueError(v)
    return int(m.group(1)) * UNITS[m.group(2)]


def utc_now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ----------------------------------------------------------------------------- info
# neutral demo names (agreed with Devend): hosts DEMO-*, IPs from RFC 5737 (192.0.2.x servers, 198.51.100.x second net).
# Applied to the example info.json too, so the mock output stays neutral whatever schema/examples contains.
DEMO_NAMES = [("SRV-HV01", "DEMO-HV01"), ("SRV-HV02", "DEMO-HV02"), ("SRV-SQL01", "DEMO-SQL01"), ("WIN-SRV01", "DEMO-VM01"),
              ("10.0.0.10", "192.0.2.10"), ("192.168.10.21", "198.51.100.21")]


def _demo_names(txt):
    for a, b in DEMO_NAMES:
        txt = re.sub(re.escape(a), lambda m, b=b: b.lower() if m.group(0).islower() else b, txt, flags=re.I)
    return txt


def _example_info():
    for p in (os.path.join(SCHEMA_DIR, "examples", "info.json"), os.path.join(HERE, "demo", "info-host-example.json")):
        if os.path.exists(p):
            return json.loads(_demo_names(open(p, encoding="utf-8").read()))
    raise SystemExit("info example missing")


def info_doc():
    """Host = Devend's example info.json; VM variants derived from it."""
    info = _example_info()
    info.pop("_example", None)
    m = info["meta"]
    m["elevated"] = ELEVATED
    m["data_dir"] = os.path.abspath(DATA)
    if HOSTKIND == "host":
        if "numa_nodes" not in m:  # contract 1.0 example
            s = m["cpu"]["sockets"]
            m["ram_free_bytes"] = int(m["ram_bytes"] * 0.81)
            n = m["cpu"]["threads"] // s  # final contract: cpus = list of global indices (group * 64 + bit)
            m["numa_nodes"] = [{"id": i, "cpus": list(range(i * n, (i + 1) * n)), "ram_bytes": m["ram_bytes"] // s,
                                "ram_free_bytes": int(m["ram_bytes"] // s * (0.78 + 0.06 * i))} for i in range(s)]
        return _ram_method(info)
    vm = copy.deepcopy(info)
    m = vm["meta"]
    m["host"] = "DEMO-VM01" if HOSTKIND == "vm" else "DEMO-VM02"
    m["cpu"].update(sockets=1, cores=8, threads=16)
    m["ram_bytes"] = 64 * GIB
    m["ram_free_bytes"] = 41 * GIB
    m["numa_nodes"] = [{"id": 0, "cpus": list(range(16)), "ram_bytes": 64 * GIB, "ram_free_bytes": 41 * GIB, "ram_method": "total"}]
    m["hypervisor"] = {"present": True, "vendor": "Microsoft Hv", "role": "guest", "partition_info": {"is_root": False}}
    m["drives"] = [{"path": "\\\\.\\PhysicalDrive0", "model": "Msft Virtual Disk", "size_bytes": 136365211648, "bus": "SCSI",
                    "sector_logical": 512, "sector_physical": 4096},
                   {"path": "\\\\.\\PhysicalDrive1", "model": "Msft Virtual Disk" if HOSTKIND == "vm" else "Msft Virtual Disk (CSV/SAN)",
                    "size_bytes": 1099511627776, "bus": "SCSI", "sector_logical": 512, "sector_physical": 4096}]
    m["nics"] = [{"name": "Microsoft Hyper-V Network Adapter", "ipv4": ["192.0.2.31"], "ipv6": [], "link_mbps": 10000}]
    vm["targets"] = {"volumes": [{"path": "\\\\.\\C:", "letter": "C", "label": "System", "fs": "NTFS", "size_bytes": 136365211648 - 2**29,
                                  "free_bytes": 81604378624},
                                 {"path": "\\\\.\\D:", "letter": "D", "label": "Daten", "fs": "ReFS", "size_bytes": 1099511627776 - 2**30,
                                  "free_bytes": 912680550400}],
                     "physical_disks": m["drives"], "nics": m["nics"]}
    return _ram_method(vm)


def _ram_method(info):
    """Contract 1.2 (#8): numa_nodes[].ram_method (total|srat|estimate); contract 1.1 core: field absent.
    Contract 1.3: meta.cpu_score (formula + reference ops/s per workload); absent before."""
    if V13:
        info["meta"].setdefault("cpu_score", {"formula": "score = ops_per_s / ref_ops_per_s[workload] * 1000", "points": 1000,
                                              "reference_threads": 1, "ref_ops_per_s": dict(CPU_REF)})
    else:
        info["meta"].pop("cpu_score", None)
    for n in info["meta"].get("numa_nodes", []):
        if not M2:
            n.pop("ram_method", None)
        else:
            n.setdefault("ram_method", "total" if len(info["meta"]["numa_nodes"]) == 1 else "srat")
    return info


# ----------------------------------------------------------------------------- validate
def _schema_validator():
    v = sb_contract.validators()
    return v and v["sb-job/1"]


def writes(t):
    return any(s["read_pct"] < 100 for s in t.get("access_specs", []))


def _vol_free(path, vols):
    m = re.match(r"^([A-Za-z]):", path)
    for v in vols:
        if m and v["letter"] == m.group(1).upper():
            return v["free_bytes"]
    return None


def _ptr(parts):
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def validate(job):
    """-> (errors, warnings); warnings are reported with ok=true as well."""
    errs, warns = [], []
    v = _schema_validator()
    if v is not None:
        for e in sorted(v.iter_errors(job), key=lambda e: list(e.absolute_path)):
            errs.append({"path": _ptr(e.absolute_path), "code": "E_SCHEMA", "msg": f"Schemaverstoß: {e.message}"})
    if errs:
        return errs, warns
    info = info_doc()
    sectors = {d["path"]: d.get("sector_logical", 512) for d in info["targets"]["physical_disks"]}
    vols = info["targets"]["volumes"]
    for i, t in enumerate(job.get("tests", [])):
        if t.get("type") in ("cpu", "memory") and M2:
            validate_compute(i, t, info["meta"], errs, warns)
            continue
        if t.get("type") != "storage":
            if t.get("type") == "net_client":
                udp_tp = t.get("proto", "tcp") == "udp" and t.get("mode", "throughput") == "throughput"
                if ("udp_rate_mbps" in t) != udp_tp:
                    errs.append({"path": f"/tests/{i}/udp_rate_mbps", "code": "E_UDP_RATE",
                                 "msg": "udp_rate_mbps nur bei UDP-Durchsatztest angeben (dort Pflicht)."})
            continue
        ws = sum(s["weight"] for s in t["access_specs"])
        if ws != 100:
            errs.append({"path": f"/tests/{i}/access_specs", "code": "E_WEIGHT_SUM", "msg": f"Summe der Gewichte ist {ws}, erwartet 100."})
        min_bs = min(parse_size(s["block_size"]) for s in t["access_specs"])
        for k, tg in enumerate(t["targets"]):
            if tg["kind"] == "file":
                d = os.path.dirname(tg["path"])
                if os.name == "nt" and d and not os.path.isdir(d):
                    errs.append({"path": f"/tests/{i}/targets/{k}/path", "code": "E_TARGET_NOT_FOUND", "msg": f"Verzeichnis {d} existiert nicht."})
                if parse_size(tg["file_size"]) < min_bs:
                    errs.append({"path": f"/tests/{i}/targets/{k}/file_size", "code": "E_TARGET_SIZE", "msg": "file_size ist kleiner als block_size."})
                free = _vol_free(tg["path"], vols)
                if free is not None and parse_size(tg["file_size"]) > free * 0.9:
                    warns.append({"path": f"/tests/{i}/targets/{k}/file_size", "code": "W_LOW_DISK_SPACE",
                                  "msg": f"Testdatei ({parse_size(tg['file_size']) / GIB:.0f} GiB) belegt mehr als 90 % des freien Platzes ({free / GIB:.0f} GiB)."})
            elif tg["kind"] == "physical" and tg["path"] not in sectors:
                errs.append({"path": f"/tests/{i}/targets/{k}/path", "code": "E_TARGET_NOT_FOUND", "msg": f"{tg['path']} existiert nicht."})
            if not ELEVATED:
                if tg["kind"] in ("volume", "physical"):
                    warns.append({"path": f"/tests/{i}/targets/{k}", "code": "W_NEEDS_ADMIN",
                                  "msg": f"{tg['path']} erfordert Administratorrechte; 'sbench run' ohne Erhöhung endet mit Exit-Code 4."})
                else:
                    warns.append({"path": f"/tests/{i}/targets/{k}/file_size", "code": "W_NEEDS_ADMIN",
                                  "msg": "Ohne Administratorrechte kein SetFileValidData; die Testdatei wird langsamer durch Schreiben angelegt."})
            if tg["kind"] in ("volume", "physical") and writes(t):
                if tg.get("allow_destructive"):
                    warns.append({"path": f"/tests/{i}/targets/{k}", "code": "W_DESTRUCTIVE",
                                  "msg": f"Schreibtest auf {tg['path']} zerstört alle Daten auf dem Ziel; 'sbench run --allow-destructive' erforderlich."})
                else:
                    errs.append({"path": f"/tests/{i}/targets/{k}/allow_destructive", "code": "E_DESTRUCTIVE_NOT_ALLOWED",
                                 "msg": f"Schreibtest auf {tg['path']} ({tg['kind']}) ohne allow_destructive: true."})
        if t.get("cache", "unbuffered") == "unbuffered":
            for j, s in enumerate(t["access_specs"]):
                for key in ("block_size", "align"):
                    if key in s and parse_size(s[key]) % 512:
                        errs.append({"path": f"/tests/{i}/access_specs/{j}/{key}", "code": "E_BLOCK_ALIGN",
                                     "msg": f"{key} {parse_size(s[key])} ist kein Vielfaches der logischen Sektorgröße (512 Bytes); bei cache = unbuffered erforderlich."})
    return errs, warns


# ----------------------------------------------------------------------------- cpu / memory (contract 1.2)
ARCH = "x64"
SIMD_OK = {"x64": ("scalar", "sse2", "avx2", "avx512"), "arm64": ("scalar", "neon")}[ARCH]  # mock CPU: Xeon Gold 6338 (AVX-512F)


def nodes_of(meta):
    return {n["id"]: n for n in meta.get("numa_nodes", [])} or {0: {"id": 0, "cpus": list(range(meta["cpu"]["threads"])),
                                                                     "ram_free_bytes": meta.get("ram_free_bytes", meta["ram_bytes"])}}


def thread_cpus(meta, threads, numa_node=None):
    """docs/cli.md 1.2: one logical CPU per physical core first, NUMA nodes alternating (or only numa_node), then the
    SMT siblings; more threads than CPUs wrap around (W_OVERSUBSCRIBED). Mock topology: siblings = (2k, 2k+1)."""
    nodes = nodes_of(meta)
    sel = [nodes[numa_node]] if numa_node is not None else [nodes[k] for k in sorted(nodes)]
    smt = meta["cpu"]["threads"] > meta["cpu"]["cores"]
    cores = [n["cpus"][0::2] if smt else n["cpus"] for n in sel]
    sibs = [n["cpus"][1::2] if smt else [] for n in sel]
    order = []
    for group in (cores, sibs):
        for k in range(max((len(g) for g in group), default=0)):
            order += [g[k] for g in group if k < len(g)]
    n = threads or len(order)
    return [order[i % len(order)] for i in range(n)]


def cpu_entry(meta, i, c):
    """Contract 1.3 phase.cpus[] entry. Mock topology: SMT siblings = (2k, 2k+1), core = c // 2."""
    smt = meta["cpu"]["threads"] > meta["cpu"]["cores"]
    return {"id": i, "cpu": c, "group": c // 64, "number": c % 64, "numa_node": node_of(meta, c), "core": c // 2 if smt else c,
            "smt_sibling_of": (c - 1) if smt and c % 2 else None}


def node_of(meta, cpu):
    for nid, n in nodes_of(meta).items():
        if cpu in n["cpus"]:
            return nid
    return 0


def mem_threads(t, meta):
    if t.get("mode") == "latency":
        return 1
    return t.get("threads", 0) or len(nodes_of(meta)[t["numa_node"]]["cpus"] if "numa_node" in t else thread_cpus(meta, 0))


def validate_compute(i, t, meta, errs, warns):
    """Semantic rules of contract 1.2 (docs/cli.md: E_NUMA_NODE, E_SIMD_UNSUPPORTED, E_BUFFER_SIZE, E_OUT_OF_MEMORY + W_*)."""
    nodes = nodes_of(meta)
    with_cpus = {k for k, n in nodes.items() if n.get("cpus")}
    if "numa_node" in t and t["numa_node"] not in with_cpus:
        errs.append({"path": f"/tests/{i}/numa_node", "code": "E_NUMA_NODE",
                     "msg": f"NUMA-Knoten {t['numa_node']} hat keine logischen Prozessoren (vorhanden: {sorted(with_cpus)})."})
        return
    if "mem_node" in t and t["mem_node"] not in nodes:
        errs.append({"path": f"/tests/{i}/mem_node", "code": "E_NUMA_NODE", "msg": f"NUMA-Knoten {t['mem_node']} existiert nicht."})
        return
    logical = len(nodes[t["numa_node"]]["cpus"]) if "numa_node" in t else sum(len(n["cpus"]) for n in nodes.values())
    if t.get("threads", 0) > logical:
        warns.append({"path": f"/tests/{i}/threads", "code": "W_OVERSUBSCRIBED",
                      "msg": f"{t['threads']} Threads, aber nur {logical} logische Prozessoren; Prozessoren werden mehrfach belegt."})
    if t["type"] != "memory":
        return
    simd = t.get("simd", "auto")
    if simd != "auto" and simd not in SIMD_OK:
        errs.append({"path": f"/tests/{i}/simd", "code": "E_SIMD_UNSUPPORTED", "msg": f"Befehlssatz {simd} wird auf {ARCH} nicht unterstützt."})
    bs = parse_size(t["buffer_size"])
    if bs < 8192:
        errs.append({"path": f"/tests/{i}/buffer_size", "code": "E_BUFFER_SIZE", "msg": "buffer_size muss mindestens 8 KiB sein."})
        return
    if t["mode"] == "latency" and t.get("threads", 0) > 1:
        warns.append({"path": f"/tests/{i}/threads", "code": "W_THREADS_IGNORED", "msg": "Latenz wird immer mit einem Thread gemessen."})
    if t.get("nontemporal") and t["mode"] == "bandwidth" and t.get("op", "read") == "read":
        warns.append({"path": f"/tests/{i}/nontemporal", "code": "W_NONTEMPORAL_IGNORED", "msg": "nontemporal wirkt nur bei write und copy."})
    need = mem_threads(t, meta) * bs
    free = nodes[t["mem_node"]].get("ram_free_bytes", 0) if "mem_node" in t else meta.get("ram_free_bytes", meta["ram_bytes"])
    if need > free * 0.9:
        errs.append({"path": f"/tests/{i}/buffer_size", "code": "E_OUT_OF_MEMORY",
                     "msg": f"Puffer gesamt {need / GIB:.1f} GiB > 90 % des freien Arbeitsspeichers ({free / GIB:.1f} GiB)."})
    elif need > free * 0.5:
        warns.append({"path": f"/tests/{i}/buffer_size", "code": "W_LOW_MEMORY",
                      "msg": f"Puffer gesamt {need / GIB:.1f} GiB belegen mehr als 50 % des freien Arbeitsspeichers ({free / GIB:.1f} GiB)."})


# ----------------------------------------------------------------------------- performance model
Z = {"p50": 0, "p90": 1.2816, "p95": 1.6449, "p99": 2.3263, "p99_9": 3.0902, "p99_99": 3.7190}
SIGMA = 0.8


def lat_stats(mean):
    p50 = mean / math.exp(SIGMA ** 2 / 2)
    d = {k: round(p50 * math.exp(SIGMA * z), 1) for k, z in Z.items()}
    return {"min": round(p50 * math.exp(-3.2 * SIGMA), 1), "mean": round(mean, 1), "max": round(p50 * math.exp(4.6 * SIGMA), 1),
            "stddev": round(mean * math.sqrt(math.exp(SIGMA ** 2) - 1), 1), **d}


def spec_caps(t):
    caps = []
    for s in t["access_specs"]:
        bs = parse_size(s["block_size"]); rd = s["read_pct"] / 100; rnd = s["random_pct"] / 100
        iops_rand = 1_000_000 * rd + 260_000 * (1 - rd)
        iops_seq = 900_000
        bw = 6.8e9 * rd + 4.0e9 * (1 - rd)
        cap = min(iops_rand * rnd + iops_seq * (1 - rnd), bw / bs) * PERF[0]
        caps.append((s, bs, cap))
    return caps


def storage_model(t):
    caps = spec_caps(t)
    inv = sum(s["weight"] / 100 / cap for s, _, cap in caps)
    cap_mix = 1 / inv
    qd = t.get("queue_depth", 1) * t.get("workers", 1)
    lat0 = 70e-6 * PERF[1]
    iops = min(cap_mix, qd / lat0)
    lat_us = qd / iops * 1e6
    specs = []
    for s, bs, cap in caps:
        specs.append({"spec": s, "bs": bs, "w": s["weight"] / 100, "lat_rel": (1 / cap) / inv})
    avg_bs = sum(x["w"] * x["bs"] for x in specs)
    return iops, lat_us, avg_bs, specs


HIST_PER_OCTAVE = 32  # core M1 (docs/core.md): 32 classes per power of two, only non-empty buckets, overflow le_us = null


def lognorm_hist(mean, n):
    p50 = mean / math.exp(SIGMA ** 2 / 2)
    edges = [round(2 ** (k / HIST_PER_OCTAVE), 3) for k in range(-2 * HIST_PER_OCTAVE, 18 * HIST_PER_OCTAVE + 1)]  # 0.25 us .. 262 ms
    cdf = lambda x: 0.5 * (1 + math.erf((math.log(x) - math.log(p50)) / (SIGMA * math.sqrt(2))))
    probs, prev = [], 0.0
    for e in edges:
        c = cdf(e); probs.append(c - prev); prev = c
    probs.append(1 - prev)
    return [{"le_us": e, "count": c} for e, c in zip(edges + [None], split_int(n, probs)) if c]


def split_int(n, weights):
    tot = sum(weights) or 1
    raw = [n * w / tot for w in weights]
    out = [int(r) for r in raw]
    for i in sorted(range(len(raw)), key=lambda i: raw[i] - out[i], reverse=True)[: n - sum(out)]:
        out[i] += 1
    return out


# ----------------------------------------------------------------------------- run
_OUT = None
_LOG = None
_RUNF = None  # <data_dir>/runs/<run_id>.jsonl, always written
_stop = False


_STOP_AT = os.environ.get("SBENCH_MOCK_STOP_AT", "")   # "<test>:<phase>[:<n events>]" -> deterministic stop (demo/tests)
_stop_cnt = [0]


def _stop_hook(obj):
    """SBENCH_MOCK_STOP_AT=2:warmup stops at the 1st progress event of test 2 in warmup; 3:measure:4 after its 4th tick;
    2:preparing:2 after the 2nd preparing progress event. Same effect as `sbench stop` (exit 3, partial result)."""
    global _stop
    if not _STOP_AT or _stop:
        return
    parts = _STOP_AT.split(":"); ti, ph = int(parts[0]), parts[1]; n = int(parts[2]) if len(parts) > 2 else 1
    if obj.get("test") != ti:
        return
    if (ph == "measure" and obj.get("ev") == "tick") or (obj.get("ev") == "progress" and obj.get("phase") == ph):
        _stop_cnt[0] += 1
        if _stop_cnt[0] >= n:
            _stop = True


_FAIL_AT = os.environ.get("SBENCH_MOCK_FAIL_AT", "")  # "<test>:<E_CODE>[:<ticks>[:<entries>]]" -> runtime error
_fail = {}                                              # set when the error fires: {"test", "code", "entries", "phase"}
_fail_cnt = [0]


def _fail_hook(obj):
    """Runtime error like the core: the running test ends (partial, stopped_in = current phase), errors[] filled, no
    further tests, exit 1. ticks 0: fails at the first preparing progress (e.g. E_OUT_OF_MEMORY when allocating)."""
    global _stop
    if not _FAIL_AT or _fail or _stop:
        return
    parts = _FAIL_AT.split(":"); ti, code = int(parts[0]), parts[1]
    n = int(parts[2]) if len(parts) > 2 else 3; k = int(parts[3]) if len(parts) > 3 else 1
    if obj.get("test") != ti:
        return
    hit = (n == 0 and obj.get("ev") == "progress" and obj.get("phase") == "preparing")
    if obj.get("ev") == "tick" and n > 0:
        _fail_cnt[0] += 1; hit = _fail_cnt[0] >= n
    if hit:
        _fail.update(test=ti, code=code, entries=k, t=obj.get("t"), phase="measure" if obj.get("ev") == "tick" else "preparing")
        _stop = True


ERR_MSG = {"E_ACCESS_DENIED": "Zugriff verweigert (Windows-Fehler 5)", "E_OUT_OF_MEMORY": "VirtualAllocExNuma: nicht genug Arbeitsspeicher",
           "E_IO": "ReadFile: Der Datenträger meldet einen E/A-Fehler (Windows-Fehler 1117)"}


def _fail_errors():
    """errors[] for the failed test: the fatal entry first, then (entries > 1) aggregated follow-up errors."""
    code = _fail["code"]
    e0 = {"code": code, "msg": ERR_MSG.get(code, f"Mock-Laufzeitfehler {code}")}
    if isinstance(_fail.get("t"), (int, float)):
        e0["t"] = _fail["t"]
    out = [e0]
    for j in range(1, _fail["entries"]):
        out.append({"code": "E_IO" if j % 2 else "E_TIMEOUT", "msg": f"Folgefehler {j}: I/O von Worker {j} nicht abgeschlossen",
                    "worker": j, "count": 1 + j})
    if V131:          # 1.3.1 (#17): fatal: true on exactly the one entry that ended the test (and the run); absent = false
        e0["fatal"] = True
    return out


def emit(obj):
    _stop_hook(obj); _fail_hook(obj)
    line = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    if _LOG:
        _LOG.write(f"{dt.datetime.now().isoformat(timespec='milliseconds')} {line}\n"); _LOG.flush()
    if _RUNF:
        _RUNF.write(line + "\n"); _RUNF.flush()
    if _OUT is not None:  # no events without --progress
        _OUT.write(line + "\n"); _OUT.flush()


def log(msg):
    print(msg, file=sys.stderr, flush=True)  # human-readable logs always to stderr


def _sig(*_):
    global _stop
    _stop = True


def marker(kind, rid):
    return os.path.join(DATA, f".{kind}-{rid}")


def sleep_tick(sec, rid):
    global _stop
    end = time.monotonic() + sec / SPEED
    while time.monotonic() < end:
        if os.path.exists(marker("stop", rid)):
            _stop = True
            return
        time.sleep(min(0.05, max(0, end - time.monotonic())))


def _phase_start(ti, extra):
    """First phase event of a test (preparing). 1.3: + threads (and cpus[] for cpu/memory)."""
    ev = {"ev": "phase", "test": ti, "name": "preparing"}
    if V13:
        ev.update(extra)
    emit(ev)


def _ts_from_ticks(ticks, keys):
    """1.3: one timeseries point per tick (--tick-ms); <= 1.2: 1 s buckets (end of interval)."""
    if V13:
        out = []
        for tk in ticks:
            p = {"t": tk["t"]}
            for k in keys:
                if k in tk:
                    p[k] = tk[k] if k != "lat_us" else {x: tk["lat_us"][x] for x in ("mean", "p99", "max")}
            out.append(p)
        return out
    ts, bucket, acc = [], 1, []
    for tk in ticks:
        acc.append(tk)
        if tk["t"] + 1e-9 >= bucket or tk is ticks[-1]:
            n = len(acc); p = {"t": round(min(bucket, tk["t"]), 3)}
            for k in keys:
                if k in ("proc_cpu_pct", "score") or k not in tk:
                    continue
                if k == "lat_us":
                    p[k] = {"mean": round(sum(a["lat_us"]["mean"] for a in acc) / n, 1), "p99": max(a["lat_us"]["p99"] for a in acc),
                            "max": max(a["lat_us"]["max"] for a in acc)}
                else:
                    p[k] = round(sum(a[k] for a in acc) / n, 3 if k in ("gbps", "latency_ns") else 1)
            ts.append(p); acc = []; bucket += 1
    return ts


STORAGE_NULL = ("iops", "mbps", "read_iops", "write_iops", "read_mbps", "write_mbps", "lat_us", "errors", "io_count", "cpu_pct", "proc_cpu_pct")


def _stopped_early(ti, typ, t, phase, summ_cfg, null_keys):
    """1.3: test stopped in preparing/warmup -> present, partial, summary values null, per_* empty. <= 1.2: absent."""
    if not V13:
        return None
    summ = dict(summ_cfg); summ.update({k: None for k in null_keys})
    out = {"index": ti, "type": typ, "partial": True, "stopped_in": phase, "config": t, "summary": summ, "errors": []}
    if typ == "storage":
        out.update(per_worker=[], per_spec=[], histogram=[], timeseries=[])
    else:
        out.update(per_worker=[], timeseries=[])
    return out


def _mark(r, stopped):
    if V13 and r is not None:
        r["partial"] = bool(stopped)
        if stopped:
            r["stopped_in"] = "measure"
        r.setdefault("partial", False)
        keys = list(r)
        order = ["index", "type", "partial", "stopped_in"]
        return {k: r[k] for k in order + [k for k in keys if k not in order] if k in r}
    return r


def run_storage(ti, t, tick, rng, rid):
    iops0, lat0, avg_bs, specs = storage_model(t)
    nw = t.get("workers", 1); nt = len(t["targets"])
    wshare = [1 + rng.uniform(-.05, .05) for _ in range(nw)]; s_ = sum(wshare); wshare = [w / s_ for w in wshare]
    # preparing: create/extend test files (progress in bytes); raw targets need no preparation
    _phase_start(ti, {"threads": nw})                    # 1.3: storage = number of workers, no cpus (workers are not pinned)
    total = sum(parse_size(tg["file_size"]) for tg in t["targets"] if tg["kind"] == "file")
    if total:
        rate = 2.5e9  # B/s simulated file creation
        done = 0
        while done < total and not _stop:
            step = min(total - done, int(rate))
            sleep_tick(step / rate, rid); done += step
            emit({"ev": "progress", "test": ti, "phase": "preparing", "done": done, "total": total, "unit": "bytes"})
    else:
        sleep_tick(0.3, rid)
    if _stop:
        emit({"ev": "phase", "test": ti, "name": "cleanup"})
        return _stopped_early(ti, "storage", t, "preparing", {}, STORAGE_NULL)
    wu = float(t.get("warmup_s", 0))
    if wu:
        emit({"ev": "phase", "test": ti, "name": "warmup", "duration_s": wu}); el = 0.0
        while el < wu and not _stop:
            step = min(1.0, wu - el); sleep_tick(step, rid); el += step
            emit({"ev": "progress", "test": ti, "phase": "warmup", "done": round(el, 3), "total": wu, "unit": "s"})
        if _stop:
            emit({"ev": "phase", "test": ti, "name": "cleanup"})
            return _stopped_early(ti, "storage", t, "warmup", {}, STORAGE_NULL)
    dur = float(t["duration_s"])
    emit({"ev": "phase", "test": ti, "name": "measure", "duration_s": dur})
    el = 0.0; ticks = []
    rd_frac = sum(s["weight"] / 100 * s["read_pct"] / 100 for s in t["access_specs"])
    wlat = [1 + rng.uniform(-.04, .06) for _ in range(nw)]
    while el + 1e-9 < dur and not _stop:
        step = min(tick, dur - el)
        sleep_tick(step, rid)
        if _stop:
            break
        el = round(el + step, 3)
        dip = .6 if rng.random() < .03 else 1
        f = (1 + .035 * math.sin(el / 3.3) + rng.gauss(0, .02)) * dip
        iops = iops0 * f; mean = lat0 / f * (1 + rng.gauss(0, .015))
        ls = lat_stats(mean)
        mbps = iops * avg_bs / 1e6
        rf = min(1, max(0, rd_frac + rng.gauss(0, .004)))
        p99 = round(ls["p99"] * (1 + abs(rng.gauss(0, .06))), 1)
        proc = 2 + iops / 32000 + rng.gauss(0, 0.5)
        tk = {"ev": "tick", "test": ti, "t": el, **({"metric": "iops"} if V13 else {}), "iops": round(iops, 1), "mbps": round(mbps, 2),
              "read_iops": round(iops * rf, 1), "write_iops": round(iops * (1 - rf), 1),
              "read_mbps": round(mbps * rf, 2), "write_mbps": round(mbps * (1 - rf), 2),
              "lat_us": {"mean": ls["mean"], "p50": ls["p50"], "p99": p99, "max": round(ls["p99"] * rng.uniform(2.5, 4.5), 1)},
              "cpu_pct": round(min(100, proc + 3 + abs(rng.gauss(0, 0.8))), 1), "proc_cpu_pct": round(max(0, proc), 1),
              "workers": [{"id": i, "iops": round(iops * w, 1), "mbps": round(mbps * w, 2),
                           "lat_us": {"mean": round(ls["mean"] * wlat[i], 1), "p99": round(p99 * wlat[i] * (1 + abs(rng.gauss(0, .03))), 1)}}
                          for i, w in enumerate(wshare)]}
        if V13 and LAG:      # worker 0 misses the 250 ms ack every 7th tick: 0 now (no lat_us), its I/O counts next tick
            k = len(ticks)
            if k % 7 == 3:
                tk["workers"][0] = {"id": 0, "iops": 0, "mbps": 0}
            elif k % 7 == 4 and k > 0:
                w0 = tk["workers"][0]; w0["iops"] = round(w0["iops"] * 2, 1); w0["mbps"] = round(w0["mbps"] * 2, 2)
        emit(tk); ticks.append((step, tk))
    stopped = _stop
    emit({"ev": "phase", "test": ti, "name": "cleanup"}); sleep_tick(0.2, rid)
    if not ticks:
        return _stopped_early(ti, "storage", t, "warmup" if wu else "preparing", {}, STORAGE_NULL)
    secs = sum(s for s, _ in ticks)
    iops = sum(s * tk["iops"] for s, tk in ticks) / secs
    mean = sum(s * tk["lat_us"]["mean"] for s, tk in ticks) / secs
    n = int(round(iops * secs))
    per_spec, spec_counts = [], split_int(n, [x["w"] for x in specs])
    for j, (x, cnt) in enumerate(zip(specs, spec_counts)):
        si = cnt / secs
        per_spec.append({"spec": j, "weight": x["spec"]["weight"], "block_size_bytes": x["bs"], "read_pct": x["spec"]["read_pct"],
                         "random_pct": x["spec"]["random_pct"], "iops": round(si, 1), "mbps": round(si * x["bs"] / 1e6, 2),
                         "lat_us": lat_stats(mean * x["lat_rel"]), "io_count": cnt})
    rmb = sum(p["mbps"] * p["read_pct"] / 100 for p in per_spec)
    mbps = sum(p["mbps"] for p in per_spec)
    per_worker = []
    for i, (w, cnt) in enumerate(zip(wshare, split_int(n, wshare))):
        wi = cnt / secs
        per_worker.append({"id": i, "target": i % nt, "iops": round(wi, 1), "mbps": round(mbps * w, 2),
                           "read_iops": round(wi * rd_frac, 1), "write_iops": round(wi * (1 - rd_frac), 1),
                           "lat_us": lat_stats(mean * wlat[i]), "io_count": cnt, "errors": 0})
    ts = _ts_from_ticks([tk for _, tk in ticks], ("iops", "mbps", "lat_us", "cpu_pct", "proc_cpu_pct"))
    summ = {"iops": round(iops, 1), "mbps": round(mbps, 2), "read_iops": round(iops * rd_frac, 1),
            "write_iops": round(iops * (1 - rd_frac), 1), "read_mbps": round(rmb, 2), "write_mbps": round(mbps - rmb, 2),
            "lat_us": lat_stats(mean), "errors": 0, "io_count": n}
    if V13:      # 1.3: cpu_pct (now also storage) + proc_cpu_pct, mean over the measure phase
        summ["cpu_pct"] = round(sum(s * tk["cpu_pct"] for s, tk in ticks) / secs, 1)
        summ["proc_cpu_pct"] = round(sum(s * tk["proc_cpu_pct"] for s, tk in ticks) / secs, 1)
    return _mark({"index": ti, "type": "storage", "config": t, "summary": summ,
                  "per_worker": per_worker, "per_spec": per_spec, "histogram": lognorm_hist(mean, n), "timeseries": ts, "errors": []}, stopped)


KEEP_FILES = [False]        # run --keep-files
EXISTING = set(filter(None, os.environ.get("SBENCH_MOCK_EXISTING", "").split(";")))   # paths that "existed before" (not created)


def _targets_state(t):
    """1.3: targets[] per storage test: created = sbench created the file (kind file, did not exist), deleted = removed
    after the test (not with --keep-files; volume/physical never created)."""
    out = []
    for k, tg in enumerate(t["targets"]):
        created = tg["kind"] == "file" and tg["path"] not in EXISTING
        out.append({"index": k, "path": tg["path"], "kind": tg["kind"], "created": created, "deleted": created and not KEEP_FILES[0]})
    return out


CPU_REF = {"int": 2.8e8, "float": 1.5e8, "crypto": 3.4e6, "mixed": 3.8e5}   # docs/core.md, ops/s of 1 reference thread
CPU_HOST = {"host": 1.01, "vm": 0.93, "vm-san": 0.93}[HOSTKIND]
# memory: per-thread GB/s (DRAM, avx2) and per-memory-node caps; copy counts read + write (STREAM)
MEM_ST = {"read": 16.0, "write": 12.0, "copy": 20.0}
MEM_ST_NT = {"write": 18.0, "copy": 24.0}
MEM_CAP = {"read": 110.0, "write": 70.0, "copy": 100.0}
MEM_CAP_NT = {"write": 95.0, "copy": 120.0}
SIMD_F = {"scalar": 0.55, "sse2": 0.85, "avx2": 1.0, "avx512": 1.08, "neon": 0.9}


def _per_thread_model(t, meta, cpus):
    """-> (metric key, [base value per thread]) for cpu / memory bandwidth / memory latency."""
    if t["type"] == "cpu":
        seen, out = {}, []
        for c in cpus:
            seen[c // 2] = seen.get(c // 2, 0) + 1
        logical = meta["cpu"]["threads"]
        over = max(1.0, len(cpus) / logical)
        for c in cpus:
            share = 0.62 if seen[c // 2] > 1 and meta["cpu"]["threads"] > meta["cpu"]["cores"] else 1.0  # SMT siblings busy
            out.append(CPU_REF[t.get("workload", "mixed")] * CPU_HOST * share / over)
        return "ops_per_s", out
    bs = parse_size(t["buffer_size"])
    vmf = 1.0 if HOSTKIND == "host" else 0.9
    if t["mode"] == "latency":
        n = node_of(meta, cpus[0]); mn = t.get("mem_node", n)
        lat = 1.3 if bs <= 32 * 1024 else 4.2 if bs <= 1 << 20 else 19 if bs <= 32 << 20 else 95 / vmf
        if bs > 32 << 20 and mn != n:
            lat *= 1.45
        return "latency_ns", [lat]
    op = t.get("op", "read"); nt = t.get("nontemporal", False) and op != "read"
    simd = t.get("simd", "auto"); simd = "avx2" if simd == "auto" else simd
    st = (MEM_ST_NT[op] if nt else MEM_ST[op]) * SIMD_F[simd] * vmf
    if bs <= 1 << 20:
        st *= 4.0                           # L2
    elif bs <= 32 << 20:
        st *= 2.0                           # L3
    vals = []
    for c in cpus:
        n = node_of(meta, c); mn = t.get("mem_node", n)
        vals.append(st * (0.6 if mn != n else 1.0))
    if bs > 32 << 20:                       # DRAM: cap per memory node
        cap = (MEM_CAP_NT[op] if nt else MEM_CAP[op]) * vmf
        by_mem = {}
        for k, c in enumerate(cpus):
            by_mem.setdefault(t.get("mem_node", node_of(meta, c)), []).append(k)
        for mn, ks in by_mem.items():
            tot = sum(vals[k] for k in ks)
            remote = any(node_of(meta, cpus[k]) != mn for k in ks)
            lim = cap * (0.45 if remote else 1.0)
            if tot > lim:
                for k in ks:
                    vals[k] *= lim / tot
    return "gbps", vals


def run_compute(ti, t, tick, rng, rid, meta):
    """Contract 1.2: cpu / memory with pinned threads. Phases: preparing (memory, progress in bytes = threads x buffer),
    warmup (progress in s), measure (ticks with ops_per_s | gbps | latency_ns + cpu_pct + proc_cpu_pct and, except for
    latency, workers[] with the per-thread value), cleanup. Stop during measure -> partial values; before -> absent."""
    typ = t["type"]
    latency = typ == "memory" and t["mode"] == "latency"
    threads = 1 if latency else (t.get("threads", 0) or 0)
    cpus = thread_cpus(meta, threads, t.get("numa_node"))
    if latency:
        cpus = cpus[:1]
    key, base = _per_thread_model(t, meta, cpus)
    wl = t.get("workload", "mixed")
    bs_act = parse_size(t["buffer_size"]) // 64 * 64 if latency else (parse_size(t["buffer_size"]) // 8192 * 8192 if typ == "memory" else 0)
    cfg_summ = ({"workload": wl, "threads": len(cpus), "score_reference": CPU_REF[wl]} if typ == "cpu" else
                {"mode": t["mode"], **({} if latency else {"op": t.get("op", "read")}), "threads": len(cpus), "buffer_size_bytes": bs_act,
                 **({} if latency else {"simd": "avx2" if t.get("simd", "auto") == "auto" else t["simd"],
                                         "nontemporal": bool(t.get("nontemporal")) and t.get("op", "read") != "read" and ARCH == "x64"})})
    if "numa_node" in t:
        cfg_summ["numa_node"] = t["numa_node"]
    if typ == "memory" and "mem_node" in t:
        cfg_summ["mem_node"] = t["mem_node"]
    cfg_summ["cpus"] = cpus
    null_keys = (("ops_per_s", "score", "ops") if typ == "cpu" else (("latency_ns", "loads") if latency else ("gbps", "bytes"))) + ("cpu_pct", "proc_cpu_pct")
    # 1.3: the first phase event of every test (cpu too) carries threads + the pinned CPUs (incl. SMT siblings)
    if typ == "memory" or V13:
        _phase_start(ti, {"threads": len(cpus), "cpus": [cpu_entry(meta, i, c) for i, c in enumerate(cpus)]})
    if typ == "memory":
        # 1.3: progress ~ every 0.3 s (>= 250 ms apart) + a final event with done = total; total = threads x actual size
        total = len(cpus) * bs_act; done = 0; rate = 6 * GIB; iv = 0.3 if V13 else 1.0
        while done < total and not _stop:
            step = min(total - done, int(rate * iv))
            sleep_tick(step / rate, rid); done += step
            emit({"ev": "progress", "test": ti, "phase": "preparing", "done": done, "total": total, "unit": "bytes"})
        if _stop:
            emit({"ev": "phase", "test": ti, "name": "cleanup"})
            return _stopped_early(ti, typ, t, "preparing", cfg_summ, null_keys)
    wu = float(t.get("warmup_s", 1))
    if wu:
        emit({"ev": "phase", "test": ti, "name": "warmup", "duration_s": wu}); el = 0.0
        wstep = 0.5 if V13 else 1.0          # 1.3: cpu/memory warmup progress ~ every 0.5 s
        while el < wu and not _stop:
            step = min(wstep, wu - el); sleep_tick(step, rid); el += step
            emit({"ev": "progress", "test": ti, "phase": "warmup", "done": round(el, 3), "total": wu, "unit": "s"})
        if _stop:
            emit({"ev": "phase", "test": ti, "name": "cleanup"})
            return _stopped_early(ti, typ, t, "warmup", cfg_summ, null_keys)
    dur = float(t["duration_s"])
    emit({"ev": "phase", "test": ti, "name": "measure", "duration_s": dur})
    logical = meta["cpu"]["threads"]
    load = min(100.0, len(set(cpus)) / logical * 100)
    jitter = [1 + rng.uniform(-.02, .02) for _ in cpus]
    el = 0.0; ticks = []; nd = 3 if key != "ops_per_s" else 1
    while el + 1e-9 < dur and not _stop:
        step = min(tick, dur - el); sleep_tick(step, rid)
        if _stop:
            break
        el = round(el + step, 3)
        f = 1 + .012 * math.sin(el / 2.1) + rng.gauss(0, .006)
        per = [round(b * j * f * (1 + rng.gauss(0, .004)), nd) for b, j in zip(base, jitter)]
        tot = round(sum(per), nd) if key != "latency_ns" else per[0]
        tk = {"ev": "tick", "test": ti, "t": el, **({"metric": key} if V13 else {}), key: tot,
              "cpu_pct": round(min(100, load * 1.01 + abs(rng.gauss(0, .3))), 2), "proc_cpu_pct": round(load * 0.99, 2)}
        if V13 and typ == "cpu":
            tk["score"] = round(tot / CPU_REF[wl] * 1000, 1)
        if not latency:
            tk["workers"] = [{"id": i, key: v} for i, v in enumerate(per)]
        emit(tk); ticks.append((step, per, tk))
    stopped = _stop
    emit({"ev": "phase", "test": ti, "name": "cleanup"}); sleep_tick(0.1, rid)
    if not ticks:
        return _stopped_early(ti, typ, t, "warmup" if wu else "preparing", cfg_summ, null_keys)
    secs = sum(s for s, _, _ in ticks)
    avg = [round(sum(s * p[i] for s, p, _ in ticks) / secs, nd) for i in range(len(cpus))]
    total = round(sum(avg), nd) if key != "latency_ns" else avg[0]
    cpu_pct = round(sum(s * tk["cpu_pct"] for s, _, tk in ticks) / secs, 1)
    per_worker = []
    for i, (c, v) in enumerate(zip(cpus, avg)):
        w = {"id": i, "cpu": c, "node": node_of(meta, c)}
        if typ == "memory":
            w["mem_node"] = t.get("mem_node", w["node"])
        w[key] = v; per_worker.append(w)
    if typ == "cpu":
        summ = {"workload": wl, "threads": len(cpus), "ops_per_s": total, "score": round(total / CPU_REF[wl] * 1000, 1),
                "ops": int(total * secs)}
        if V13:
            summ["score_reference"] = CPU_REF[wl]
    else:
        bs = parse_size(t["buffer_size"])
        summ = {"mode": t["mode"]}
        if not latency:
            summ["op"] = t.get("op", "read")
        summ.update(threads=len(cpus), buffer_size_bytes=bs // 64 * 64 if latency else bs // 8192 * 8192)
        if latency:
            summ.update(latency_ns=total, loads=int(secs * 1e9 / total))
        else:
            simd = t.get("simd", "auto")
            summ.update(gbps=total, simd="avx2" if simd == "auto" else simd,
                        nontemporal=bool(t.get("nontemporal")) and summ["op"] != "read" and ARCH == "x64", bytes=int(total * 1e9 * secs))
        if "mem_node" in t:
            summ["mem_node"] = t["mem_node"]
    if "numa_node" in t:
        summ["numa_node"] = t["numa_node"]
    summ.update(cpus=cpus, cpu_pct=cpu_pct)
    if V13:
        summ["proc_cpu_pct"] = round(sum(s * tk["proc_cpu_pct"] for s, _, tk in ticks) / secs, 1)
    if typ == "memory" and "mem_node" in t:      # schema order: numa_node before mem_node (cosmetic)
        summ["mem_node"] = summ.pop("mem_node")
    ts = _ts_from_ticks([tk for _, _, tk in ticks], (key, "cpu_pct", "proc_cpu_pct", "score"))
    return _mark({"index": ti, "type": typ, "config": t, "summary": summ, "per_worker": per_worker, "timeseries": ts, "errors": []}, stopped)


def run_simple(ti, t, tick, rng, rid, meta):
    """cpu / memory / net / hyperv_info: plausible values, schema-conform."""
    typ = t["type"]
    if typ != "hyperv_info":
        emit({"ev": "phase", "test": ti, "name": "warmup", "duration_s": 0.5}); sleep_tick(0.5, rid)
    dur = float(t.get("duration_s", t.get("sample_s", 5))); el = 0.0; vals = []
    emit({"ev": "phase", "test": ti, "name": "measure", "duration_s": dur})
    threads = t.get("threads", 0) or meta["cpu"]["threads"]
    while el + 1e-9 < dur and not _stop:
        step = min(tick, dur - el); sleep_tick(step, rid)
        if _stop:
            break
        el = round(el + step, 3); f = 1 + rng.gauss(0, .015)
        tk = {"ev": "tick", "test": ti, "t": el}
        if typ == "cpu":
            tk.update(ops_per_s=round(threads * 1.15e9 * f, 1), cpu_pct=round(min(100, 98.5 + rng.gauss(0, .5)), 1),
                      proc_cpu_pct=round(min(100, 97.8 + rng.gauss(0, .5)), 1)); vals.append(tk["ops_per_s"])
        elif typ == "memory":
            g = (182 if t["mode"] == "bandwidth" else 0.0) * f
            tk.update(gbps=round(g, 2), cpu_pct=round(min(100, 60 + rng.gauss(0, 2)), 1)); vals.append(g)
        elif typ in ("net_client", "net_server"):
            mb = 23400 * f * (0.82 if HOSTKIND != "host" else 1)
            tk.update(mbps=round(mb, 1), cpu_pct=round(12 + rng.gauss(0, 1), 1)); vals.append(mb)
        else:
            vals.append(0)
        if typ != "hyperv_info":
            emit(tk)
    emit({"ev": "phase", "test": ti, "name": "cleanup"})
    if not vals and typ != "hyperv_info":
        return None
    avg = sum(vals) / max(1, len(vals))
    if typ == "cpu":
        summ = {"workload": t.get("workload", "mixed"), "threads": threads, "ops_per_s": round(avg, 1), "score": round(avg / 1e8, 1)}
    elif typ == "memory":
        bs = parse_size(t["buffer_size"])
        summ = {"mode": t["mode"], "threads": 1 if t["mode"] == "latency" else threads, "buffer_size_bytes": bs}
        summ.update({"op": t.get("op", "read"), "gbps": round(avg, 2)} if t["mode"] == "bandwidth" else {"latency_ns": round(96 * PERF[1] ** .3, 1)})
    elif typ == "net_client":
        mode = t.get("mode", "throughput"); proto = t.get("proto", "tcp")
        summ = {"proto": proto, "mode": mode, "streams": t.get("streams", 1), "reverse": t.get("reverse", False)}
        if mode == "throughput":
            summ.update(mbps=round(avg, 1), bytes=int(avg * 1e6 / 8 * dur))
            if proto == "udp":
                summ.update(jitter_ms=0.04, loss_pct=0.01)
        else:
            summ["rtt_ms"] = {"min": 0.031, "mean": 0.048, "max": 0.41, "p50": 0.045, "p90": 0.061, "p99": 0.12}
    elif typ == "net_server":
        summ = {"proto": t.get("proto", "tcp"), "clients": 1, "bytes": int(avg * 1e6 / 8 * dur), "mbps": round(avg, 1)}
    else:
        summ = {"hypervisor": meta["hypervisor"], "vm_count": 7 if meta["hypervisor"]["role"] == "host" else None}
    return {"index": ti, "type": typ, "config": t, "summary": summ, "errors": []}


def run(a):
    global _OUT, _LOG, _RUNF, _stop
    signal.signal(signal.SIGTERM, _sig); signal.signal(signal.SIGINT, _sig)
    KEEP_FILES[0] = bool(getattr(a, "keep_files", False))
    if a.tick_ms < 100:
        log("--tick-ms: Minimum 100"); return EXIT_INVALID
    rid = a.id or dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + f"{random.randrange(16**4):04x}"
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", rid):
        log("--id ungültig"); return EXIT_INVALID
    for d in ("logs", "jobs", "runs"):
        os.makedirs(os.path.join(DATA, d), exist_ok=True)
    _LOG = open(os.path.join(DATA, "logs", f"{rid}.log"), "w", encoding="utf-8")
    runf = os.path.join(DATA, "runs", f"{rid}.jsonl")
    _RUNF = open(runf, "w", encoding="utf-8", newline="\n")
    if a.progress == "stdout":
        _OUT = sys.stdout
    elif a.progress and a.progress.startswith("file:"):
        _OUT = open(a.progress[5:], "a", encoding="utf-8", newline="\n")
    elif a.progress and a.progress.startswith("pipe:"):
        # sbench is the pipe CLIENT; the GUI created the server end (DACL: user SID + BUILTIN\Administrators,
        # PIPE_REJECT_REMOTE_CLIENTS). Real core retries for 10 s, then E_PIPE_CONNECT / exit 1.
        end = time.monotonic() + 10
        while True:
            try:
                _OUT = open(a.progress[5:], "w", encoding="utf-8", newline="\n"); break
            except OSError as e:
                if time.monotonic() > end:
                    emit({"ev": "error", "code": "E_PIPE_CONNECT", "msg": f"Pipe {a.progress[5:]} nicht erreichbar (10 s): {e}"})
                    return EXIT_ERR
                time.sleep(0.25)
    elif a.progress:
        log("--progress: unbekanntes Ziel"); return EXIT_INVALID
    try:
        job = json.load(open(a.job, encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        emit({"ev": "error", "code": "E_SCHEMA", "msg": f"Job nicht lesbar: {e}", "field": ""}); return EXIT_INVALID
    errs, _w = validate(job)
    for i, t in enumerate(job.get("tests", []) if not errs else []):
        for k, tg in enumerate(t.get("targets", [])):
            if t["type"] == "storage" and tg["kind"] in ("volume", "physical") and writes(t) and not a.allow_destructive:
                errs.append({"path": f"/tests/{i}/targets/{k}/allow_destructive", "code": "E_DESTRUCTIVE_NOT_ALLOWED",
                             "msg": f"Schreibtest auf {tg['path']}: Target hat allow_destructive, aber 'sbench run --allow-destructive' fehlt."})
    if errs:
        for e in errs:
            emit({"ev": "error", "code": e["code"], "msg": e["msg"], "field": e["path"]})
        return EXIT_INVALID
    json.dump(job, open(os.path.join(DATA, "jobs", f"{rid}.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    if not ELEVATED:
        for i, t in enumerate(job["tests"]):
            for k, tg in enumerate(t.get("targets", [])):
                if tg["kind"] in ("volume", "physical"):
                    emit({"ev": "error", "code": "E_ACCESS_DENIED", "msg": f"{tg['path']}: Administratorrechte erforderlich.",
                          "field": f"/tests/{i}/targets/{k}", "test": i})
                    return EXIT_PRIV
    open(marker("run", rid), "w").close()
    started = utc_now()
    job_name = job.get("name") or os.path.splitext(os.path.basename(a.job))[0] or "job"  # sb-runs/1 job_name
    lock = os.path.join(DATA, "runs", f"{rid}.lock.json")
    with open(lock, "w", encoding="utf-8") as f:
        json.dump({"run_id": rid, "job_name": job_name, "pid": os.getpid(), "started_utc": utc_now(), "job": os.path.join(DATA, "jobs", f"{rid}.json"),
                   "out": os.path.abspath(a.out)}, f, indent=1, ensure_ascii=False)  # sb-runs-1#/$defs/lock_file
    rc = EXIT_ERR
    try:
        meta = info_doc()["meta"]
        emit({"ev": "start", "run_id": rid, "tests": len(job["tests"])})
        log(f"run {rid}: {len(job['tests'])} Test(s)")
        rng = random.Random(f"{HOSTKIND}-{rid}")
        tick = a.tick_ms / 1000; results = []
        for ti, t in enumerate(job["tests"]):
            if t["type"] not in IMPLEMENTED:  # core M1/M2: skipped, not in tests[] (indices keep the job position)
                msg = f"Testtyp '{t['type']}' ist in Meilenstein {'2' if M2 else '1'} noch nicht implementiert und wird übersprungen."
                emit({"ev": "error", "code": "E_NOT_IMPLEMENTED", "msg": msg, "test": ti}); log(f"Test {ti}: {msg}")
                continue
            if t["type"] == "storage":
                r = run_storage(ti, t, tick, rng, rid)
            elif t["type"] in ("cpu", "memory") and M2:
                r = run_compute(ti, t, tick, rng, rid, meta)
            else:
                r = run_simple(ti, t, tick, rng, rid, meta)
            if r and V13 and t["type"] == "storage":
                r["targets"] = _targets_state(t)
            if r and V131 and r["type"] in ("cpu", "memory"):
                cp = (r.get("summary") or {}).get("cpus") or []
                r["cpus"] = [cpu_entry(meta, i, c) for i, c in enumerate(cp)]
            if r and _fail and _fail["test"] == ti:
                r["errors"] = _fail_errors()
                if r["type"] == "storage" and (r.get("summary") or {}).get("errors") is not None:
                    r["summary"]["errors"] = sum(e.get("count", 1) for e in r["errors"])
                if V13:
                    r["partial"] = True; r["stopped_in"] = r.get("stopped_in") or _fail["phase"]
                emit({"ev": "error", "code": _fail["code"], "msg": r["errors"][0]["msg"], "test": ti})
            if r:
                results.append(r)
            if _stop:
                break
        # runtime error: 1.3.1 (#17, default here) aborted: false, exit 1; the real 1.3 core writes aborted: true (exit 1) ->
        # SBENCH_MOCK_ABORT_ON_ERROR=1. Exit code (index.jsonl) is the unambiguous signal in both versions.
        user_stop = _stop and (not _fail or ABORT_ON_ERROR)
        res = {"schema": "sb-result/1", "kern_version": VERSION, "run_id": rid, "started_utc": started, "ended_utc": utc_now(),
               "aborted": user_stop, **({"tick_ms": a.tick_ms} if V13 else {}), "meta": meta, "tests": results}
        tmp = a.out + ".tmp"  # write <out>.tmp, then atomic rename
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(res, f, indent=1, ensure_ascii=False)
        os.replace(tmp, a.out)
        rc = EXIT_ERR if _fail else (EXIT_ABORTED if _stop else EXIT_OK)
        # done.exit_code: contract 1.3.1 (#17 a4287fa), optional 0-4, 3 iff aborted, 1 iff a fatal entry -> 1.3.1 mode only
        # (main's 1.3 schema does not allow it yet)
        emit({"ev": "done", "result": os.path.abspath(a.out), "aborted": user_stop, **({"exit_code": rc} if V131 else {})})
        return rc
    finally:
        with open(os.path.join(DATA, "runs", "index.jsonl"), "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"run_id": rid, "started_utc": started, "ended_utc": utc_now(), "out": os.path.abspath(a.out),
                                "exit_code": rc}) + "\n")
        for k in (marker("run", rid), marker("stop", rid), lock):
            if os.path.exists(k):
                os.remove(k)


# ----------------------------------------------------------------------------- export / bundle
FLAT = ["test", "type", "scope", "id", "iops", "mbps", "read_iops", "write_iops", "read_mbps", "write_mbps",
        "lat_mean_us", "lat_p99_us", "lat_max_us", "errors"]
FLAT_M2 = ["ops_per_s", "gbps", "latency_ns", "score"]   # contract 1.2: appended, older positions unchanged
FLAT_V13 = ["partial", "stopped_in"]                     # contract 1.3: per test, on every row of the test


def _flat_row(idx, typ, scope, id_, x, rw_mbps, tst=None):
    """Like core export.c flat_row(): every type gets total/worker/spec rows; empty = no value (column n/a for type/scope
    or null in a partial test), 0 = measured. 1.3: + partial (true/false), stopped_in."""
    g = lambda o, k: "" if not isinstance(o, dict) or o.get(k) is None or isinstance(o.get(k), (dict, list)) else o[k]
    lat = x.get("lat_us") if isinstance(x.get("lat_us"), dict) else {}
    e = x.get("errors", "")
    row = [idx, typ, scope, id_, g(x, "iops"), g(x, "mbps"), g(x, "read_iops"), g(x, "write_iops"),
           g(x, "read_mbps") if rw_mbps else "", g(x, "write_mbps") if rw_mbps else "", g(lat, "mean"), g(lat, "p99"), g(lat, "max"),
           len(e) if isinstance(e, list) else e]
    row += [g(x, k) for k in FLAT_M2] if M2 else []
    if V13:
        pa = (tst or {}).get("partial")
        row += ["" if pa is None else ("true" if pa else "false"), (tst or {}).get("stopped_in") or ""]
    return row


def export(a):
    res = json.load(open(a.result, encoding="utf-8"))
    with open(a.csv, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\r\n")
        if a.style == "flat":
            w.writerow(FLAT + (FLAT_M2 if M2 else []) + (FLAT_V13 if V13 else []))
            for t in res["tests"]:
                if t["type"] not in IMPLEMENTED and t["type"] != "storage":
                    continue
                # like export.c: the total row is the summary (cpu/memory have no summary.errors -> empty)
                w.writerow(_flat_row(t["index"], t["type"], "total", "", t["summary"], True, t))
                for x in t.get("per_worker", []):
                    w.writerow(_flat_row(t["index"], t["type"], "worker", x["id"], x, False, t))
                for x in t.get("per_spec", []):
                    w.writerow(_flat_row(t["index"], t["type"], "spec", x["spec"], x, False, t))
        else:
            w.writerow(["Target Type", "Target Name", "Access Specification Name", "# Managers", "# Workers", "# Disks", "IOps",
                        "Read IOps", "Write IOps", "MBps (Decimal)", "Read MBps (Decimal)", "Write MBps (Decimal)",
                        "Average Response Time", "Maximum Response Time", "Errors"])
            for t in res["tests"]:
                if t["type"] != "storage":
                    continue
                s = t["summary"]; c = t["config"]; lat = s.get("lat_us") or {}   # 1.3: null values -> empty fields
                e = lambda v: "" if v is None else v   # noqa: E731
                name = " + ".join(f"{p['weight']}% {parse_size(p['block_size']) // 1024}K {p['read_pct']}%R {p['random_pct']}%Rnd"
                                  for p in c["access_specs"])
                w.writerow(["ALL", "All", name, 1, c.get("workers", 1), len(c["targets"]), e(s["iops"]), e(s["read_iops"]), e(s["write_iops"]),
                            e(s["mbps"]), e(s["read_mbps"]), e(s["write_mbps"]), round(lat["mean"] / 1000, 4) if "mean" in lat else "",
                            round(lat["max"] / 1000, 4) if "max" in lat else "", e(s["errors"])])
    return EXIT_OK


IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
IPV6 = re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}\b")
MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
USER = re.compile(r"(?i)(Users\\{1,2})([^\\\"/]+)")


class Anon:
    """--anonymize (cli.md 1.1): consistent pseudonyms per bundle: host-1, user-1, 10.0.0.x, fd00::x, 02:00:00:00:00:xx.
    Mapping is not stored. Disk serials: not present in sb-info/1 -> nothing to do in the mock."""
    def __init__(self, hosts):
        self.maps = {}; self.hosts = [h for h in hosts if h]

    def _p(self, kind, v, fmt):
        m = self.maps.setdefault(kind, {})
        if v not in m:
            m[v] = fmt(len(m) + 1)
        return m[v]

    def __call__(self, b):
        t = b.decode("utf-8", "replace")
        for h in self.hosts:
            t = re.sub(re.escape(h), lambda mm, h=h: self._p("host", h.upper(), lambda n: f"host-{n}"), t, flags=re.I)
        t = USER.sub(lambda mm: mm.group(1) + self._p("user", mm.group(2).lower(), lambda n: f"user-{n}"), t)
        t = IPV4.sub(lambda mm: mm.group(0) if mm.group(0).startswith(("0.", "127.")) else self._p("ip4", mm.group(0), lambda n: f"10.0.0.{n}"), t)
        t = IPV6.sub(lambda mm: self._p("ip6", mm.group(0).lower(), lambda n: f"fd00::{n:x}"), t)
        t = MAC.sub(lambda mm: self._p("mac", mm.group(0).lower(), lambda n: f"02:00:00:00:00:{n:02x}"), t)
        return t.encode("utf-8")


def bundle(a):
    """cli.md 1.1: manifest.json (sb-bundle/1), info.json, results/, runs/, logs/, extra/<ordner>/.
    --all = finished runs from runs/index.jsonl; --results = those + their runs/*.jsonl + logs; neither = info + includes only."""
    global _OUT
    if a.progress == "stdout":
        _OUT = sys.stdout
    bid = dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + f"{random.randrange(16**4):04x}"
    emit({"ev": "start", "run_id": bid, "tests": 1}); emit({"ev": "phase", "test": 0, "name": "preparing"})

    def fail(msg, rc=EXIT_ERR):
        emit({"ev": "error", "code": "E_IO", "msg": msg}); log(f"sbench: {msg}"); return rc
    files = []
    def add_run(rid, res_path):
        if res_path and os.path.exists(res_path):
            files.append((res_path, f"results/{rid}.json"))
        for d, ext in (("runs", ".jsonl"), ("logs", ".log"), ("jobs", ".json")):
            p = os.path.join(DATA, d, rid + ext)
            if os.path.exists(p):
                files.append((p, f"{d}/{rid}{ext}"))
    idx = os.path.join(DATA, "runs", "index.jsonl")
    by_out = {}
    for ln in open(idx, encoding="utf-8") if os.path.exists(idx) else []:
        e = json.loads(ln); by_out[os.path.abspath(e["out"])] = e["run_id"]
    if a.all:
        seen = set()
        for ln in open(idx, encoding="utf-8") if os.path.exists(idx) else []:
            e = json.loads(ln)
            if e["run_id"] not in seen:
                seen.add(e["run_id"]); add_run(e["run_id"], e["out"])
    elif a.results:
        for r in a.results:
            for f in ([os.path.join(r, x) for x in sorted(os.listdir(r)) if x.endswith(".json")] if os.path.isdir(r) else [r]):
                if not os.path.exists(f):
                    return fail(f"'{f}' existiert nicht.")
                rid = by_out.get(os.path.abspath(f)) or os.path.splitext(os.path.basename(f))[0]  # run_id via runs/index.jsonl
                add_run(rid, f)
    incs = []
    for inc in a.include or []:
        if not os.path.isdir(inc):
            return fail(f"--include '{inc}' ist kein Ordner.", EXIT_INVALID)
        name = os.path.basename(os.path.normpath(inc)); incs.append(name)
        for root, _, fs in os.walk(inc):
            for f in sorted(fs):
                full = os.path.join(root, f)
                files.append((full, f"extra/{name}/" + os.path.relpath(full, inc).replace(os.sep, "/")))
    info = info_doc(); m = info["meta"]
    entries = [("info.json", json.dumps(info, indent=1, ensure_ascii=False).encode())]
    seen = set()
    for src, arc in files:
        if arc not in seen:
            seen.add(arc); entries.append((arc, open(src, "rb").read()))
    anon = Anon([m["host"]]) if a.anonymize else None
    if anon:
        entries = [(arc, anon(b) if arc.endswith((".json", ".log", ".jsonl")) else b) for arc, b in entries]
    man = {"schema": "sb-bundle/1", "kern_version": VERSION, "erstellt_utc": utc_now(),
           "host": anon(m["host"].encode()).decode() if anon else m["host"],
           "data_dir": anon(os.path.abspath(DATA).encode()).decode() if anon else os.path.abspath(DATA),
           "anonymized": bool(a.anonymize), "includes": incs,
           "files": [{"path": arc, "size_bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()} for arc, b in entries]}
    tmp = a.out + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for arc, b in entries:
            z.writestr(arc, b)
        z.writestr("manifest.json", json.dumps(man, indent=1, ensure_ascii=False))
    os.replace(tmp, a.out)
    emit({"ev": "done", "result": os.path.abspath(a.out), "aborted": False})
    if a.progress != "stdout":
        log(f"Bundle geschrieben: {a.out}")
    return EXIT_OK


def _pid_alive(pid):
    if os.name == "nt":  # never os.kill() on Windows (it terminates)
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong(); k.GetExitCodeProcess(h, ctypes.byref(code)); k.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0); return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def list_running():
    runs = []
    d = os.path.join(DATA, "runs")
    for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if f.endswith(".lock.json"):
            try:
                lk = json.load(open(os.path.join(d, f), encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if _pid_alive(lk["pid"]):
                runs.append({"run_id": lk["run_id"], "job_name": lk.get("job_name") or "job", "pid": lk["pid"], "started_utc": lk["started_utc"], "out": lk["out"],
                             "progress_file": os.path.join(d, lk["run_id"] + ".jsonl")})
            else:  # orphaned lock -> ignore and remove (cli.md 1.1)
                try:
                    os.remove(os.path.join(d, f))
                except OSError:
                    pass
    print(json.dumps({"schema": "sb-runs/1", "runs": runs}, indent=1, ensure_ascii=False))
    return EXIT_OK


ICF_SPEC = re.compile(r"^\s*(\d+),(\d+),(\d+),(\d+),(\d+),(\d+),(\d+),(\d+)\s*$")


def import_icf(a):
    """PROVISIONAL (M2, core #15 has the real one): Iometer .icf -> sb-job/1. Maps 'ACCESS SPECIFICATIONS'
    (size,% of size,% reads,% random,delay,burst,align,reply), Test Description, Run Time, Ramp Up Time and the manager
    list; prints sb-validate/1 with the W_ICF_* warnings of docs/cli.md that this subset can detect (RUN_TIME, ALIGN, REPLY,
    SECTOR, ZERO_WEIGHT, NO_TARGET, NO_WORKERS, MANAGERS, UNSUPPORTED). Targets/workers/QD are not read (placeholder)."""
    try:
        txt = open(a.icf, encoding="latin-1").read().splitlines()
    except OSError as e:
        log(f"sbench: {a.icf} nicht lesbar: {e}"); return EXIT_ERR
    warns, groups, cur = [], [], None
    desc, run_s, ramp, managers, section, nxt = None, None, None, [], "", None
    for no, ln in enumerate(txt, 1):
        st = ln.strip()
        if st.startswith("'") and "=====" in st:
            section = st.strip("'= ").split(" ")[0].upper(); cur = None if section != "ACCESS" else cur; continue
        if st in ("'Test Description", "'Run Time", "'Ramp Up Time (s)", "'Manager ID, manager name"):
            nxt = st; continue
        if st.startswith("'Access specification name"):
            nxt = "spec"; continue
        if st.startswith("'") or not st:
            continue
        if nxt == "'Test Description":
            desc = st; nxt = None; continue
        if nxt == "'Run Time" and re.fullmatch(r"\d+\s+\d+\s+\d+", st):
            h, m, sec = map(int, st.split()); run_s = (h * 3600 + m * 60 + sec, no); nxt = None; continue
        if nxt == "'Ramp Up Time (s)" and st.isdigit():
            ramp = int(st); nxt = None; continue
        if nxt == "'Manager ID, manager name":
            if re.match(r"^\d+,", st):
                managers.append((st, no)); continue
            nxt = None
        if section == "ACCESS" and cur is not None and ICF_SPEC.match(ln):
            cur["lines"].append(([int(x) for x in ICF_SPEC.match(ln).groups()], no)); continue
        if section == "ACCESS" and nxt == "spec" and re.match(r"^\s*\S.*,(ALL|NONE|\d+)\s*$", ln):
            cur = {"name": ln.split(",")[0].strip(), "lines": []}; groups.append(cur); nxt = None
    groups = [g for g in groups if g["lines"]]
    if not groups:
        print(json.dumps({"schema": "sb-validate/1", "ok": False, "errors": [{"path": "", "code": "E_ICF_PARSE", "msg": "Keine Access Specification gefunden."}], "warnings": []}, ensure_ascii=False))
        return EXIT_INVALID
    dur = 60
    if run_s is None or run_s[0] == 0:
        warns.append({"path": "", "code": "W_ICF_RUN_TIME", "msg": f"Run Time {'0 (unbegrenzt)' if run_s else 'fehlt'}"
                      + (f" (Zeile {run_s[1]})" if run_s else "") + " → duration_s = 60."})
    else:
        dur = run_s[0]
    if not managers:
        warns.append({"path": "", "code": "W_ICF_NO_WORKERS", "msg": "Keine Manager-Liste → 1 Worker je Target, queue_depth 1."})
    elif len(managers) > 1:
        warns.append({"path": "", "code": "W_ICF_MANAGERS", "msg": f"{len(managers)} Manager (Zeilen {', '.join(str(n) for _, n in managers)}) → nur der erste wird übernommen."})
    tests = []
    for gi, g in enumerate(groups):
        specs = []
        for (size, pct, rd, rnd, delay, burst, align, reply), no in g["lines"]:
            j = len(specs)
            if pct == 0:
                warns.append({"path": f"/tests/{gi}/access_specs", "code": "W_ICF_ZERO_WEIGHT", "msg": f"Zeile {no}: 0 % of size → weggelassen."}); continue
            sp = {"weight": pct, "block_size": f"{size // 1024}KiB" if size % 1024 == 0 else size, "read_pct": rd, "random_pct": rnd}
            if size % 512 or (align and align % 512):
                warns.append({"path": f"/tests/{gi}/access_specs/{j}", "code": "W_ICF_SECTOR", "msg": f"Zeile {no}: size {size}/align {align} kein Vielfaches von 512."})
            if align:
                sp["align"] = f"{align // 1024}KiB" if align % 1024 == 0 else align
            else:
                warns.append({"path": f"/tests/{gi}/access_specs/{j}", "code": "W_ICF_ALIGN", "msg": f"Zeile {no}: align 0 (Sektorgrenzen) → ohne align, Ausrichtung an block_size."})
            if delay:
                sp["delay_ms"] = delay; sp["burst_len"] = max(1, burst)
            if reply:
                warns.append({"path": f"/tests/{gi}/access_specs/{j}", "code": "W_ICF_REPLY", "msg": f"Zeile {no}: reply {reply} (nur Netzwerk) wird ignoriert."})
            specs.append(sp)
        tests.append({"type": "storage", "name": g["name"][:80], "targets": [{"path": "D:\\sbtest.dat", "kind": "file", "file_size": "8GiB"}],
                      "workers": 1, "queue_depth": 1, "duration_s": dur, **({"warmup_s": ramp} if ramp else {}), "access_specs": specs})
    warns.append({"path": "/tests/0/targets", "code": "W_ICF_NO_TARGET", "msg": "Kein Disk-Worker mit Target (Mock liest Targets nicht) → Platzhalter D:\\sbtest.dat (8 GiB)."})
    warns.append({"path": "", "code": "W_ICF_UNSUPPORTED", "msg": "Mock: Worker/Queue Depth aus dem ICF werden nicht gelesen (Kern #15 kann das), bitte prüfen."})
    job = {"schema": "sb-job/1", "name": (desc or os.path.splitext(os.path.basename(a.icf))[0])[:80], "tests": tests}
    errs, _ = validate(job)
    if not errs:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(job, f, indent=1, ensure_ascii=False)
    print(json.dumps({"schema": "sb-validate/1", "ok": not errs, "errors": errs, "warnings": warns}, indent=1, ensure_ascii=False))
    return EXIT_INVALID if errs else EXIT_OK


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr); log(f"sbench: {message}"); sys.exit(EXIT_INVALID)


def _take_data_dir(argv):
    """Global --data-dir <p> / --data-dir=<p> anywhere in argv (cli.md, PR #10). Returns (argv without it, path|None)."""
    out, dd, i = [], None, 0
    while i < len(argv):
        if argv[i] == "--data-dir":
            if i + 1 >= len(argv):
                log("sbench: E_ARGS --data-dir ohne Pfad"); sys.exit(EXIT_INVALID)
            dd = argv[i + 1]; i += 2; continue
        if argv[i].startswith("--data-dir="):
            dd = argv[i].split("=", 1)[1]; i += 1; continue
        out.append(argv[i]); i += 1
    return out, dd


def main():
    global DATA
    argv, dd = _take_data_dir(sys.argv[1:])
    if dd is not None:
        try:
            os.makedirs(dd, exist_ok=True)
            if not os.access(dd, os.W_OK):
                raise OSError("nicht beschreibbar")
        except OSError as ex:
            log(f"sbench: E_ARGS --data-dir {dd}: {ex}"); return EXIT_INVALID
        DATA = os.path.abspath(dd)
    ap = Parser(prog="sbench")
    sub = ap.add_subparsers(dest="cmd", required=True, parser_class=Parser)
    r = sub.add_parser("run"); r.add_argument("--job", required=True); r.add_argument("--out", required=True)
    r.add_argument("--progress"); r.add_argument("--tick-ms", type=int, default=1000); r.add_argument("--id")
    r.add_argument("--allow-destructive", action="store_true")
    r.add_argument("--keep-files", action="store_true")
    ls_ = sub.add_parser("list"); ls_.add_argument("--running", action="store_true", required=True); ls_.add_argument("--json", action="store_true", required=True)
    ic = sub.add_parser("import-icf"); ic.add_argument("icf"); ic.add_argument("--out", required=True)
    sub.add_parser("stop").add_argument("--id", required=True)
    sub.add_parser("info").add_argument("--json", action="store_true", required=True)
    sub.add_parser("validate").add_argument("--job", required=True)
    e = sub.add_parser("export"); e.add_argument("result"); e.add_argument("--csv", required=True)
    e.add_argument("--style", choices=["flat", "iometer"], default="flat")
    sub.add_parser("version").add_argument("--json", action="store_true")
    b = sub.add_parser("bundle"); b.add_argument("--out", required=True); b.add_argument("--results", nargs="+"); b.add_argument("--all", action="store_true")
    b.add_argument("--include", action="append"); b.add_argument("--anonymize", action="store_true"); b.add_argument("--progress")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        return run(a)
    if a.cmd == "stop":
        if not os.path.exists(marker("run", a.id)):
            log(f"kein Lauf mit ID {a.id}"); return EXIT_ERR
        open(marker("stop", a.id), "w").close(); return EXIT_OK
    if a.cmd == "info":
        print(json.dumps(info_doc(), indent=1, ensure_ascii=False)); return EXIT_OK
    if a.cmd == "validate":
        try:
            job = json.load(open(a.job, encoding="utf-8"))
            errs, warns = validate(job)
        except (OSError, json.JSONDecodeError) as ex:
            errs, warns = [{"path": "", "code": "E_SCHEMA", "msg": f"Job nicht lesbar: {ex}"}], []
        print(json.dumps({"schema": "sb-validate/1", "ok": not errs, "errors": errs, "warnings": warns}, indent=1, ensure_ascii=False))
        return EXIT_INVALID if errs else EXIT_OK
    if a.cmd == "export":
        return export(a)
    if a.cmd == "version" and a.json:
        # SBENCH_MOCK_EXE: wrapper script that stands in for sbench.exe (Linux test of the frozen build) -> hash that file
        sha = hashlib.sha256(open(os.environ.get("SBENCH_MOCK_EXE") or os.path.abspath(__file__), "rb").read()).hexdigest()
        print(json.dumps({"version": VERSION, "commit": "mock", "arch": "x64", "sha256": sha, "formats": FORMATS})); return EXIT_OK
    if a.cmd == "version":
        print(f"sbench {VERSION}"); print("\n".join(FORMATS)); return EXIT_OK
    if a.cmd == "bundle":
        return bundle(a)
    if a.cmd == "list":
        return list_running()
    if a.cmd == "import-icf":
        return import_icf(a)


if __name__ == "__main__":
    sys.exit(main())
