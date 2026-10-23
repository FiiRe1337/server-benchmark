"""Crash log for the windowed EXE (console=False): no crash may end the GUI silently.

Installed FIRST in __main__.py, before PySide6 or any other sbgui module is imported (stdlib only here), so it also
covers failures at import time / before the QApplication exists (missing DLL, broken bundle, exception in
build_window ...). Everything goes to %LOCALAPPDATA%\\sbench\\crash.log (Linux dev: ~/.local/share/sbench/crash.log,
override: $SBENCH_CRASH_DIR):

* unhandled Python exceptions (sys.excepthook, threading.excepthook, the try/except around main() in __main__.py),
* native crashes (access violation, stack overflow, ...) via faulthandler, which writes the Python stack of every
  thread into the same file,
* fatal Qt messages (qFatal -> abort/fail-fast), see errors.qt_message_handler.

After writing, a message box is shown: QMessageBox if a QApplication exists, otherwise the plain Win32 MessageBoxW via
ctypes (works without Qt). No box in non-interactive modes (self-test, click test, screenshots, guide, $SBENCH_NO_DIALOGS=1),
so CI never hangs on a modal dialog; those modes only write the file (CI fails if crash.log is not empty).
The GUI log %LOCALAPPDATA%\\sbench-gui\\logs\\gui-YYYYMMDD.log (sbgui/errors.py) stays as it is."""
import datetime as dt, faulthandler, os, sys, threading, traceback

NON_INTERACTIVE = ("--self-test", "--self-test-child", "--self-test-clicks", "--self-test-crash", "--smoke-start", "--screenshot", "--guide")
TITLE = "sbench – Programmfehler"
_state = {"installed": False, "fh_file": None, "fh_size": 0, "showing": False}


def crash_dir(env=None):
    env = os.environ if env is None else env
    if env.get("SBENCH_CRASH_DIR"):
        return env["SBENCH_CRASH_DIR"]
    base = env.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "sbench")


def crash_file(env=None):
    return os.path.join(crash_dir(env), "crash.log")


def interactive(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if os.environ.get("SBENCH_NO_DIALOGS") == "1":
        return False
    return not any(a.split("=", 1)[0] in NON_INTERACTIVE for a in argv)


def _version():
    try:
        from . import _build_info as b   # generated at build time; not in git
        return f"{getattr(b, 'VERSION', '?')} (Commit {str(getattr(b, 'COMMIT', '?'))[:7]})"
    except Exception:  # noqa: BLE001 -- never fail while reporting a failure
        return "0.1.0-dev (Quelle)"


def header(where):
    return (f"===== {dt.datetime.now().astimezone():%Y-%m-%d %H:%M:%S %z} · {where} · sbench-gui {_version()} · "
            f"Python {sys.version.split()[0]} · PID {os.getpid()} · frozen={bool(getattr(sys, 'frozen', False))}\n"
            f"argv: {sys.argv!r}\n")


def append(text, where="Absturz"):
    """Append one entry to crash.log. Returns the file path or None (never raises)."""
    try:
        f = crash_file()
        os.makedirs(os.path.dirname(f), exist_ok=True)
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(header(where) + text.rstrip("\n") + "\n\n")
        return f
    except Exception:  # noqa: BLE001
        return None


def message_box(title, text):
    """Modal error box without depending on a working Qt: QMessageBox if a QApplication runs in this (main) thread,
    else Win32 MessageBoxW. Returns True if a box was shown."""
    if _state["showing"] or not interactive() or threading.current_thread() is not threading.main_thread():
        return False
    _state["showing"] = True
    try:
        if "PySide6.QtWidgets" in sys.modules:
            try:
                from PySide6.QtWidgets import QApplication, QMessageBox
                if QApplication.instance() is not None:
                    QMessageBox.critical(None, title, text); return True
            except Exception:  # noqa: BLE001 -- fall through to the Win32 box
                pass
        if os.name == "nt":
            import ctypes
            MB_ICONERROR, MB_TOPMOST, MB_SETFOREGROUND = 0x10, 0x40000, 0x10000
            ctypes.windll.user32.MessageBoxW(None, text, title, MB_ICONERROR | MB_TOPMOST | MB_SETFOREGROUND)
            return True
        if sys.stderr:
            sys.stderr.write(f"{title}: {text}\n")
        return False
    except Exception:  # noqa: BLE001
        return False
    finally:
        _state["showing"] = False


def report(exc_type, exc, tb, where="Start", show=True):
    """Unhandled exception -> crash.log (+ GUI log if configured) + message box. Returns the formatted traceback."""
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    f = append(text, where)
    try:
        import logging
        logging.getLogger("sbench-gui").error("Unbehandelte Ausnahme (%s), Absturzprotokoll %s:\n%s", where, f, text)
    except Exception:  # noqa: BLE001
        pass
    if sys.stderr:
        try:
            sys.stderr.write(text)
        except Exception:  # noqa: BLE001
            pass
    if show:
        message_box(TITLE, f"sbench-gui wurde wegen eines Fehlers beendet ({where}):\n\n{exc_type.__name__}: {exc}\n\n"
                           f"Details stehen im Absturzprotokoll:\n{f or crash_file()}\n\n"
                           "Bitte diese Datei bei der Fehlermeldung mitschicken.")
    return text


def _sys_hook(exc_type, exc, tb):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb); return
    report(exc_type, exc, tb, where="Start")


def _thread_hook(args):
    if args.exc_type is SystemExit:
        return
    report(args.exc_type, args.exc_value, args.exc_traceback, where=f"Thread {getattr(args.thread, 'name', '?')}", show=False)


def install():
    """Idempotent. Call before importing PySide6. sbgui.errors.install() later replaces sys.excepthook with the GUI hook
    (log + box per failed action, the GUI keeps running); that hook also appends to crash.log."""
    if _state["installed"]:
        return crash_file()
    sys.excepthook = _sys_hook
    threading.excepthook = _thread_hook
    _enable_faulthandler()
    _state["installed"] = True
    import atexit
    atexit.register(_cleanup)
    return crash_file()


def _enable_faulthandler():
    """faulthandler needs an open file at crash time: crash.log is opened (append) now; an empty file is removed again
    at a normal exit (on Windows only if no other sbench-gui process still holds it open)."""
    try:
        f = crash_file()
        os.makedirs(os.path.dirname(f), exist_ok=True)
        fh = open(f, "a", encoding="utf-8")
        _state["fh_file"] = fh; _state["fh_size"] = fh.tell()
        faulthandler.enable(file=fh, all_threads=True)
    except Exception:  # noqa: BLE001 -- no writable LOCALAPPDATA: at least stderr (console runs)
        if sys.stderr:
            try:
                faulthandler.enable(all_threads=True)
            except Exception:  # noqa: BLE001
                pass


def _cleanup():
    fh = _state.get("fh_file")
    if fh is None:
        return
    try:
        faulthandler.disable()
        fh.close()
        f = crash_file()
        if os.path.isfile(f) and os.path.getsize(f) == 0:
            os.remove(f)
    except Exception:  # noqa: BLE001
        pass
