"""HTML/PDF report from sb-result/1 (GUI-side; CSV comes from `sbench export`)."""
import html
from .model import test_label, host_label, human_bytes, storage_tests


def build_html(results, compact=False):
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
        out.append(f"<h2>{html.escape(host_label(m))}{' (aborted)' if r['aborted'] else ''}</h2>")
        out.append(f"<div class='m'>{html.escape(m['os']['name'])} {m['os']['build']} · {html.escape(m['cpu']['model'])} "
                   f"({m['cpu']['cores']}C/{m['cpu']['threads']}T) · {human_bytes(m['ram_bytes'])} RAM · Kern {r['kern_version']} · "
                   f"{r['started_utc'][:16].replace('T', ' ')} UTC</div>")
        cols = ["Test", "IOPS", "MB/s", "Ø µs", "p99 µs"] + ([] if compact else ["p99.9 µs", "Fehler"])
        out.append("<table><tr>" + "".join(f"<th nowrap>{c}</th>" for c in cols) + "</tr>")
        for t in storage_tests(r):
            s = t["summary"]; l = s["lat_us"]
            vals = [f"{s['iops']:,.0f}", f"{s['mbps']:,.0f}", f"{l['mean']:,.0f}", f"{l['p99']:,.0f}"] + ([] if compact else [f"{l['p99_9']:,.0f}", str(s["errors"])])
            out.append(f"<tr><td nowrap>{html.escape(test_label(t['config']))}</td>" + "".join(f"<td class='n' nowrap>{v}</td>" for v in vals) + "</tr>")
        out.append("</table>")
        if compact:
            continue
    out.append("</body></html>")
    return "".join(out)


def write_pdf(results, path):
    from PySide6.QtGui import QTextDocument, QPdfWriter, QPageSize
    doc = QTextDocument(); doc.setHtml(build_html(results))
    w = QPdfWriter(path); w.setPageSize(QPageSize(QPageSize.A4)); w.setResolution(150)
    doc.print_(w)
