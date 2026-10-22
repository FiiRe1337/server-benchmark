"""Click smoke test: press EVERY button / action of every page and require that nothing raises and nothing is dead.

Used by tests/test_click_smoke.py (source, mock core) AND by `sbench-gui --self-test-clicks` inside the frozen EXE
(real core, CI on windows-2022 after the PyInstaller build), so a dead or crashing button cannot ship again
(v0.1.0-beta.1: '+ Datei' / '+ Volume' / '+ Disk' had no handler at all).

Modal OS dialogs are replaced while it runs: QFileDialog returns a temp path (or cancel for the support ZIP),
QMessageBox / QDialog.exec / QMenu.exec record the call and return at once, QDesktopServices.openUrl is recorded,
the editor's run_requested is recorded instead of MainWindow.start_run (no real benchmark). A watchdog rejects any modal widget that still
appears. In-window overlays (TargetPicker, IssuePopup, ...) that a click opens get their primary button pressed once
(accept path) and are then closed.

A button is
  dead        -> no receiver for clicked/toggled/pressed, no menu, not in a QButtonGroup        (failure)
  failed      -> an exception reached sys.excepthook while it ran                                (failure)
  observable  -> page, overlay, dialog/message call, table/list rows, chip text or run request changed
Buttons in EXPECT must be observable (failure otherwise). Page signals (e.g. Dashboard.quickstart) must be connected.
Pages may offer click_setup() (called once when the page is shown, e.g. results: a result with an error toggle) and
click_prepare(button) (called before each button, e.g. profile editor: select a storage test for '+ Datei', a cpu/memory test
for the cpu/memory form)."""
import json, os, tempfile, time

EXPECT = {"+ Datei", "+ Volume", "+ Disk", "Entfernen", "+ Spec", "Duplizieren", "+ Neu", "Validieren", "Speichern",
          "+ CPU", "+ RAM", "Test entfernen",                     # M2: cpu/memory tests in the profile editor
          "▶  Test starten", "Exportieren", "…", "Import ▾", "Auswahl für Support exportieren", "Auswahl → Support-Paket",
          "⇪  Ergebnisse für Support exportieren"}


def _btn_text(b):
    return (b.text() or b.toolTip() or b.objectName() or type(b).__name__).replace("\n", " ").strip()


class _Patches:
    def __init__(self, win, tmp):
        self.win, self.tmp, self.calls, self.saved = win, tmp, [], []

    def _set(self, owner, name, fn):
        self.saved.append((owner, name, owner.__dict__.get(name, getattr(owner, name)))); setattr(owner, name, fn)

    def __enter__(self):
        from PySide6.QtWidgets import QFileDialog, QMessageBox, QDialog, QMenu
        from PySide6.QtGui import QDesktopServices
        from .app import DEMO
        rec = self.calls.append
        tmp = self.tmp

        def save_name(_parent=None, caption="", *a, **k):
            rec(("QFileDialog.getSaveFileName", caption))
            return ("", "") if "Support" in caption else (os.path.join(tmp, "sbench-click-test.dat"), "")

        def open_name(_parent=None, caption="", _dir="", filt="", *a, **k):
            rec(("QFileDialog.getOpenFileName", caption))
            if ".icf" in filt:
                return os.path.join(DEMO, "iometer-example.icf"), ""
            if ".json" in filt:
                return os.path.join(DEMO, "job-oltp.json"), ""
            return "", ""

        def exist_dir(_parent=None, caption="", *a, **k):
            rec(("QFileDialog.getExistingDirectory", caption)); return tmp

        def msg(kind):
            def f(_parent=None, title="", text="", *a, **k):
                rec((f"QMessageBox.{kind}", f"{title}: {str(text)[:160]}"))
                return QMessageBox.Yes if kind == "question" else QMessageBox.Ok
            return staticmethod(f)

        self._set(QFileDialog, "getSaveFileName", staticmethod(save_name))
        self._set(QFileDialog, "getOpenFileName", staticmethod(open_name))
        self._set(QFileDialog, "getExistingDirectory", staticmethod(exist_dir))
        for kind in ("information", "warning", "critical", "question"):
            self._set(QMessageBox, kind, msg(kind))
        self._set(QMessageBox, "exec", lambda s, *a: rec(("QMessageBox.exec", s.windowTitle())) or QMessageBox.Ok)
        self._set(QDialog, "exec", lambda s, *a: rec(("QDialog.exec", s.windowTitle())) or QDialog.Rejected)
        self._set(QMenu, "exec", lambda s, *a: rec(("QMenu.exec", s.title())) or None)
        self._set(QDesktopServices, "openUrl", staticmethod(lambda url: rec(("QDesktopServices.openUrl", url.toString())) or True))
        # no real benchmark: the editor's run request is recorded instead of MainWindow.start_run
        self.pe = self.win.pages["profiles"]
        self._fake_run = lambda job_path, opts=None: rec(("run_requested", os.path.basename(job_path), opts))
        self.pe.run_requested.disconnect(); self.pe.run_requested.connect(self._fake_run)
        return self

    def __exit__(self, *exc):
        self.pe.run_requested.disconnect(); self.pe.run_requested.connect(self.win.start_run)
        for owner, name, orig in reversed(self.saved):
            setattr(owner, name, orig)
        return False


