# sbench-gui (Prototyp M2)

PySide6/pyqtgraph-GUI für `sbench.exe`, steuert ausschließlich die CLI (QProcess + JSON Lines, Vertrag 1.3 in [`../schema`](../schema) und [`../docs/cli.md`](../docs/cli.md)).
Plan: [`../docs/GUI_PLAN.md`](../docs/GUI_PLAN.md) · Kompatibilität: [`../docs/kompatibilitaet.md`](../docs/kompatibilitaet.md)

> **GUI erfordert Desktop Experience, auf Server Core die CLI nutzen.**

**Kern:** Die portable `sbench-gui.exe` (CI-Artefakt `sbench-gui-x64`) bettet den echten `sbench.exe` ein (gleicher Commit, aus `build-windows.yml`). Beim Start:

1. Datenverzeichnis = `data\` neben `sbench-gui.exe`, wenn beschreibbar (echter Schreibtest), sonst `%ProgramData%\sbench`.
2. Der eingebettete Kern wird **nie** aus dem PyInstaller-Temp-Ordner (`_MEI…`) gestartet, sondern nach `<data_dir>\bin\sbench-<version>.exe` kopiert (fehlt oder weicht ab → neu kopieren).
3. Vor **jedem** Start von sbench (auch vor `runas`/UAC) wird der SHA-256 gegen den beim Build eingebetteten Wert (`sbgui/_build_info.py`) geprüft. Abweichung → Start verweigert, klare Fehlermeldung, Datei wird aus der eingebetteten Kopie wiederhergestellt. Unter Windows hält die GUI die geprüfte Datei mit `FILE_SHARE_READ` offen (kein Austausch zwischen Prüfung und Start).
4. Jeder sbench-Aufruf erhält `--data-dir <data_dir>` (runas erbt keine Umgebung, `SBENCH_DATA_DIR` wird dafür nicht genutzt).

Quell-Läufe (Entwicklung/Tests): `SBENCH_EXE` (optional `SBENCH_EXE_SHA256`), sonst der Mock `mock_sbench.py` (Vertrag 1.3 inkl. CPU/RAM-Tests + `--data-dir`, `version --json`; `SBENCH_MOCK_CONTRACT=1.2` bzw. `1.1` = älteres Verhalten; `SBENCH_MOCK_STOP_AT=<test>:<phase>[:<n>]` stoppt deterministisch, `SBENCH_MOCK_LAG=1` simuliert Storage-Worker ohne Ack). Eine eingefrorene EXE nutzt nie den Mock.
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
- Live: Kurve des aktuellen Tests (ops/s, GB/s oder ns), CPU-Last (System/sbench), Balken + Tabelle pro Thread aus `tick.workers[]` (keine 128 Linien), Fortschrittsbalken „Puffer wird angelegt“ ab 64 MiB. Score live = `tick.score` vom Kern (1.3); nur bei Kernen < 1.3 eine als „Schätzung“ markierte Näherung aus `meta.cpu_score`/`docs/core.md`.
- Ergebnisse (neue Seite): CPU-Score je Workload/Threads, RAM GB/s je Operation/NT/SIMD und Latenz in ns, NUMA-Platzierung (`numa_node` → `mem_node`), RAM-Quelle pro Knoten (`ram_method`), `per_worker` und Zeitreihe. Abgebrochene Läufe = Teilergebnis.
- Vergleich: CPU/RAM-Tests Host vs. VM als % des Basislaufs (Latenz invertiert) plus Delta-Tabelle. Flat-CSV (`model.read_flat_csv`) liest 14 (1.1), 18 (1.2) und 20 Spalten (1.3).

**Vertrag 1.3:**
- `tick.metric` bestimmt die Kennzahl (Schlüssel-Raten nur noch als Rückfall); `phase.threads`/`cpus[]` liefern Thread-Zahl und Beschriftung „CPU · Knoten“, SMT-Geschwister werden live violett markiert.
- Teilergebnisse: `partial`/`stopped_in` → „abgebrochen in Vorbereitung/Warmup/Messung“, „keine Messwerte“; alle Verbraucher (Ergebnisse, Vergleich, Bericht, Guide, CSV, Live) sind null-sicher (`model.sval`). Demo: `demo/result-m2-partial.json`.
- Storage-Worker ohne Ack (0 in einem Tick) werden geglättet (Ø zweier Ticks) und mit ↻ markiert, nicht als Fehler.
- Score und Referenz vom Kern (`tick.score`, `summary.score_reference`, `meta.cpu_score`); Zeitreihen je Tick (`tick_ms`); Export-Hinweis: das Iometer-CSV enthält keine CPU/RAM-Tests.

**Vertrag 1.3 – Antworten von Devend / Vorschau 1.3.1:**
- SMT in den Ergebnissen aus `cpus[]` je Test (1.3.1, Zuordnung über Worker-`id`); ohne `cpus[]` nur live markiert.
- Abbruch vs. Fehler über den Exit-Code (`runs/index.jsonl`, `model.run_outcome`): 3 = abgebrochen, 1 = Laufzeitfehler – eindeutig für den 1.3-Kern (`aborted: true` auch bei Fehlern) und 1.3.1 (#17); ohne Exit-Code entscheidet `errors[]` vor `aborted`. Mock: `SBENCH_MOCK_ABORT_ON_ERROR=1` = Verhalten des 1.3-Kerns.
- Laufzeitfehler: alle `fatal`-Einträge in `errors[]` (ohne `fatal`-Feld alle Einträge), der erste hervorgehoben, weitere aufklappbar; feste Texte nur für `E_ACCESS_DENIED` („Adminrechte nötig“) und `E_OUT_OF_MEMORY` („Nicht genug Arbeitsspeicher“), sonst Code + Meldung.
- Storage-Worker ohne `lat_us` im Intervall: „–“; fehlt `lat_us` im Tick, bricht die Latenzlinie dort ab (NaN-Lücke, keine Interpolation).
- Score je Thread = ops/s ÷ Referenz × 1000 mit Referenz aus `meta.cpu_score` (live in der Thread-Tabelle, in den Ergebnissen als Balken).
- `targets[]` mit `created`/`deleted`: Hinweis in den Ergebnissen, Warnung mit Pfad bei `created: true, deleted: false` (z. B. `--keep-files`).
- `metric` `mbps` (Netz) und `latency_us` (Ping, M3) sind bekannt; live noch ohne eigene Ansicht (nur Ereignisprotokoll).
- Import-ICF (#15): deutsche Kurztexte für alle `W_ICF_*` (`model.WARN_TXT`), der Mock meldet RUN_TIME, ALIGN, REPLY, SECTOR, ZERO_WEIGHT, NO_TARGET, NO_WORKERS, MANAGERS, UNSUPPORTED.
- Mock: `SBENCH_MOCK_FAIL_AT=<test>:<E_CODE>[:<ticks>[:<einträge>]]` (Laufzeitfehler, Exit 1), `run --keep-files`, `SBENCH_MOCK_CONTRACT=1.3.1` (Vorschau, nicht schemagültig; Demo unter `demo/contract-1.3.1/`).

Windows-only und noch nicht auf echtem Windows getestet: `sbgui/pipe_win.py` (Named Pipe mit DACL, runas).
