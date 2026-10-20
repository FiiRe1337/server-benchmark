# sbench-gui (Prototyp M2)

PySide6/pyqtgraph-GUI für `sbench.exe`, steuert ausschließlich die CLI (QProcess + JSON Lines, Vertrag 1.2 in [`../schema`](../schema) und [`../docs/cli.md`](../docs/cli.md)).
Plan: [`../docs/GUI_PLAN.md`](../docs/GUI_PLAN.md) · Kompatibilität: [`../docs/kompatibilitaet.md`](../docs/kompatibilitaet.md)

> **GUI erfordert Desktop Experience, auf Server Core die CLI nutzen.**

**Kern:** Die portable `sbench-gui.exe` (CI-Artefakt `sbench-gui-x64`) bettet den echten `sbench.exe` ein (gleicher Commit, aus `build-windows.yml`). Beim Start:

1. Datenverzeichnis = `data\` neben `sbench-gui.exe`, wenn beschreibbar (echter Schreibtest), sonst `%ProgramData%\sbench`.
2. Der eingebettete Kern wird **nie** aus dem PyInstaller-Temp-Ordner (`_MEI…`) gestartet, sondern nach `<data_dir>\bin\sbench-<version>.exe` kopiert (fehlt oder weicht ab → neu kopieren).
3. Vor **jedem** Start von sbench (auch vor `runas`/UAC) wird der SHA-256 gegen den beim Build eingebetteten Wert (`sbgui/_build_info.py`) geprüft. Abweichung → Start verweigert, klare Fehlermeldung, Datei wird aus der eingebetteten Kopie wiederhergestellt. Unter Windows hält die GUI die geprüfte Datei mit `FILE_SHARE_READ` offen (kein Austausch zwischen Prüfung und Start).
4. Jeder sbench-Aufruf erhält `--data-dir <data_dir>` (runas erbt keine Umgebung, `SBENCH_DATA_DIR` wird dafür nicht genutzt).

Quell-Läufe (Entwicklung/Tests): `SBENCH_EXE` (optional `SBENCH_EXE_SHA256`), sonst der Mock `mock_sbench.py` (Vertrag 1.2 inkl. CPU/RAM-Tests + `--data-dir`, `version --json`; `SBENCH_MOCK_CONTRACT=1.1` = altes M1-Verhalten). Eine eingefrorene EXE nutzt nie den Mock.
Versionen (GUI + Kern) stehen im Fenstertitel und unten in der Seitenleiste (Tooltip: Commit, Kern-SHA-256).

Self-Test: `sbench-gui.exe --self-test --report st.json` (ohne Admin) prüft zusätzlich zum GUI-Rendering den Kern: `version --json` (SHA-256 = eingebetteter Wert), `info --json` gegen sb-info/1, 3-s-Storage-Job (4 KiB random read, Datei in `%TEMP%`) mit Exit 0, `io_count > 0`, sb-result/1 und Laden in der GUI, `bundle --all` gegen sb-bundle/1. `--no-core` überspringt den Kern-Teil. Bericht als JSON mit lesbarer `summary`.

```
cd gui
python -m venv .venv && .venv/bin/pip install -r requirements.txt     # Windows: .venv\Scripts\pip
python -m sbgui                                   # GUI (gegen sbench.exe oder Mock)
python -m sbgui --demo                            # Live-Lauf gegen den Mock
QT_QPA_PLATFORM=offscreen python -m sbgui --screenshot ../docs/img [--scale 1.5] [--size 1366x768] --report r.json
python -m sbgui --self-test --report selftest.json
python tools/check_mock.py demo/*.json demo/progress.jsonl profiles/*.json   # oltp-mixed.json schlägt absichtlich fehl (E_DESTRUCTIVE_NOT_ALLOWED)
python tools/live_qprocess_check.py               # E2E gegen den Mock (Exit 4/2/3, Re-Attach, Support-ZIP)
python tools/make_demo.py                         # demo/ neu erzeugen
```

CI ([`../.github/workflows/gui-ci.yml`](../.github/workflows/gui-ci.yml)): Windows 2022/2025 (ruff, pytest, `--self-test` gegen den Mock), Linux (Schemas, `check_mock.py`, E2E, Self-Test), Job `core` (`build-windows.yml` per `workflow_call`) und Job `gui` ([`build-gui.yml`](../.github/workflows/build-gui.yml): lädt `sbench-cli-x64` aus demselben Lauf, `tools/gen_build_info.py` schreibt Version/Commit/SHA-256, PyInstaller onefile, Self-Test der EXE mit echtem Kern + Manipulationstest, Artefakt `sbench-gui-x64` mit genau `sbench-gui.exe`). Lokaler EXE-Build: `python tools/gen_build_info.py --core <sbench.exe> --run-core && pyinstaller --noconfirm sbench-gui.spec`. Lokal:

```
pip install -r requirements.txt -r requirements-ci.txt     # exakte CI-Pins
ruff check . && QT_QPA_PLATFORM=offscreen python -m pytest
```

Umgebungsvariablen (nur Quell-Läufe): `SBENCH_EXE` (Kern), `SBENCH_DATA_DIR` (Vorgabe für `--data-dir` im Quell-Lauf), `SBENCH_GUI_DIR` (GUI-Logs/Profile/Ergebnisse, Standard `%LOCALAPPDATA%\sbench-gui`), `SBENCH_SCHEMA_DIR`.
Mock: `SBENCH_MOCK_ELEVATED=1` simuliert Administratorrechte, `SBENCH_MOCK_SPEED` beschleunigt Läufe.

**CPU/RAM (M2, Vertrag 1.2):**
- Testprofil: Test-Auswahl mit „+ CPU“/„+ RAM“, Formular nach `test_cpu`/`test_memory` (Workload, Modus, Operation, Threads 0 = alle, Puffer/Thread, Befehlssatz, Non-temporal, NUMA-Knoten für Threads und Puffer) und Vorlagen; Profile `profiles/cpu-ram.json`, `profiles/numa-lokal-remote.json`.
- Live: Kurve des aktuellen Tests (ops/s, GB/s oder ns), CPU-Last (System/sbench), Balken + Tabelle pro Thread aus `tick.workers[]` (keine 128 Linien), Fortschrittsbalken „Puffer wird angelegt“ ab 64 MiB. Score live = Schätzung aus den Referenzwerten in `docs/core.md`.
- Ergebnisse (neue Seite): CPU-Score je Workload/Threads, RAM GB/s je Operation/NT/SIMD und Latenz in ns, NUMA-Platzierung (`numa_node` → `mem_node`), RAM-Quelle pro Knoten (`ram_method`), `per_worker` und Zeitreihe. Abgebrochene Läufe = Teilergebnis.
- Vergleich: CPU/RAM-Tests Host vs. VM als % des Basislaufs (Latenz invertiert) plus Delta-Tabelle. Flat-CSV (`model.read_flat_csv`) liest 14 (1.1) und 18 Spalten (1.2).

Windows-only und noch nicht auf echtem Windows getestet: `sbgui/pipe_win.py` (Named Pipe mit DACL, runas).