def _pump(n=6):
    from PySide6.QtCore import QCoreApplication
    for _ in range(n):
        QCoreApplication.processEvents()


def _wired(b):
    from PySide6.QtCore import QMetaMethod
    if getattr(b, "menu", None) and b.menu() is not None:
        return True
    g = b.group()
    if g is not None and any(g.isSignalConnected(QMetaMethod.fromSignal(sig)) for sig in (g.idClicked, g.buttonClicked, g.buttonToggled)):
        return True
    return any(b.isSignalConnected(QMetaMethod.fromSignal(sig)) for sig in (b.clicked, b.toggled, b.pressed, b.released))


def _unconnected_signals(obj):
    """Custom signals declared by obj's own class (not Qt's) that nobody listens to."""
    mo = obj.metaObject(); out = []
    from PySide6.QtCore import QMetaMethod
    for i in range(mo.methodOffset(), mo.methodCount()):
        m = mo.method(i)
        if m.methodType() == QMetaMethod.Signal and not obj.isSignalConnected(m):
            out.append(f"{type(obj).__name__}.{bytes(m.name()).decode()}")
    return out


def _fingerprint(win, p):
    from .overlay import Overlay
    pe = win.pages["profiles"]; ex = win.pages["export"]
    ovs = [o for o in win.centralWidget().findChildren(Overlay) if o.isVisible()]
    return (win.stack.currentIndex(), win.collapsed, len(p.calls), len(ovs), pe.targets.rowCount(), pe.specs.rowCount(), pe.list.count(),
            pe.valid_chip.text(), pe.test_box.currentIndex(), pe.title.text(), hash(pe.json.toPlainText()), ex.out.text(),
            win.toast.isVisible(), win.run_mini.text(),
            tuple(t.isChecked() for t, _ in getattr(win.pages.get("results"), "err_more", [])))


def _overlays(win):
    from .overlay import Overlay
    return [o for o in win.centralWidget().findChildren(Overlay) if o.isVisible()]


def _close_overlays(win):
    for ov in _overlays(win):
        ov.hide(); ov.deleteLater()
    _pump()


def _press_overlay_primary(win, before, p, entry):
    """Overlay opened by the click: press its primary (accept) button once, else its first enabled button."""
    from PySide6.QtWidgets import QPushButton
    from . import errors
    for ov in [o for o in _overlays(win) if o not in before]:
        btns = [b for b in ov.findChildren(QPushButton) if b.isEnabled() and b.parent() is not None]
        prim = [b for b in btns if b.objectName() == "primary"] or btns[-1:]
        entry.setdefault("overlays", []).append(type(ov).__name__)
        if prim:
            entry.setdefault("overlay_buttons", []).append(_btn_text(prim[0]))
            prim[0].click(); _pump()
            for e in errors.take_seen():
                entry.setdefault("errors", []).append(e[-1500:])
    _close_overlays(win)


