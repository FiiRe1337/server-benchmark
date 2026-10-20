"""Core binary handling (security requirements by Devend, PR #10 / docs/cli.md "Datenverzeichnis").

Frozen build (PyInstaller onefile): sbench.exe is embedded and unpacked by the bootloader into the per-start
temp dir sys._MEIPASS (_MEIxxxx). It is NEVER executed from there:

1. data_dir = <dir of sbench-gui.exe>\\data if writable (real test write, os.access ignores ACLs), else %ProgramData%\\sbench.
2. The embedded copy is checked against the SHA-256 baked in at build time (sbgui/_build_info.py) and copied to
   <data_dir>\\bin\\sbench-<core_version>.exe if missing or different (atomic: temp file + os.replace).
3. Before EVERY start of sbench (incl. runas/UAC) the installed copy is hashed again; on mismatch the launch is refused
   (CoreIntegrityError, shown to the user) and the copy is restored from the embedded binary for the next attempt.
   On Windows the verified file stays open with FILE_SHARE_READ only (no write/delete sharing) while the GUI runs,
   so it cannot be swapped between check and start (TOCTOU) by a non-admin user.
4. Every sbench call gets `--data-dir <data_dir>` (runas does not inherit the environment, so SBENCH_DATA_DIR is not used).

Source runs (dev/tests): $SBENCH_EXE if set (unverified unless $SBENCH_EXE_SHA256), else the mock (python mock_sbench.py).
"""
import hashlib, logging, os, shutil, sys, tempfile, threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEV_VERSION = "0.1.0-dev"
log = logging.getLogger("sbench-gui")


class CoreError(RuntimeError):
    """Core missing / not installable. Message is user-facing (German)."""


class CoreIntegrityError(CoreError):
    """SHA-256 of the core binary does not match the value embedded at build time."""


# ------------------------------------------------------------------ build info / versions
def build_info():
    """Values generated at build time (tools/gen_build_info.py -> sbgui/_build_info.py); dev defaults otherwise."""
    d = {"version": DEV_VERSION, "commit": "dev", "core_sha256": None, "core_version": None, "built_utc": None}
    try:
        from . import _build_info as b   # generated, not in git
        d.update({k: getattr(b, k.upper()) for k in d if hasattr(b, k.upper())})
    except ImportError:
        pass
    return d


def gui_version():
    return build_info()["version"] or DEV_VERSION


def frozen():
    return bool(getattr(sys, "frozen", False))


# ------------------------------------------------------------------ data dir
def _writable_dir(d):
    """True if d exists/can be created and a file can actually be created in it (ACL-proof, unlike os.access)."""
    try:
        os.makedirs(d, exist_ok=True)
        fd, p = tempfile.mkstemp(prefix=".wtest-", dir=d)
        os.close(fd); os.remove(p)
        return True
    except OSError:
        return False


def is_within(path, root):
    try:
        return os.path.commonpath([os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(root))]) == os.path.normcase(os.path.abspath(root))
    except ValueError:   # different drives
        return False


def program_data_dir(env=None):
    env = os.environ if env is None else env
    return os.path.join(env.get("ProgramData") or env.get("PROGRAMDATA") or r"C:\ProgramData", "sbench")


def select_data_dir(exe_dir, env=None, writable=_writable_dir):
    """`data` next to the GUI EXE if writable (portable), else %ProgramData%\\sbench (created if needed)."""
    portable = os.path.join(exe_dir, "data")
    if writable(portable):
        return os.path.abspath(portable)
    pd = program_data_dir(env)
    if not writable(pd):
        raise CoreError(f"Kein beschreibbares Datenverzeichnis: weder {portable} noch {pd}")
    return os.path.abspath(pd)


# ------------------------------------------------------------------ hashing / locking
def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class _ShareReadLock:
    """Windows: handle on the file with FILE_SHARE_READ only -> nobody can open it for write/delete/rename while held.
    The loader can still map it for execution (needs read sharing only). No-op elsewhere."""

    def __init__(self, path):
        self.path = path; self.handle = None
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING = 0x80000000, 0x1, 3
        h = k32.CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, None, OPEN_EXISTING, 0, None)
        if h in (None, wintypes.HANDLE(-1).value):
            raise OSError(ctypes.get_last_error(), f"CreateFileW({path}) fehlgeschlagen (Datei in Benutzung?)")
        self.handle = h; self._k32 = k32

    def close(self):
        if self.handle is not None:
            self._k32.CloseHandle(self.handle); self.handle = None


# ------------------------------------------------------------------ install + verify
def core_target(data_dir, core_version):
    safe = "".join(c if c.isalnum() or c in ".-_" else "_" for c in (core_version or "unknown"))
    return os.path.join(data_dir, "bin", f"sbench-{safe}.exe")


