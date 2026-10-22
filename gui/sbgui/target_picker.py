"""Profile editor 'Ziele': + Datei / + Volume / + Disk.

+ Datei   -> QFileDialog (path of the test file) -> TargetPicker (size, scope)
+ Volume  -> TargetPicker with the volumes of `sbench info --json` (targets.volumes)
+ Disk    -> TargetPicker with the physical disks of `sbench info --json` (targets.physical_disks)
Enumerating volumes/disks needs no admin rights (the core opens disks with access 0 as fallback); RUNNING a test on
them does (W_NEEDS_ADMIN -> the start flow offers 'Als Administrator starten', only sbench.exe is elevated via UAC)."""
import os
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QCheckBox, QLineEdit, QComboBox, QHBoxLayout, QPushButton, QFileDialog, QFrame, QVBoxLayout
from .overlay import Overlay
from .widgets import label
from .model import human_bytes, size_bytes

FILE_SIZES = ["1GiB", "4GiB", "16GiB", "64GiB", "256GiB"]
KIND_TITLE = {"file": "Datei als Ziel", "volume": "Volume als Ziel", "physical": "Physische Disk als Ziel"}
ADMIN_HINT = ("Für Volumes und physische Disks werden zum Testen Administratorrechte benötigt. Beim Start bietet sbench "
              "»Als Administrator starten« an (nur sbench.exe wird per UAC erhöht, die GUI bleibt normal).")


