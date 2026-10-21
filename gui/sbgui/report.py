"""HTML/PDF report from sb-result/1 (GUI-side; CSV comes from `sbench export`)."""
import html
from .model import (test_label, host_label, human_bytes, storage_tests, compute_tests, fmt_metric, numa_placement, short_size,
                    RAM_METHOD_TXT, sval, partial_text, fatal_errors, error_text, leftover_targets, run_outcome,
                    OUTCOME_TXT)


def _n(v, spec=",.0f"):
    """Null-safe number (contract 1.3: summary values are null for tests stopped before measure)."""
    return "–" if v is None else format(v, spec)


def _name(t, res):
    p = partial_text(t, res)
    return test_label(t["config"]) + (f" ({p})" if p else "")


def build_html(results, compact=False, exit_codes=None):
    """exit_codes: optional {run_id: exit_code}; decides '(abgebrochen)' vs '(Laufzeitfehler)' in the heading."""
    css = """body{font-family:'Inter','Segoe UI',sans-serif;color:#1d2733;font-size:10pt}
h1{font-size:16pt;margin:0 0 2px 0;color:#0b7285} h2{font-size:11pt;margin:14px 0 4px 0;color:#0b7285}
.m{color:#6b7785;font-size:8.5pt} table{border-collapse:collapse;width:100%;margin-top:4px}
th{background:#e7f8fb;color:#0b5563;text-align:left;font-size:8.5pt;padding:4px 6px}
td{border-bottom:1px solid #e3e8ee;padding:3px 6px;font-size:8.5pt} td.n{text-align:right;font-family:'JetBrains Mono',monospace}"""
    out = [f"<html><head><style>{css}</style></head><body>",
           "<h1>sbench Ergebnisbericht</h1>",
           f"<div class='m'>{len(results)} Lauf/Läufe · erzeugt von sbench-gui · Formate sb-result/1</div>"]
    for r in results:
        m = r["meta"]
        oc = run_outcome(r, (exit_codes or {}).get(r.get("run_id")))
        out.append(f"<h2>{html.escape(host_label(m))}{f' ({OUTCOME_TXT[oc]})' if oc != 'ok' else ''}</h2>")
        out.append(f"<div class='m'>{html.escape(m['os']['name'])} {m['os']['build']} · {html.escape(m['cpu']['model'])} "
                   f"({m['cpu']['cores']}C/{m['cpu']['threads']}T) · {human_bytes(m['ram_bytes'])} RAM · Kern {r['kern_version']} · "
                   f"{r['started_utc'][:16].replace('T', ' ')} UTC</div>")
        if storage_tests(r) or not compute_tests(r):
            cols = ["Test", "IOPS", "MB/s", "Ø µs", "p99 µs"] + ([] if compact else ["p99.9 µs", "Fehler"])
            out.append(_table(cols, [[_name(t, r), _n(sval(t, "summary", "iops")), _n(sval(t, "summary", "mbps")),
                                      _n(sval(t, "summary", "lat_us", "mean")), _n(sval(t, "summary", "lat_us", "p99"))]
                                     + ([] if compact else [_n(sval(t, "summary", "lat_us", "p99_9")), _n(sval(t, "summary", "errors"), "d")])
                                     for t in storage_tests(r)]))
        out += _compute_html(r, compact)
        for t in r["tests"]:          # runtime errors (first fatal entry, + n more) and test files left behind
            fe = fatal_errors(t) if t.get("partial") or (r["tests"] and t is r["tests"][-1]) else []
            if fe:
                out.append(f"<div class='m' style='color:#b42318'>Test {t['index'] + 1} „{html.escape(test_label(t['config']))}“: "
                           f"{html.escape(error_text(fe[0]))}{f' (+{len(fe) - 1} weitere)' if len(fe) > 1 else ''}</div>")
            for g in leftover_targets(t):
                out.append(f"<div class='m' style='color:#b54708'>Testdatei nicht gelöscht: {html.escape(g['path'])}</div>")
        if compact:
            continue
    out.append("</body></html>")
    return "".join(out)


def _table(cols, rows):
    out = ["<table><tr>" + "".join(f"<th nowrap>{c}</th>" for c in cols) + "</tr>"]
    for row in rows:
        out.append(f"<tr><td nowrap>{html.escape(str(row[0]))}</td>" + "".join(f"<td class='n' nowrap>{html.escape(str(v))}</td>" for v in row[1:]) + "</tr>")
    return "".join(out) + "</table>"


def _compute_html(r, compact):
    """Contract 1.2: CPU score table, RAM GB/s + latency table, NUMA RAM source (ram_method)."""
    out, ct = [], compute_tests(r)
    cpu = [t for t in ct if t["type"] == "cpu"]; mem = [t for t in ct if t["type"] == "memory"]
    if cpu:
        out.append("<div class='m'>CPU · Score = ops/s ÷ Referenz-Thread × 1000</div>")
        out.append(_table(["CPU-Test", "Workload", "Threads", "NUMA", "ops/s", "Score"],
                          [[_name(t, r), sval(t, "summary", "workload") or t["config"].get("workload", "–"),
                            sval(t, "summary", "threads") or "–", numa_placement(t) or "alle",
                            fmt_metric("ops_per_s", sval(t, "summary", "ops_per_s")), fmt_metric("score", sval(t, "summary", "score"))] for t in cpu]))
    if mem:
        out.append("<div class='m'>Arbeitsspeicher · GB/s = 10⁹ Byte/s · Latenz in ns</div>")
        rows = []
        for t in mem:
            s = t.get("summary") or {}; bw = s.get("mode", t["config"].get("mode")) == "bandwidth"
            rows.append([_name(t, r), (s.get("op", "") + (" NT" if s.get("nontemporal") else "")) if bw else "Latenz",
                         s.get("simd", "–") if bw else "–", s.get("threads", "–"),
                         short_size(s["buffer_size_bytes"]) if s.get("buffer_size_bytes") else "–", numa_placement(t) or "lokal",
                         fmt_metric("gbps", s.get("gbps")), fmt_metric("latency_ns", s.get("latency_ns"))])
        out.append(_table(["RAM-Test", "Modus", "SIMD", "Threads", "Puffer", "NUMA", "GB/s", "Latenz ns"], rows))
    nodes = r["meta"].get("numa_nodes") or []
    if ct and not compact and nodes:
        out.append("<div class='m'>NUMA-Knoten · RAM-Quelle (ram_method)</div>")
        out.append(_table(["Knoten", "CPUs", "RAM", "frei", "Quelle"],
                          [[n["id"], len(n.get("cpus") or []), human_bytes(n.get("ram_bytes", 0)), human_bytes(n.get("ram_free_bytes", 0))
                            if n.get("ram_free_bytes") is not None else "–", RAM_METHOD_TXT.get(n.get("ram_method"), n.get("ram_method") or "–")]
                           for n in nodes]))
    return out


def write_pdf(results, path):
    from PySide6.QtGui import QTextDocument, QPdfWriter, QPageSize
    doc = QTextDocument(); doc.setHtml(build_html(results))
    w = QPdfWriter(path); w.setPageSize(QPageSize(QPageSize.A4)); w.setResolution(150)
    doc.print_(w)
