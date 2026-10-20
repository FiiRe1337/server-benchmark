import json, logging, os
from PySide6.QtCore import QUrl, QSize
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QFrame, QPushButton, QStackedWidget, QButtonGroup,
                               QFileDialog, QMessageBox)
from .widgets import label, chip, nav_icon, scroll
from .pages.dashboard import Dashboard
from .pages.profiles import ProfileEditor
from .pages.live import LiveView
from .pages.compare import Compare
from .pages.export import ExportPage
from .overlay import ReattachOffer, SupportDialog, Overlay
from . import theme as T, sbench
from .sbench import ROOT

from .core import gui_version, build_info, CoreError

GUI_VERSION = gui_version()        # from sbgui/_build_info.py (CI build) or 0.1.0-dev (source)
NAV = [("dashboard", "Übersicht"), ("profiles", "Testprofile"), ("live", "Live-Lauf"), ("compare", "Vergleich"), ("export", "Export")]
MIN_W, MIN_H = 1280, 720          # logical px; RDP 1366x768 must fit
COLLAPSE_BELOW = 1400             # sidebar auto-collapses below this window width
SIDEBAR_W, SIDEBAR_MINI = 232, 64
log = logging.getLogger("sbench-gui")


class Toast(QFrame):
    """In-window notification (child widget, so it shows up in widget.grab() screenshots)."""

    def __init__(self, parent):
        super().__init__(parent); self.setObjectName("toast"); self.hide()
        v = QVBoxLayout(self); v.setContentsMargins(18, 14, 18, 14); v.setSpacing(6)
        h = QHBoxLayout(); ic = label("✓"); ic.setStyleSheet(f"color:{T.GREEN}; font-size:18px; font-weight:700;"); h.addWidget(ic)
        self.title = label("", "h2"); h.addWidget(self.title, 1); v.addLayout(h)
        self.body = label("", "muted", wrap=True); v.addWidget(self.body)
        b = QHBoxLayout(); b.addStretch(1)
        self.open_btn = QPushButton("Ordner öffnen"); self.open_btn.setObjectName("primary"); b.addWidget(self.open_btn)
        close = QPushButton("Schließen"); close.clicked.connect(self.hide); b.addWidget(close); v.addLayout(b)
        self.setFixedWidth(420); self.folder = None
        self.open_btn.clicked.connect(lambda: self.folder and QDesktopServices.openUrl(QUrl.fromLocalFile(self.folder)))

    def show_msg(self, title, body, folder):
        self.title.setText(title); self.body.setText(body); self.folder = folder
        self.adjustSize(); self.reposition(); self.show(); self.raise_()

    def reposition(self):
        p = self.parentWidget(); self.move(p.width() - self.width() - 24, p.height() - self.height() - 24)