def _gib(b):
    """Bytes -> '447 GiB' / '3,5 TiB' (model.human_bytes knows units up to GiB)."""
    return human_bytes(b // 1024, "GiB").replace("GiB", "TiB") if b >= 1 << 40 else human_bytes(b, "GiB")


def candidates(info, kind):
    """sb-info/1 -> [(path, display text, tooltip)] for kind 'volume' | 'physical'."""
    t = (info or {}).get("targets") or {}
    sysdrive = (os.environ.get("SystemDrive") or "C:").rstrip("\\").upper()
    out = []
    if kind == "volume":
        for v in t.get("volumes") or []:
            letter = (v.get("letter") or v["path"][-2:-1]).upper()
            sysv = f"{letter}:" == sysdrive
            txt = (f"{letter}:  ·  {v.get('label') or 'ohne Bezeichnung'}  ·  {v.get('fs') or '?'}  ·  "
                   f"{_gib(v.get('size_bytes', 0))} (frei {_gib(v.get('free_bytes', 0))})" + ("  ·  Systemlaufwerk!" if sysv else ""))
            out.append((v["path"], txt, v["path"]))
    else:
        for d in t.get("physical_disks") or []:
            n = d["path"].replace("\\\\.\\", "")
            txt = f"{n}  ·  {d.get('model') or 'unbekanntes Modell'}  ·  {d.get('bus') or '?'}  ·  " + (
                _gib(d["size_bytes"]) if d.get("size_bytes") else "Größe unbekannt")
            out.append((d["path"], txt, f"{d['path']} · Sektor {d.get('sector_logical', '?')}/{d.get('sector_physical', '?')} B"))
    return out


def empty_message(kind, elevated, error=None):
    what = "Volumes" if kind == "volume" else "physischen Disks"
    msg = f"sbench info hat keine {what} gemeldet."
    if error:
        msg = f"Die Liste der {what} konnte nicht gelesen werden ({error})."
    if not elevated:
        msg += ("\n\nFür Laufwerke/Disks werden Administratorrechte benötigt; ohne Rechte meldet Windows manche Geräte nicht. "
                "Die GUI selbst wird nie erhöht gestartet: Ziel bei Bedarf als Pfad im Job eintragen (z. B. \\\\.\\PhysicalDrive1 "
                "oder \\\\.\\D:) – beim Teststart bietet sbench »Als Administrator starten« an.")
    return msg


class TargetPicker(Overlay):
    """accepted(dict): {"target": sb-job/1 target, "all_tests": bool, "replace": bool}."""

    def __init__(self, parent, kind, items=(), n_storage=1, test_name="", replace=False, elevated=False, path="", file_size="16GiB"):
        sub = {"file": "Testdatei, die sbench anlegt (Größe = file_size). Das Verzeichnis muss existieren.",
               "volume": "Ganzes Volume (\\\\.\\X:) direkt ohne Dateisystem. Schreibtests zerstören Daten – Freigabe wird beim Start abgefragt.",
               "physical": "Ganze Disk (\\\\.\\PhysicalDriveN) direkt. Schreibtests zerstören alle Daten – Freigabe wird beim Start abgefragt."}[kind]
        super().__init__(parent, KIND_TITLE[kind], sub, "info" if kind == "file" else "warn", 600)
        self.kind = kind
        if kind == "file":
            r = QHBoxLayout(); r.setSpacing(8)
            self.path = QLineEdit(path); self.path.setPlaceholderText("z. B. D:\\sbench\\testfile.dat"); r.addWidget(self.path, 1)
            b = QPushButton("Durchsuchen …"); b.clicked.connect(self.browse); r.addWidget(b); self.add(r)
            r2 = QHBoxLayout(); r2.addWidget(label("Dateigröße", "muted"))
            self.size = QComboBox(); self.size.setEditable(True); self.size.addItems(FILE_SIZES); self.size.setCurrentText(file_size or "16GiB")
            r2.addWidget(self.size); r2.addStretch(1); self.add(r2)
            self.path.textChanged.connect(self._check); self.size.editTextChanged.connect(self._check)
        else:
            self.list = QListWidget(); self.list.setMinimumHeight(150)
            for p, txt, tip in items:
                it = QListWidgetItem(txt); it.setData(Qt.UserRole, p); it.setToolTip(tip); self.list.addItem(it)
            self.list.setCurrentRow(0); self.list.currentRowChanged.connect(self._check)
            self.list.itemDoubleClicked.connect(lambda _it: self.ok.isEnabled() and self._accept())
            self.add(self.list)
            if not elevated:
                box = QFrame(); box.setObjectName("warnBox"); bl = QVBoxLayout(box); bl.setContentsMargins(10, 7, 10, 7)
                bl.addWidget(label(ADMIN_HINT, "muted", wrap=True)); self.add(box)
        self._multi = n_storage > 1
        self.all_tests = QCheckBox(f"Für alle {n_storage} Storage-Tests des Profils übernehmen")
        self.all_tests.setChecked(n_storage > 1); self.all_tests.setVisible(n_storage > 1); self.add(self.all_tests)
        self.replace = QCheckBox("Bisherige Ziele ersetzen" + (f" (Test »{test_name}«)" if test_name and n_storage <= 1 else ""))
        self.replace.setChecked(replace); self.add(self.replace)
        self.err = label("", "dim", wrap=True); self.add(self.err)
        self.button("Abbrechen")
        self.ok = self.button("Ziel übernehmen", "primary", self._accept)
        self._check()

    def browse(self):
        start = self.path.text().strip() or os.path.join(os.path.expanduser("~"), "testfile.dat")
        p, _ = QFileDialog.getSaveFileName(self, "Testdatei für sbench wählen", start, "Testdatei (*.dat);;Alle Dateien (*)",
                                           options=QFileDialog.DontConfirmOverwrite)
        if p:
            self.path.setText(os.path.normpath(p))

    def _check(self, *_):
        msg = ""
        if self.kind == "file":
            p = self.path.text().strip()
            try:
                size_bytes(self.size.currentText().strip())
            except ValueError:
                msg = "Dateigröße z. B. 16GiB, 512MiB"
            if not p:
                msg = "Pfad der Testdatei fehlt"
            elif os.path.isdir(p):
                msg = "Bitte einen Dateinamen angeben, nicht nur ein Verzeichnis"
        elif self.list.currentItem() is None:
            msg = "Kein Eintrag gewählt"
        self.err.setText(msg); self.ok.setEnabled(not msg)

    def value(self):
        if self.kind == "file":
            tg = {"path": os.path.normpath(self.path.text().strip()) if self.path.text().strip() else "", "kind": "file",
                  "file_size": self.size.currentText().strip()}
        else:
            it = self.list.currentItem()
            tg = {"path": it.data(Qt.UserRole) if it else "", "kind": self.kind}
        return {"target": tg, "all_tests": self._multi and self.all_tests.isChecked(), "replace": self.replace.isChecked()}

    def _accept(self):
        self.close_accept(self.value())
