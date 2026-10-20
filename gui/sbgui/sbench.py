"""Binding layer to the core CLI `sbench.exe` (QProcess + JSON lines). No ctypes/DLL for measurements.

Contract: FiiRe1337/server-benchmark main, Vertrag 1.1 + PR #10 (docs/cli.md). The data_dir is chosen once by
sbgui.core (data\\ next to the GUI EXE if writable, else %ProgramData%\\sbench) and passed as `--data-dir` on EVERY
call (runas processes don't inherit the environment). Displayed paths come from meta.data_dir of `sbench info --json`.
The GUI's own state (logs, profiles, results it requested via --out) lives in gui_dir()."""
import json, logging, os, subprocess
from PySide6.QtCore import QObject, QProcess, Signal, QTimer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXIT_TEXT = {0: "ok", 1: "Fehler", 2: "ungültiger Job", 3: "abgebrochen", 4: "fehlende Rechte"}
log = logging.getLogger("sbench-gui")


def core():
    """Resolved core (sbgui.core): frozen -> verified copy in <data_dir>\\bin, source -> $SBENCH_EXE or mock."""
    from . import core as _core
    return _core.get()


def command(args, verify=True):
    """argv for ONE sbench call: <core> --data-dir <data_dir> <args>. Verifies the core's SHA-256 first
    (raises core.CoreIntegrityError on mismatch). Every launch path below goes through here."""
    return core().command(args, verify=verify)


def sbench_command():
    """Base command incl. --data-dir (no args, not verified; for logging/tests)."""
    return command([], verify=False)


