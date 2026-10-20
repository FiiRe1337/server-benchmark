# Architektur

Ein portables Werkzeug (keine Installation), mit dem sich Windows-Server schnell und
reproduzierbar vermessen lassen:

- **Storage** wie Iometer: Dateien, Volumes und physische Disks, mehrere Worker,
  Queue Depth, mehrere gewichtete Access-Specs (z. B. 60 % 4K random read +
  40 % 64K sequential write), Latenz-Perzentile und Histogramm.
- **CPU** (Integer, Gleitkomma, Krypto, gemischt), **Arbeitsspeicher** (Bandbreite,
  Latenz), **Netzwerk** (TCP/UDP-Durchsatz, Jitter, Verlust, RTT; Client und Server).
  *In Meilenstein 1 ist nur Storage implementiert; andere Testtypen meldet der Kern als
  `E_NOT_IMPLEMENTED` und lässt sie aus.*
- **Hyper-V**: Erkennung von Host/Gast, Hypervisor-Informationen und
  Leistungsindikatoren.

## Aufbau

```
┌────────────────────────────┐   Job (JSON)  / Ergebnis (JSON)
│ GUI (Python, PySide6)      │──────────────────────────────┐
│ PyInstaller-EXE            │   Fortschritt (JSON Lines     │
│ startet sbench als         │◄── über stdout/Named Pipe) ── │
│ Unterprozess (ggf. runas)  │   Stop (benanntes Event)      │
└────────────────────────────┘                               ▼
                                          ┌──────────────────────────────┐
                                          │ sbench.exe (CLI)             │
                                          │  └─ C-Messkern (statische    │
                                          │     Lib), zusätzlich als     │
                                          │     sbcore.dll               │
                                          └──────────────────────────────┘
```

- **C-Messkern** als statische Bibliothek; daraus entstehen `sbench.exe` (CLI) und
  `sbcore.dll` (für spätere direkte Einbindung). Details: [core.md](core.md).
- **GUI** in PySide6 steuert ausschließlich die CLI als Unterprozess. Dadurch kann
  der Messkern bei Bedarf erhöht (Administrator) laufen, während die GUI normal läuft.
- Auslieferung der GUI als **PyInstaller-EXE** (`sbench-gui.exe`, onefile), die
  `sbench.exe` eingebettet mitbringt. Die GUI startet den Kern nie aus dem
  PyInstaller-Temp-Ordner, sondern kopiert ihn nach `<data_dir>\bin\sbench-<version>.exe`,
  prüft vor jedem Start den SHA-256 gegen den beim Build eingebetteten Wert und übergibt
  bei jedem Aufruf `--data-dir` ([gui/README.md](../gui/README.md)).
- **Datenverzeichnis:** `data\` neben der EXE, wenn beschreibbar, sonst
  `%ProgramData%\sbench` ([cli.md](cli.md#datenverzeichnis)).
- **Build** per GitHub Actions, nativ für x64 und ARM64 (ab Windows 10 1607 /
  Server 2016, statische CRT); Details unter [Build & Plattformen](cli.md#build--plattformen),
  Release-Ablauf in [release.md](release.md).

## Schnittstellen-Vertrag

- [`schema/`](../schema/) – JSON Schemas (draft 2020-12) für Job (`sb-job/1`), Ergebnis
  (`sb-result/1`), Fortschritts-Events (`sb-progress/1`), Systeminfo (`sb-info/1`),
  Validierung (`sb-validate/1`), laufende Läufe (`sb-runs/1`) und Support-Paket
  (`sb-bundle/1`), mit Beispielen in [`schema/examples/`](../schema/examples/).
- [cli.md](cli.md) – Befehle, Optionen, Exit-Codes und Rechte von `sbench.exe`.

Exit-Codes: 0 ok, 1 Fehler, 2 ungültiger Job/Argumente, 3 abgebrochen, 4 fehlende Rechte.

## GUI

Plan und Bildschirme: [GUI_PLAN.md](GUI_PLAN.md) · Code und Sicherheitsmaßnahmen beim
Kernstart: [gui/README.md](../gui/README.md) · Kompatibilität: [kompatibilitaet.md](kompatibilitaet.md).

## Meilensteine

1. **Storage-Kern + Durchstich** – Storage-Test im C-Kern (Dateien, Volumes,
   physische Disks, Worker, Queue Depth, gewichtete Access-Specs, Latenzstatistik),
   `sbench run/stop/validate/info` gemäß Vertrag, GUI, die einen Job startet,
   Live-Werte anzeigt und das Ergebnis lädt; Build per GitHub Actions.
2. **Weitere Tests und Komfort** – u. a. Import von Iometer-Konfigurationen
   (`sbench import-icf`), CPU, RAM, Netzwerk, Hyper-V.
