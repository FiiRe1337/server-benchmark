"""sb-validate/1 errors/warnings -> German field names and reasons for the '<n> Fehler' chip popup (no Qt).

JSON pointers (cli.md): /tests/<i>/targets/<k>/<field>, /tests/<i>/access_specs/<j>/<field>, /tests/<i>/<field>."""

FIELD_TXT = {"workers": "Worker", "queue_depth": "Queue Depth/Worker", "cache": "Cache-Modus", "warmup_s": "Warmup (s)",
             "duration_s": "Messdauer (s)", "name": "Name", "type": "Testart", "targets": "Ziele", "access_specs": "Access Specs"}
TARGET_TXT = {"path": "Pfad", "kind": "Art", "file_size": "Dateigröße", "allow_destructive": "Freigabe (destruktiv)"}
SPEC_TXT = {"name": "Name", "weight": "Gewicht", "block_size": "Block", "read_pct": "% Lesen", "random_pct": "% Rand.",
            "align": "Align", "burst_len": "Burst", "delay_ms": "Delay ms"}
CODE_TXT = {
    "E_SCHEMA": "Ungültiger Wert (Schema)",
    "E_WEIGHT_SUM": "Summe der Gewichte ist nicht 100 %",
    "E_DESTRUCTIVE_NOT_ALLOWED": "Schreibtest auf Volume/Disk ohne Freigabe",
    "E_BLOCK_ALIGN": "Kein Vielfaches der Sektorgröße (unbuffered)",
    "E_TARGET_NOT_FOUND": "Ziel bzw. Verzeichnis existiert nicht",
    "E_TARGET_SIZE": "Ziel kleiner als die Blockgröße",
    "E_UDP_RATE": "UDP-Rate fehlt/unzulässig",
    "W_NEEDS_ADMIN": "Administratorrechte nötig",
    "W_DESTRUCTIVE": "Schreibtest zerstört Daten",
    "W_LOW_DISK_SPACE": "Wenig freier Speicherplatz",
}


def parse_path(ptr):
    """'/tests/2/targets/0/path' -> {"test": 2, "area": "targets", "index": 0, "field": "path"} (missing parts None)."""
    p = [x for x in (ptr or "").split("/")][1:]
    out = {"test": None, "area": None, "index": None, "field": None}
    if len(p) >= 2 and p[0] == "tests" and p[1].isdigit():
        out["test"] = int(p[1])
        rest = p[2:]
        if rest and rest[0] in ("targets", "access_specs"):
            out["area"] = rest[0]
            if len(rest) >= 2 and rest[1].isdigit():
                out["index"] = int(rest[1])
            if len(rest) >= 3:
                out["field"] = rest[2]
        elif rest:
            out["area"] = "field"; out["field"] = rest[0]
    return out


def field_name(ptr, job=None):
    """German, e.g. 'Test 2 »4K Random Write« · Ziel 1 · Pfad'."""
    d = parse_path(ptr)
    if d["test"] is None:
        return "Job" if not ptr or ptr == "/" else ptr
    parts = [f"Test {d['test'] + 1}"]
    try:
        t = job["tests"][d["test"]]
        if t.get("name"):
            parts[0] += f" »{t['name']}«"
    except (TypeError, KeyError, IndexError):
        pass
    if d["area"] == "targets":
        parts.append(f"Ziel {d['index'] + 1}" if d["index"] is not None else "Ziele")
        if d["field"]:
            parts.append(TARGET_TXT.get(d["field"], d["field"]))
    elif d["area"] == "access_specs":
        if d["index"] is None:
            parts.append("Access Specs · Gewichtssumme")
        else:
            parts.append(f"Spec {d['index'] + 1}")
            if d["field"]:
                parts.append(SPEC_TXT.get(d["field"], d["field"]))
    elif d["area"] == "field":
        parts.append(FIELD_TXT.get(d["field"], d["field"]))
    return " · ".join(parts)


def reason(issue):
    """German reason: short text for the code + the core's (German) message."""
    short = CODE_TXT.get(issue.get("code"), issue.get("code") or "")
    msg = (issue.get("msg") or "").strip()
    return f"{short}: {msg}" if msg and short else (msg or short)


def entries(res, job=None):
    """[(is_error, field name, reason, pointer, code)] errors first, then warnings."""
    out = []
    for is_err, key in ((True, "errors"), (False, "warnings")):
        for e in (res or {}).get(key) or []:
            out.append((is_err, field_name(e.get("path"), job), reason(e), e.get("path") or "", e.get("code") or ""))
    return out