def install_core(embedded, data_dir, core_version, expected_sha):
    """Copy embedded -> <data_dir>/bin/sbench-<ver>.exe if missing or hash differs. Returns the target path.
    Raises CoreIntegrityError if the embedded copy itself does not match (broken/tampered build)."""
    if not expected_sha:
        raise CoreIntegrityError("Build ohne eingebetteten SHA-256 des Kerns – Start verweigert.")
    if not embedded or not os.path.isfile(embedded):
        raise CoreError("Eingebetteter Kern sbench.exe fehlt im Build.")
    got = sha256_file(embedded)
    if got != expected_sha.lower():
        raise CoreIntegrityError(f"Eingebetteter Kern beschädigt: SHA-256 {got} ≠ erwartet {expected_sha.lower()}")
    target = core_target(data_dir, core_version)
    if os.path.isfile(target):
        try:
            if sha256_file(target) == expected_sha.lower():
                return target
        except OSError:
            pass
        log.warning("core %s differs from embedded copy, replacing", target)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".sbench-", suffix=".tmp", dir=os.path.dirname(target))
    os.close(fd)
    try:
        shutil.copyfile(embedded, tmp)
        if os.name != "nt":
            os.chmod(tmp, 0o755)       # mkstemp creates 0600; POSIX needs +x (dev/Linux test of the frozen build)
        if sha256_file(tmp) != expected_sha.lower():
            raise CoreIntegrityError(f"Kopie von {embedded} nach {tmp} fehlerhaft (SHA-256)")
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    log.info("core installed: %s", target)
    return target


class Core:
    """Resolved core: base command, data_dir and (for verified builds) expected SHA-256 + embedded source copy."""

    def __init__(self, base_cmd, data_dir, mode, expected_sha=None, embedded=None, core_version=None):
        self.base_cmd = list(base_cmd); self.data_dir = data_dir; self.mode = mode
        self.expected_sha = expected_sha.lower() if expected_sha else None
        self.embedded = embedded; self.core_version = core_version
        self._lock = None; self._mx = threading.Lock()

    @property
    def exe(self):
        return self.base_cmd[0] if self.mode != "mock" else self.base_cmd[-1]

    def verify(self):
        """Hash check right before a launch. Mismatch -> repair from embedded copy (if any) and refuse THIS launch."""
        if not self.expected_sha:
            return
        with self._mx:
            path = self.base_cmd[0]
            try:
                got = sha256_file(path)
            except OSError as e:
                got = f"nicht lesbar ({e})"
            if got == self.expected_sha:
                if self._lock is None:
                    try:
                        self._lock = _ShareReadLock(path)
                    except OSError as e:   # still verified; only the TOCTOU guard is missing
                        log.warning("core lock failed: %s", e)
                return
            self.release()
            repaired = ""
            if self.embedded:
                try:
                    install_core(self.embedded, self.data_dir, self.core_version, self.expected_sha); repaired = " Die Datei wurde aus der eingebetteten Kopie wiederhergestellt; bitte den Vorgang erneut starten."
                except (OSError, CoreError) as e:
                    repaired = f" Wiederherstellung fehlgeschlagen: {e}"
            log.error("core integrity mismatch %s: %s != %s", path, got, self.expected_sha)
            raise CoreIntegrityError(f"Integritätsprüfung von sbench fehlgeschlagen – Start verweigert.\n{path}\n"
                                     f"SHA-256 ist {got}\nerwartet  {self.expected_sha}.{repaired}")

    def release(self):
        if self._lock is not None:
            self._lock.close(); self._lock = None

    def command(self, args, verify=True):
        """Full argv for one sbench call: <core> --data-dir <data_dir> <args...> (hash verified first)."""
        if verify:
            self.verify()
        return self.base_cmd + ["--data-dir", self.data_dir] + list(args)


def resolve(env=None):
    """Pick the core for this process. Frozen: embedded real core only (never mock, never _MEI). Source: $SBENCH_EXE or mock."""
    env = os.environ if env is None else env
    bi = build_info()
    if frozen():
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        data_dir = select_data_dir(exe_dir, env)
        embedded = os.path.join(getattr(sys, "_MEIPASS", exe_dir), "core", "sbench.exe")
        if is_within(data_dir, getattr(sys, "_MEIPASS", "/nonexistent")):
            raise CoreError(f"Datenverzeichnis {data_dir} liegt im PyInstaller-Temp-Verzeichnis – Start verweigert")
        target = install_core(embedded, data_dir, bi["core_version"] or bi["version"], bi["core_sha256"])
        return Core([target], data_dir, "embedded", bi["core_sha256"], embedded, bi["core_version"] or bi["version"])
    data_dir = os.path.abspath(env.get("SBENCH_DATA_DIR") or "") if env.get("SBENCH_DATA_DIR") else select_data_dir(ROOT, env)
    if env.get("SBENCH_EXE"):
        return Core([env["SBENCH_EXE"]], data_dir, "env", env.get("SBENCH_EXE_SHA256"))
    return Core([sys.executable, os.path.join(ROOT, "mock_sbench.py")], data_dir, "mock")


_CORE = None


def get():
    global _CORE
    if _CORE is None:
        _CORE = resolve()
        log.info("core %s mode=%s data_dir=%s", _CORE.base_cmd, _CORE.mode, _CORE.data_dir)
    return _CORE


def reset():
    """Tests: forget the cached core (env changed)."""
    global _CORE
    if _CORE is not None:
        _CORE.release()
    _CORE = None
