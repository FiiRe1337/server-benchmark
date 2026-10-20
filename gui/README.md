# sbench-gui (Prototyp M1)

PySide6/pyqtgraph-GUI für `sbench.exe`, steuert ausschließlich die CLI (QProcess + JSON Lines, Vertrag 1.1 in [`../schema`](../schema) und [`../docs/cli.md`](../docs/cli.md)).
Plan: [`../docs/GUI_PLAN.md`](../docs/GUI_PLAN.md) · Kompatibilität: [`../docs/kompatibilitaet.md`](../docs/kompatibilitaet.md)

> **GUI erfordert Desktop Experience, auf Server Core die CLI nutzen.**

Ohne `sbench.exe` (Variable `SBENCH_EXE`, neben der EXE oder eingebettet) startet die GUI den Mock `mock_sbench.py`, der den Vertrag 1.1 nachbildet.

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

CI ([`../.github/workflows/gui-ci.yml`](../.github/workflows/gui-ci.yml)): Windows 2022/2025 (ruff, pytest, `--self-test` offscreen, PyInstaller-EXE + Self-Test der EXE, Artefakt `sbench-gui-<runner>-x64`) und Linux (Schemas, `check_mock.py`, E2E, Self-Test). Lokal:

```
pip install -r requirements.txt -r requirements-ci.txt     # exakte CI-Pins
ruff check . && QT_QPA_PLATFORM=offscreen python -m pytest
```

Umgebungsvariablen: `SBENCH_EXE` (Kern), `SBENCH_DATA_DIR` (Datenordner des Kerns; die GUI liest ihn nur aus `meta.data_dir`), `SBENCH_GUI_DIR` (GUI-Logs/Profile/Ergebnisse, Standard `%LOCALAPPDATA%\sbench-gui`), `SBENCH_SCHEMA_DIR`.
Mock: `SBENCH_MOCK_ELEVATED=1` simuliert Administratorrechte, `SBENCH_MOCK_SPEED` beschleunigt Läufe.

Windows-only und noch nicht auf echtem Windows getestet: `sbgui/pipe_win.py` (Named Pipe mit DACL, runas).