def gui_dir():
    """GUI-owned state: $SBENCH_GUI_DIR, else %LOCALAPPDATA%\\sbench-gui (Linux dev: ~/.local/share/sbench-gui).
    Subdirs: logs/ (GUI log, bundled via --include), profiles/ (user profiles, bundled via --include), results/ (--out)."""
    d = os.environ.get("SBENCH_GUI_DIR") or os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share"), "sbench-gui")
    for sub in ("logs", "profiles", "results"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    return d


def setup_logging():
    import datetime as dt
    f = os.path.join(gui_dir(), "logs", f"gui-{dt.date.today():%Y%m%d}.log")
    logging.basicConfig(filename=f, level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return f


def run_sync(args, timeout_ms=15000):
    p = QProcess(); cmd = command(args)
    p.start(cmd[0], cmd[1:])
    if not p.waitForFinished(timeout_ms):
        p.kill(); raise TimeoutError(" ".join(args))
    out = bytes(p.readAllStandardOutput()).decode("utf-8", "replace")
    err = bytes(p.readAllStandardError()).decode("utf-8", "replace")
    log.info("sbench %s -> %s%s", " ".join(args[:2]), p.exitCode(), f" ({err.strip()[:200]})" if err.strip() else "")
    return p.exitCode(), out


def info():
    code, out = run_sync(["info", "--json"]); return json.loads(out)


def validate(job_path):
    code, out = run_sync(["validate", "--job", job_path])
    res = json.loads(out); res.setdefault("warnings", [])  # optional in schema, always emitted by sbench >= 1.1
    return res


def list_running():
    """`sbench list --running --json` -> sb-runs/1 object; [] on any failure (older core: command unknown)."""
    try:
        code, out = run_sync(["list", "--running", "--json"], 8000)
        doc = json.loads(out) if code == 0 else {}
        return doc.get("runs", []) if doc.get("schema") == "sb-runs/1" else []
    except (TimeoutError, ValueError):
        return []


def import_icf(icf_path, out_path):
    """M2: `sbench import-icf <f> --out <job>` -> (exit code, sb-validate/1 with W_ICF_UNSUPPORTED warnings or None)."""
    code, out = run_sync(["import-icf", icf_path, "--out", out_path], 20000)
    try:
        res = json.loads(out); res.setdefault("warnings", [])
    except ValueError:
        res = None
    return code, res


class EventSource(QObject):
    """Common signals for live process, elevated pipe run, re-attach tail and replay."""
    event = Signal(dict)          # every parsed JSON line (sb-progress/1)
    finished = Signal(int, str)   # exit code, text
    stderr = Signal(str)
    run_id = ""

    def _emit_lines(self, data, buf):
        buf += data
        *lines, buf = buf.split(b"\n")
        for ln in lines:
            if ln.strip():
                try:
                    self.event.emit(json.loads(ln))
                except json.JSONDecodeError:
                    self.stderr.emit(f"bad line: {ln[:120]!r}")
        return buf


def run_args(job_path, out_path, run_id, tick_ms, allow_destructive, progress):
    a = ["run", "--job", job_path, "--progress", progress, "--out", out_path, "--tick-ms", str(tick_ms), "--id", run_id]
    return a + (["--allow-destructive"] if allow_destructive else [])


class RunSession(EventSource):
    """Non-elevated: `sbench run ... --progress stdout` via QProcess; parses stdout per line."""

    def __init__(self, job_path, out_path, run_id, tick_ms=250, allow_destructive=False, parent=None):
        super().__init__(parent)
        self.run_id = run_id; self._buf = b""; self.out_path = out_path
        self.proc = QProcess(self)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.readyReadStandardError.connect(lambda: self.stderr.emit(bytes(self.proc.readAllStandardError()).decode("utf-8", "replace")))
        self.proc.finished.connect(self._fin)
        self.args = run_args(job_path, out_path, run_id, tick_ms, allow_destructive, "stdout"); self.cmd = None

    def start(self):
        from .core import CoreError
        try:
            self.cmd = command(self.args)          # SHA-256 check right before the launch
        except CoreError as e:
            log.error("start refused: %s", e); self.stderr.emit(str(e))
            QTimer.singleShot(0, lambda: self.finished.emit(1, "Kern-Integritätsprüfung fehlgeschlagen")); return
        log.info("start %s", subprocess.list2cmdline(self.cmd)); self.proc.start(self.cmd[0], self.cmd[1:])

    def _read(self):
        self._buf = self._emit_lines(bytes(self.proc.readAllStandardOutput()), self._buf)

    def _fin(self, code, _status):
        self._read(); log.info("run %s exit %s", self.run_id, code)
        self.finished.emit(code, EXIT_TEXT.get(code, "?"))

    def stop(self):
        stop_run(self.run_id)
        QTimer.singleShot(5000, lambda: self.proc.state() != QProcess.NotRunning and self.proc.kill())


def stop_run(run_id):
    """Graceful: `sbench stop --id` (named event Global\\sbench-stop-<id>) -> partial result, exit 3."""
    from .core import CoreError
    try:
        cmd = command(["stop", "--id", run_id])
    except CoreError as e:
        log.error("stop refused: %s", e); return False
    return QProcess.startDetached(cmd[0], cmd[1:])


class ElevatedRunSession(EventSource):
    """Elevated: GUI = named-pipe server, `sbench run --progress pipe:\\\\.\\pipe\\sbench-<id>` started via
    ShellExecuteEx 'runas' (UAC). Windows only; see pipe_win.py for DACL / PIPE_REJECT_REMOTE_CLIENTS."""

    def __init__(self, job_path, out_path, run_id, tick_ms=250, allow_destructive=False, parent=None):
        super().__init__(parent)
        self.run_id = run_id; self.out_path = out_path; self._buf = b""
        self.pipe_name = f"\\\\.\\pipe\\sbench-{run_id}"
        self.args = run_args(job_path, out_path, run_id, tick_ms, allow_destructive, f"pipe:{self.pipe_name}"); self.cmd = None

    def start(self):
        from .core import CoreError
        if os.name != "nt":
            QTimer.singleShot(0, lambda: self.finished.emit(1, "Erhöhung nur unter Windows"))
            return
        try:
            self.cmd = command(self.args)          # verified right before ShellExecuteEx runas (elevated!)
        except CoreError as e:
            log.error("elevated start refused: %s", e); self.stderr.emit(str(e))
            QTimer.singleShot(0, lambda: self.finished.emit(1, "Kern-Integritätsprüfung fehlgeschlagen")); return
        from . import pipe_win
        self.reader = pipe_win.PipeServer(self.pipe_name, self)          # create pipe BEFORE starting sbench (10 s connect timeout)
        self.reader.data.connect(lambda b: setattr(self, "_buf", self._emit_lines(b, self._buf)))
        self.reader.start()
        self.proc = pipe_win.RunasProcess(self.cmd, self)
        self.proc.exited.connect(self._fin)
        log.info("start elevated %s", subprocess.list2cmdline(self.cmd)); self.proc.start()

    def _fin(self, code):
        self.reader.close()
        if code == -1223:
            self.finished.emit(-1, "UAC abgebrochen"); return
        self.finished.emit(code, EXIT_TEXT.get(code, "?"))

    def stop(self):
        stop_run(self.run_id)


class TailSession(EventSource):
    """Re-attach after a GUI restart: read <data_dir>/runs/<run_id>.jsonl from the start, then follow it until `done`."""

    def __init__(self, run, parent=None):
        super().__init__(parent)
        self.run = run; self.run_id = run["run_id"]; self.path = run["progress_file"]; self.out_path = run["out"]
        self.pos = 0; self._buf = b""; self.idle = 0; self.done = None
        self.timer = QTimer(self); self.timer.timeout.connect(self.poll)
        self.event.connect(lambda e: e.get("ev") == "done" and setattr(self, "done", e))

    def start(self):
        self.poll(); self.timer.start(250)

    def poll(self):
        try:
            with open(self.path, "rb") as f:
                f.seek(self.pos); data = f.read(); self.pos += len(data)
        except OSError:
            data = b""
        if data:
            self.idle = 0; self._buf = self._emit_lines(data, self._buf)
        else:
            self.idle += 1
        if self.done is not None:
            self.timer.stop(); code = self._exit_code()
            self.finished.emit(code, EXIT_TEXT.get(code, "?")); return
        if self.idle and self.idle % 40 == 0 and not any(r["run_id"] == self.run_id for r in list_running()):
            self.timer.stop(); self.finished.emit(1, "Lauf beendet ohne done-Event")

    def _exit_code(self):
        idx = os.path.join(os.path.dirname(self.path), "index.jsonl")  # {run_id, ..., exit_code}
        try:
            for ln in reversed(open(idx, encoding="utf-8").read().splitlines()):
                e = json.loads(ln)
                if e.get("run_id") == self.run_id:
                    return int(e["exit_code"])
        except (OSError, ValueError, KeyError):
            pass
        return 3 if self.done.get("aborted") else 0

    def stop(self):
        stop_run(self.run_id)


class Replay(EventSource):
    """Feeds a recorded progress.jsonl into the live view. Deterministic: feed(n) has no timing dependence."""

    def __init__(self, path, parent=None):
        super().__init__(parent)
        with open(path, encoding="utf-8") as f:
            self.events = [json.loads(l) for l in f if l.strip()]
        self.pos = 0
        self._timer = QTimer(self); self._timer.timeout.connect(lambda: self.feed(1))

    def feed(self, n=None):
        end = len(self.events) if n is None else min(len(self.events), self.pos + n)
        while self.pos < end:
            self.event.emit(self.events[self.pos]); self.pos += 1
        if self.pos >= len(self.events):
            self._timer.stop(); self.finished.emit(0, "ok")

    def feed_until(self, pred):
        while self.pos < len(self.events) and not pred(self.events[self.pos]):
            self.feed(1)
        self.feed(1)

    def feed_fraction(self, frac):
        self.feed(int(len(self.events) * frac) - self.pos)

    def play(self, interval_ms=100):
        self._timer.start(interval_ms)

    def stop(self):
        self._timer.stop()


def bundle_args(out_path, results=None, include=(), anonymize=False):
    """`bundle --out <zip> (--results ...|--all) [--include <dir>]... [--anonymize] --progress stdout`.
    No selection -> always --all (without it the core would bundle only info.json + includes)."""
    args = ["bundle", "--out", out_path] + (["--results", *results] if results else ["--all"])
    for d in include:
        args += ["--include", d]
    if anonymize:
        args.append("--anonymize")
    return args + ["--progress", "stdout"]


def bundle(out_path, results=None, include=(), anonymize=False, parent=None):
    """Async `sbench bundle ...` (see bundle_args)."""
    p = QProcess(parent)
    args = bundle_args(out_path, results, include, anonymize)
    cmd = command(args)
    log.info("bundle %s", subprocess.list2cmdline(args))
    p.start(cmd[0], cmd[1:]); return p


def bundle_summary(zip_path):
    """(file count, anonymized) from manifest.json of a finished bundle."""
    import zipfile
    try:
        man = json.loads(zipfile.ZipFile(zip_path).read("manifest.json"))
        return len(man.get("files", [])) + 1, bool(man.get("anonymized"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None, None


def new_run_id():
    import datetime as dt, random
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + f"{random.randrange(16**4):04x}"


def version_json():
    """`sbench version --json` -> {version, commit, arch, sha256, formats} (PR #10). None if unsupported."""
    code, out = run_sync(["version", "--json"])
    try:
        return json.loads(out) if code == 0 else None
    except ValueError:
        return None


def kern_version():
    from .core import CoreError
    try:
        v = version_json()
        if v and v.get("version"):
            return v["version"]
        code, out = run_sync(["version"])
        return out.splitlines()[0].split()[1] if code == 0 else "?"
    except CoreError:
        raise
    except Exception:  # noqa: BLE001
        return "?"
