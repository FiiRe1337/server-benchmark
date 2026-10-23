"""Tolerant loading of user state left behind by older GUI versions (or by interrupted runs).

The GUI keeps its state only in plain files, no registry/QSettings:
  %LOCALAPPDATA%\\sbench-gui\\profiles\\*.json   saved sb-job/1 profiles (ProfileEditor)
  %LOCALAPPDATA%\\sbench-gui\\results\\*.json    sb-result/1 of runs started from the GUI
  %LOCALAPPDATA%\\sbench-gui\\logs\\gui-YYYYMMDD.log
A file that cannot be used (empty/truncated after a crash or reboot during a run, UTF-8 BOM, foreign JSON, structure of
an older contract the pages cannot render) must never stop the GUI from starting (v0.2.0-beta.1: an unreadable result
raised in Compare.__init__ while MainWindow was being built). Such a file is renamed to
<name>.defekt-YYYYMMDD-HHMMSS (kept as a backup, no longer matched by *.json), logged, and reported once in a German
warning after the window is shown. Only files inside the user's own folders are renamed; bundled profiles/demo/schema
examples are never touched (only skipped + logged)."""
import datetime as dt, json, logging, os, shutil

from .model import SchemaError, check_schema

log = logging.getLogger("sbench-gui")
RESULT_ROLES = ("host", "guest", "none")
_state = {"quarantined": [], "skip": set()}   # quarantined: [(original path, backup path or None, reason)] until shown


def read_json(path):
    """json.load that also accepts a UTF-8 BOM (files written by PowerShell / Notepad / the core's info.json)."""
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def result_problem(doc):
    """None if the sb-result/1 has everything the pages access without .get(); else a short German reason."""
    if not isinstance(doc, dict):
        return f"kein sb-result/1 (JSON-{type(doc).__name__} statt Objekt)"
    try:
        check_schema(doc, "sb-result/1")
    except SchemaError as e:
        return f"kein sb-result/1 ({e})"
    meta = doc.get("meta")
    if not isinstance(meta, dict):
        return "meta fehlt"
    if not isinstance(meta.get("host"), str):
        return "meta.host fehlt"
    hv = meta.get("hypervisor")
    if not isinstance(hv, dict) or hv.get("role") not in RESULT_ROLES:
        return "meta.hypervisor.role fehlt oder unbekannt"
    try:
        dt.datetime.fromisoformat(str(doc.get("started_utc")).replace("Z", "+00:00"))
    except ValueError:
        return "started_utc fehlt oder ist kein Zeitstempel"
    tests = doc.get("tests")
    if not isinstance(tests, list):
        return "tests fehlt"
    for i, t in enumerate(tests):
        if not isinstance(t, dict) or not isinstance(t.get("type"), str):
            return f"tests[{i}] ohne type"
        if t["type"] == "storage":
            cfg = t.get("config")
            if not isinstance(cfg, dict) or not isinstance(cfg.get("access_specs"), list):
                return f"tests[{i}].config.access_specs fehlt"
            for s in cfg["access_specs"]:
                if not isinstance(s, dict) or not _is_num(s.get("read_pct")) or not _is_num(s.get("random_pct")):
                    return f"tests[{i}].config.access_specs unvollständig"
    return None


def profile_problem(doc):
    """None if the sb-job/1 can be listed/opened by the profile editor; else a short German reason."""
    if not isinstance(doc, dict):
        return f"kein sb-job/1 (JSON-{type(doc).__name__} statt Objekt)"
    try:
        check_schema(doc, "sb-job/1")
    except SchemaError as e:
        return f"kein sb-job/1 ({e})"
    if doc.get("name") is not None and not isinstance(doc.get("name"), str):
        return "name ist kein Text"
    tests = doc.get("tests")
    if not isinstance(tests, list) or not tests:
        return "tests fehlt oder ist leer"
    for i, t in enumerate(tests):
        if not isinstance(t, dict) or not isinstance(t.get("type"), str):
            return f"tests[{i}] ohne type"
        if t["type"] == "storage":
            if not isinstance(t.get("targets"), list) or not all(isinstance(x, dict) for x in t["targets"]):
                return f"tests[{i}].targets fehlt"
            if not isinstance(t.get("access_specs"), list) or not all(isinstance(x, dict) for x in t["access_specs"]):
                return f"tests[{i}].access_specs fehlt"
    return None


def check_file(path, kind):
    """(doc, None) or (None, reason). kind: 'result' | 'profile'."""
    try:
        doc = read_json(path)
    except OSError as e:
        return None, f"nicht lesbar ({e.strerror or e})"
    except UnicodeDecodeError:
        return None, "keine UTF-8-Datei"
    except ValueError as e:
        return None, "leer" if _empty(path) else f"kein gültiges JSON ({e})"
    try:
        why = (result_problem if kind == "result" else profile_problem)(doc)
    except Exception as e:  # noqa: BLE001 -- the check itself must never break the start
        why = f"unerwarteter Inhalt ({type(e).__name__}: {e})"
    return (None, why) if why else (doc, None)


