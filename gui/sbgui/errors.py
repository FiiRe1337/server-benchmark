"""Global error handler: no click may fail silently in the windowed EXE (console=False -> no stderr).

PySide6 hands exceptions raised in slots / virtual overrides to sys.excepthook (PyErr_Print) and then carries on;
without a hook they only end up on stderr, which a windowed PyInstaller EXE does not have. install() logs every
uncaught exception with traceback to <gui_dir>/logs/gui-YYYYMMDD.log (the same log `sbench bundle --include` takes
into the support ZIP) and shows a German message box (at most one at a time, never recursive)."""
import datetime as dt, logging, os, sys, threading, traceback

log = logging.getLogger("sbench-gui")
_state = {"installed": False, "showing": False, "log_file": None, "quiet": False, "seen": []}


def log_file():
    return _state["log_file"]


def _ensure_log(log_dir):
    """Log file handler even if setup_logging() was not called (self-test, screenshot modes)."""
    if not log_dir:
        return None
    try:
        os.makedirs(log_dir, exist_ok=True)
    except OSError:
        return None
    f = os.path.join(log_dir, f"gui-{dt.date.today():%Y%m%d}.log")
    root = logging.getLogger()
    if not any(isinstance(h, logging.FileHandler) and os.path.abspath(getattr(h, "baseFilename", "")) == os.path.abspath(f)
               for h in root.handlers + log.handlers):
        h = logging.FileHandler(f, encoding="utf-8"); h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        log.addHandler(h)
        if log.level == logging.NOTSET or log.level > logging.INFO:
            log.setLevel(logging.INFO)
    return f


def report(exc_type, exc, tb, where="GUI"):
    """Log + message box. Returns the formatted traceback (also kept in _state['seen'] for the click self-test)."""
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    _state["seen"].append(text)
    try:
        log.error("Unbehandelte Ausnahme (%s):\n%s", where, text)
    except Exception:  # noqa: BLE001 -- logging must never raise here
        pass
    if sys.stderr:
        try:
            sys.stderr.write(text)
        except Exception:  # noqa: BLE001
            pass
    if _state["quiet"] or _state["showing"]:
        return text
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance()
        if app is None or threading.current_thread() is not threading.main_thread():
            return text
        _state["showing"] = True
        try:
            box = QMessageBox(QMessageBox.Critical, "sbench – unerwarteter Fehler",
                              f"Die Aktion ist fehlgeschlagen: {exc_type.__name__}: {exc}\n\n"
                              f"Details stehen im Protokoll:\n{_state['log_file'] or '(kein Protokoll)'}\n\n"
                              "Bitte bei Problemen »Ergebnisse für Support exportieren« verwenden (enthält das Protokoll).",
                              QMessageBox.Ok, app.activeWindow())
            box.setDetailedText(text[-6000:])
            box.exec()
        finally:
            _state["showing"] = False
    except Exception:  # noqa: BLE001 -- the handler itself must never raise
        pass
    return text


def _hook(exc_type, exc, tb):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb); return
    report(exc_type, exc, tb)


def _thread_hook(args):
    if args.exc_type is SystemExit:
        return
    report(args.exc_type, args.exc_value, args.exc_traceback, where=f"Thread {getattr(args.thread, 'name', '?')}")


def install(log_dir=None, quiet=False):
    """Idempotent. quiet=True: log only, no message box (click self-test collects the exceptions instead)."""
    _state["quiet"] = quiet
    if log_dir:
        _state["log_file"] = _ensure_log(log_dir) or _state["log_file"]
    if not _state["installed"]:
        sys.excepthook = _hook
        threading.excepthook = _thread_hook
        _state["installed"] = True
    return _state["log_file"]


def take_seen():
    """Exceptions reported since the last call (click self-test)."""
    out = list(_state["seen"]); _state["seen"].clear(); return out