class MainWindow(QMainWindow):
    def __init__(self, info, kern_version, result_files, demo=False):
        super().__init__()
        self.kern_version = kern_version
        self.setWindowTitle(f"sbench – Server Benchmark · GUI {GUI_VERSION} · Kern {kern_version}")
        self.setMinimumSize(MIN_W, MIN_H)
        self.info = info; self.demo = demo; self.session = None; self.sidebar_pref = None; self.collapsed = None
        m = info["meta"]
        self.data_dir = m.get("data_dir")            # never computed by the GUI (cli.md 1.1)
        self.elevated = bool(m.get("elevated"))
        root = QWidget(); root.setObjectName("root"); self.setCentralWidget(root)
        h = QHBoxLayout(root); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(0)

        sb = self.sidebar = QFrame(); sb.setObjectName("sidebar")
        sv = QVBoxLayout(sb); sv.setContentsMargins(12, 18, 12, 16); sv.setSpacing(4)
        brand = QHBoxLayout(); brand.setSpacing(8)
        self.menu_btn = QPushButton(); self.menu_btn.setObjectName("flat"); self.menu_btn.setIcon(nav_icon("menu", T.MUTED))
        self.menu_btn.setIconSize(QSize(18, 18)); self.menu_btn.setToolTip("Seitenleiste ein-/ausklappen")
        self.menu_btn.clicked.connect(self.toggle_sidebar); brand.addWidget(self.menu_btn)
        self.brand_w = QWidget(); bv = QVBoxLayout(self.brand_w); bv.setContentsMargins(0, 0, 0, 0); bv.setSpacing(0)
        bv.addWidget(label("sbench", "brand")); bv.addWidget(label("Server Benchmark", "brandSub"))
        brand.addWidget(self.brand_w); brand.addStretch(1); sv.addLayout(brand); sv.addSpacing(18)
        self.group = QButtonGroup(self); self.group.setExclusive(True); self.nav = {}
        for i, (key, txt) in enumerate(NAV):
            b = QPushButton(txt); b.setObjectName("nav"); b.setCheckable(True); b.setMinimumHeight(40)
            b.setIconSize(QSize(18, 18)); b.setToolTip(txt); b._txt = txt; b._key = key
            self.group.addButton(b, i); sv.addWidget(b); self.nav[key] = b
        self.group.buttonToggled.connect(lambda b, on: self._nav_icons())
        sv.addStretch(1)
        self.run_mini = chip("kein Lauf aktiv"); sv.addWidget(self.run_mini); sv.addSpacing(10)
        self.sup = QPushButton("Ergebnisse für\nSupport exportieren"); self.sup.setObjectName("support"); self.sup.setMinimumHeight(50)
        self.sup.setIcon(nav_icon("support", T.ACCENT)); self.sup.setIconSize(QSize(18, 18)); self.sup.setToolTip("Ergebnisse für Support exportieren")
        self.sup.clicked.connect(lambda: self.support_bundle([])); sv.addWidget(self.sup); sv.addSpacing(10)
        self.data_label = label(f"Daten: {_short_path(self.data_dir or '?')}", "dim")
        self.data_label.setToolTip(f"meta.data_dir: {self.data_dir}"); sv.addWidget(self.data_label)
        self.ver_label = label(f"GUI {GUI_VERSION} · Kern {kern_version}", "dim"); sv.addWidget(self.ver_label)
        bi = build_info()
        self.ver_label.setToolTip(f"GUI {GUI_VERSION} (Commit {bi['commit']})\nKern {kern_version}"
                                  + (f"\nKern-SHA-256 {bi['core_sha256']}" if bi.get("core_sha256") else ""))
        h.addWidget(sb)

        self.stack = QStackedWidget(); h.addWidget(self.stack, 1)
        gd = sbench.gui_dir()
        self.pages = {
            "dashboard": Dashboard(info, kern_version),
            "profiles": ProfileEditor([os.path.join(ROOT, "profiles"), os.path.join(gd, "profiles")], elevated=self.elevated),
            "live": LiveView(),
            "compare": Compare(result_files),
            "export": ExportPage(result_files, os.path.join(gd, "export")),
        }
        for k, _ in NAV:
            self.stack.addWidget(scroll(self.pages[k]))
        self.group.idClicked.connect(self.stack.setCurrentIndex)
        self.show_page("dashboard")
        self.toast = Toast(root)
        self._set_collapsed(False)

        self.pages["profiles"].run_requested.connect(self.start_run)
        self.pages["live"].stop_requested.connect(self.stop_run)
        self.pages["live"].elevate_requested.connect(self.offer_elevation)
        self.pages["compare"].export_selected.connect(self.support_bundle)
        self.pages["export"].support_requested.connect(self.support_bundle)

    # --------------------------------------------------------------- layout
    def _nav_icons(self):
        for key, b in self.nav.items():
            b.setIcon(nav_icon(key, T.ACCENT if b.isChecked() else T.MUTED))

    def _set_collapsed(self, c):
        if c == self.collapsed:
            return
        self.collapsed = c
        self.sidebar.setFixedWidth(SIDEBAR_MINI if c else SIDEBAR_W)
        self.sidebar.layout().setContentsMargins(8 if c else 12, 18, 8 if c else 12, 16)
        for b in self.nav.values():
            b.setText("" if c else f"  {b._txt}"); b.setObjectName("navIcon" if c else "nav"); b.style().unpolish(b); b.style().polish(b)
        self.sup.setText("" if c else "  Ergebnisse für\n  Support exportieren")
        for w in (self.brand_w, self.run_mini, self.data_label, self.ver_label):
            w.setVisible(not c)
        self._nav_icons()

    def toggle_sidebar(self):
        self.sidebar_pref = not self.collapsed; self._set_collapsed(self.sidebar_pref)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        auto = e.size().width() < COLLAPSE_BELOW
        if self.sidebar_pref is None or (e.oldSize().width() >= COLLAPSE_BELOW) != (e.size().width() >= COLLAPSE_BELOW):
            self.sidebar_pref = None; self._set_collapsed(auto)
        if hasattr(self, "toast") and self.toast.isVisible():
            self.toast.reposition()
        for ov in self.centralWidget().findChildren(Overlay):
            ov.setGeometry(self.centralWidget().rect())

    def show_page(self, key):
        i = [k for k, _ in NAV].index(key); self.nav[key].setChecked(True); self.stack.setCurrentIndex(i)

    # --------------------------------------------------------------- runs
    def start_run(self, job_path, opts=None, tick_ms=250):
        opts = opts or {}
        job = json.load(open(job_path, encoding="utf-8"))
        rid = sbench.new_run_id()
        out = os.path.join(sbench.gui_dir(), "results", f"{rid}.json")
        if not self._core_ok():
            return
        cls = sbench.ElevatedRunSession if opts.get("elevated") else sbench.RunSession
        self.session = cls(job_path, out, rid, tick_ms=tick_ms, allow_destructive=bool(opts.get("allow_destructive")), parent=self)
        self._attach(self.session, job, rid, tick_ms)

    def _attach(self, session, job, rid, tick_ms=250, reattached=False):
        self.pages["live"].attach(session, job, tick_ms=tick_ms, reattached=reattached)
        session.finished.connect(lambda c, t: self.run_mini.setText(f"letzter Lauf: {t}"))
        session.start(); self.run_mini.setText(f"● läuft · {rid}"); self.run_mini.setObjectName("chipAccent")
        self.run_mini.style().polish(self.run_mini)
        self.show_page("live")

    def _core_ok(self):
        """SHA-256 check of the core before a launch (repeated inside every session start); clear error on mismatch."""
        try:
            sbench.core().verify(); return True
        except CoreError as e:
            log.error("core refused: %s", e)
            QMessageBox.critical(self, "sbench – Integritätsprüfung fehlgeschlagen", str(e))
            return False

    def stop_run(self):
        if self.session:
            self.session.stop()

    def check_running(self):
        """GUI start: re-attach to runs that survived a GUI exit (sbench list --running --json -> sb-runs/1)."""
        runs = sbench.list_running()
        if runs:
            log.info("running runs: %s", [r["run_id"] for r in runs])
            return self.offer_reattach(runs)

    def offer_reattach(self, runs):
        dlg = ReattachOffer(self.centralWidget(), runs)

        def act(choice):
            what, run = choice
            if what == "stop":
                sbench.stop_run(run["run_id"]); return
            self.session = sbench.TailSession(run, parent=self)
            job = None
            try:  # job copy of the run (lock file 'job'; not in sb-runs/1 -> best effort via data_dir/jobs)
                job = json.load(open(os.path.join(self.data_dir or "", "jobs", f"{run['run_id']}.json"), encoding="utf-8"))
            except (OSError, ValueError):
                pass
            self._attach(self.session, job, run["run_id"], reattached=True)
        dlg.accepted.connect(act); dlg.open(); return dlg

    def offer_elevation(self):
        QMessageBox.warning(self, "Administratorrechte erforderlich",
                            "sbench hat mit Exit-Code 4 abgebrochen (fehlende Rechte).\n"
                            "Bitte den Test erneut starten und »Als Administrator starten« wählen:\n"
                            "nur sbench.exe wird per UAC (runas) erhöht, der Fortschritt kommt über eine Named Pipe.")

    # --------------------------------------------------------------- support bundle
    def support_bundle(self, results, anonymize=False):
        dlg = SupportDialog(self.centralWidget(), len(results), anonymize)
        dlg.accepted.connect(lambda o: self._bundle_save(results if o["selected"] else [], o)); dlg.open(); return dlg

    def _bundle_save(self, results, o, out_path=None):
        if out_path is None:
            default = os.path.join(os.path.expanduser("~"), "Desktop", "sbench-support.zip")
            out_path, _ = QFileDialog.getSaveFileName(self, "Support-Paket speichern", default, "ZIP-Archiv (*.zip)")
            if not out_path:
                return
        if not self._core_ok():
            return
        gd = sbench.gui_dir()
        inc = ([os.path.join(gd, "logs")] if o.get("logs") else []) + ([os.path.join(gd, "profiles")] if o.get("profiles") else [])
        p = sbench.bundle(out_path, results or None, include=inc, anonymize=o.get("anonymize"), parent=self)

        def done(code, _st):
            if code == 0:
                n, anon = sbench.bundle_summary(out_path)
                self.toast.show_msg("Support-Paket erstellt", f"{os.path.basename(out_path)} · {n or '?'} Dateien inkl. manifest.json (SHA-256)"
                                    f"{' · anonymisiert' if anon else ''}\n{_short_path(os.path.dirname(out_path), 52)}", os.path.dirname(out_path))
            else:
                QMessageBox.critical(self, "Support-Paket", bytes(p.readAllStandardError()).decode(errors="replace") or f"Exit {code}")
        p.finished.connect(done)
        return p


def _short_path(p, n=30):
    return p if len(p) <= n else "…" + p[-(n - 1):]