def _empty(path):
    try:
        return os.path.getsize(path) == 0
    except OSError:
        return False


def _inside(path, d):
    try:
        return bool(d) and os.path.normcase(os.path.dirname(os.path.abspath(path))) == os.path.normcase(os.path.abspath(d))
    except (TypeError, ValueError):
        return False


def quarantine(path, reason, user_dir=None):
    """Rename an unusable user file to <name>.defekt-<timestamp> (backup, never deleted) and remember it for the warning.
    Files outside user_dir (bundled profiles, demo, schema examples) are only skipped. Returns the backup path or None."""
    if is_quarantined(path):
        return None
    _state["skip"].add(os.path.normcase(os.path.abspath(path)))
    backup = None
    if user_dir is None or _inside(path, user_dir):
        backup = f"{path}.defekt-{dt.datetime.now():%Y%m%d-%H%M%S}"
        try:
            os.replace(path, backup)
        except OSError as e:
            log.warning("Datei %s unbrauchbar (%s), Umbenennen fehlgeschlagen: %s – wird übersprungen", path, reason, e)
            backup = None
    if backup:
        log.warning("Datei %s unbrauchbar (%s) – gesichert als %s", path, reason, backup)
    else:
        log.warning("Datei %s unbrauchbar (%s) – wird übersprungen", path, reason)
    _state["quarantined"].append((path, backup, reason))
    return backup


def user_results_dir():
    from . import sbench
    return os.path.join(sbench.gui_dir(), "results")


def is_quarantined(path):
    """True if this path was already found unusable in this session (also when renaming it failed, e.g. read-only)."""
    return os.path.normcase(os.path.abspath(path)) in _state["skip"]


def load_results(files, user_dir="auto"):
    """[(path, sb-result/1)] of all usable files; unusable ones are quarantined (inside user_dir, default
    %LOCALAPPDATA%\\sbench-gui\\results) or skipped."""
    user_dir = user_results_dir() if user_dir == "auto" else user_dir
    out = []
    for f in files:
        if is_quarantined(f):
            continue
        doc, why = check_file(f, "result")
        if why:
            quarantine(f, why, user_dir)
        else:
            out.append((f, doc))
    return out


def sanitize_user_dir(gui_dir):
    """Startup: check every *.json in <gui_dir>/profiles and <gui_dir>/results before any page is built."""
    for sub, kind in (("profiles", "profile"), ("results", "result")):
        d = os.path.join(gui_dir, sub)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            p = os.path.join(d, f)
            if f.lower().endswith(".json") and os.path.isfile(p):
                _, why = check_file(p, kind)
                if why:
                    quarantine(p, why, d)


def backup_user_state(gui_dir):
    """Last resort when the main window still cannot be built: move profiles/ and results/ to backup-<timestamp>/ (the GUI
    then starts with the bundled profiles only). Returns the backup dir or None if there was nothing to move."""
    dst = os.path.join(gui_dir, f"backup-{dt.datetime.now():%Y%m%d-%H%M%S}")
    moved = False
    for sub in ("profiles", "results"):
        src = os.path.join(gui_dir, sub)
        if os.path.isdir(src) and os.listdir(src):
            os.makedirs(dst, exist_ok=True)
            try:
                shutil.move(src, os.path.join(dst, sub)); moved = True
            except OSError as e:
                log.error("Sicherung von %s fehlgeschlagen: %s", src, e)
    if moved:
        log.warning("Benutzerdaten nach %s verschoben (Hauptfenster ließ sich damit nicht aufbauen)", dst)
        _state["quarantined"].append((os.path.join(gui_dir, "profiles|results"), dst, "Hauptfenster ließ sich damit nicht aufbauen"))
    return dst if moved else None


def take_quarantined():
    q = list(_state["quarantined"]); _state["quarantined"].clear()
    return q


def peek_quarantined():
    return list(_state["quarantined"])


def warning_text(items):
    """German text for the one warning after start (None if nothing was quarantined)."""
    if not items:
        return None
    lines = []
    for path, backup, reason in items[:8]:
        lines.append(f"• {os.path.basename(path)}: {reason}" + (f"\n   gesichert als {os.path.basename(backup)}" if backup else " (übersprungen)"))
    if len(items) > 8:
        lines.append(f"… und {len(items) - 8} weitere (siehe Protokoll)")
    return ("Einige gespeicherte Dateien einer älteren Version konnten nicht gelesen werden und werden nicht mehr geladen. "
            "Sie wurden nicht gelöscht, sondern umbenannt:\n\n" + "\n".join(lines))