def run(win, timeout_s=120):
    """Returns the report dict (ok, buttons[], dead[], failed[], unobservable_expected[], unconnected_signals[])."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QAbstractButton, QPushButton, QToolButton
    from .main_window import NAV
    from . import errors
    from shiboken6 import isValid
    errors.install(quiet=True); errors.take_seen()
    tmp = tempfile.mkdtemp(prefix="sbench-click-")
    rep = {"schema": "sbench-gui-clicktest/1", "buttons": [], "dead": [], "failed": [], "unobservable_expected": [], "unconnected_signals": []}
    t_end = time.time() + timeout_s
    watchdog = QTimer(); watchdog.setInterval(250)

    def kill_modal():
        w = QApplication.activeModalWidget()
        if w is not None:
            rep.setdefault("watchdog", []).append(w.windowTitle() or type(w).__name__); w.close()
    watchdog.timeout.connect(kill_modal); watchdog.start()
    for key, _ in NAV:
        rep["unconnected_signals"] += _unconnected_signals(win.pages[key])
    with _Patches(win, tmp) as p:
        seen = set()
        for key, _ in NAV:
            win.show_page(key); _pump()
            page = win.pages[key]
            if hasattr(page, "click_setup"):       # e.g. results: pick a result whose error box has a toggle
                page.click_setup(); _pump()
            scope = [page] + ([win.sidebar] if key == NAV[-1][0] else [])
            for root in scope:
                for b in list(root.findChildren(QAbstractButton)):
                    if not isValid(b) or not isinstance(b, (QPushButton, QToolButton)) or id(b) in seen:
                        continue
                    seen.add(id(b))
                    if time.time() > t_end:
                        rep["timeout"] = True; break
                    if key != "dashboard" and win.stack.currentIndex() != [k for k, _ in NAV].index(key):
                        win.show_page(key); _pump()
                    prep = getattr(page, "click_prepare", None)     # e.g. profile editor: select a storage / cpu test first
                    if prep is not None:
                        prep(b); _pump()
                    entry = {"page": key, "text": _btn_text(b), "enabled": b.isEnabled(), "wired": _wired(b)}
                    rep["buttons"].append(entry)
                    if not entry["wired"]:
                        rep["dead"].append(f"{key}: {entry['text']}")
                    if not b.isEnabled():
                        continue
                    before_ov = _overlays(win); fp = _fingerprint(win, p); n_calls = len(p.calls)
                    errors.take_seen()
                    if b.menu() is not None:
                        acts = [a for a in b.menu().actions() if not a.isSeparator()]
                        entry["actions"] = [a.text() for a in acts]
                        for a in acts:
                            before_a = _overlays(win); a.trigger(); _pump(); _press_overlay_primary(win, before_a, p, entry)
                    else:
                        b.click(); _pump()
                    for e in errors.take_seen():
                        entry.setdefault("errors", []).append(e[-1500:])
                    entry["calls"] = [c[0] + (f" {c[1]}" if len(c) > 1 else "") for c in p.calls[n_calls:]][:6]
                    entry["observable"] = _fingerprint(win, p) != fp or bool(_overlays(win)) or len(p.calls) > n_calls
                    _press_overlay_primary(win, before_ov, p, entry)
                    for e in errors.take_seen():
                        entry.setdefault("errors", []).append(e[-1500:])
                    if entry.get("errors"):
                        rep["failed"].append(f"{key}: {entry['text']}")
                    if entry["text"] in EXPECT and not entry["observable"]:
                        rep["unobservable_expected"].append(f"{key}: {entry['text']}")
    watchdog.stop()
    missing = sorted(EXPECT - {e["text"] for e in rep["buttons"]})
    rep["expected_missing"] = missing
    rep["count"] = len(rep["buttons"])
    rep["ok"] = not (rep["dead"] or rep["failed"] or rep["unobservable_expected"] or rep["unconnected_signals"] or missing or rep.get("timeout"))
    rep["summary"] = summary(rep)
    return rep


def summary(rep):
    lines = [f"Klick-Test: {'OK' if rep['ok'] else 'FEHLGESCHLAGEN'} · {rep.get('count', 0)} Buttons"]
    for k, txt in (("dead", "ohne Funktion"), ("failed", "Ausnahme"), ("unobservable_expected", "keine sichtbare Reaktion"),
                   ("unconnected_signals", "Signal nicht verbunden"), ("expected_missing", "erwarteter Button fehlt")):
        for x in rep.get(k) or []:
            lines.append(f"  FEHLER {txt}: {x}")
    if rep.get("timeout"):
        lines.append("  FEHLER Zeitlimit überschritten")
    return lines


def main(a):
    """`sbench-gui --self-test-clicks [--platforms offscreen] [--report f.json]` -> exit 0/1 (frozen EXE: real core)."""
    from .app import make_app, build_window, _emit
    res = {"schema": "sbench-gui-clicktest/1", "ok": False}
    os.environ.setdefault("SBENCH_GUI_DIR", tempfile.mkdtemp(prefix="sbench-gui-clicks-"))   # never touch the user's profiles
    try:
        plat = (a.platforms or "offscreen").split(",")[0]
        _app = make_app(["-platform", plat])
        win = build_window(a, deterministic=True)
        win.resize(1440, 900); win.show(); _pump()
        res = run(win)
        res["platform"] = plat
    except Exception as e:  # noqa: BLE001
        import traceback
        res["error"] = f"{type(e).__name__}: {e}"; res["traceback"] = traceback.format_exc()[-3000:]
        res["summary"] = [f"Klick-Test: FEHLGESCHLAGEN · {res['error']}"]
    res["frozen"] = bool(getattr(__import__("sys"), "frozen", False))
    _emit(res, a.report)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":   # pragma: no cover
    print(json.dumps({"hint": "python -m sbgui --self-test-clicks"}))
