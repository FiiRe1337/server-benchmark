# Entwicklung

## Verzeichnisse

| Pfad | Inhalt |
|---|---|
| `core/`, `cli/`, `third_party/`, `CMakeLists.txt` | C-Messkern und `sbench.exe` ([core.md](core.md)) |
| `schema/` | JSON Schemas und Beispiele (Vertrag, [architektur.md](architektur.md#schnittstellen-vertrag)) |
| `gui/` | PySide6-GUI, Mock-Kern `mock_sbench.py`, Tests, PyInstaller-Spec ([gui/README.md](../gui/README.md)) |
| `tests/smoke/` | Smoke-Tests des Kerns (Windows) |
| `jobs/` | Abnahme-Jobs ([abnahme-m1.md](abnahme-m1.md)) |
| `docs/` | Doku; `docs/img/guide/` = Screenshots der README-Anleitung |

## Kern bauen (Windows, MSVC)

```
cmake -S . -B build -A x64
cmake --build build --config Release --parallel
ctest --test-dir build -C Release --output-on-failure
```

Details (ARM64, statische CRT, Versionen): [cli.md – Build & Plattformen](cli.md#build--plattformen).

## GUI lokal

```
cd gui
pip install -r requirements.txt -r requirements-ci.txt
ruff check . && QT_QPA_PLATFORM=offscreen python -m pytest
python -m sbgui                       # gegen $SBENCH_EXE oder den Mock
python -m sbgui --self-test --report st.json
```

Portable EXE mit eingebettetem Kern:
`python tools/gen_build_info.py --core <sbench.exe> --run-core && pyinstaller --noconfirm sbench-gui.spec`.

## Schemas prüfen

```
pip install jsonschema
python tools/validate_examples.py
```

## CI (GitHub Actions)

| Workflow | Auslöser | Inhalt |
|---|---|---|
| `build-windows.yml` | Kern-Pfade (push/PR), `workflow_call` | Kern x64/ARM64, Unit- und Smoke-Tests, Server-Core-Container |
| `gui-ci.yml` | `gui/**` u. a. | ruff, pytest, Self-Test, E2E gegen Mock; `core` → `gui` (portable EXE mit echtem Kern, `build-gui.yml`) |
| `build-gui.yml` | nur `workflow_call` | `sbench-gui.exe` (PyInstaller onefile), Self-Test mit echtem Kern, Artifact `sbench-gui-x64` |
| `screenshots.yml` | `gui/**` (PR), `workflow_dispatch` | echte Screenshots der README-Anleitung (siehe unten) |
| `release.yml` | Tag `v*`, `workflow_dispatch` | Release mit Kern, GUI und `SHA256SUMS.txt` ([release.md](release.md)) |
| `schema.yml` | `schema/**` | Schema-Beispiele |

## Screenshots der README-Anleitung aktualisieren

Die Bilder in `docs/img/guide/` sind **echte Aufnahmen** der aktuellen GUI mit dem echten
Kern auf dem Runner `windows-2022` (Azure-VM), keine Mockups:

1. `screenshots.yml` läuft bei PRs mit Änderungen unter `gui/**` automatisch, sonst
   *Actions → GUI screenshots (README-Anleitung) → Run workflow* auf dem Branch.
2. Ablauf: `core` (build-windows.yml) → `gui` (build-gui.yml) → `guide`: die portable EXE
   aus `C:\Tools\sbench` mit `--guide` ([`gui/sbgui/guide.py`](../gui/sbgui/guide.py)),
   Plattform-Plugin `windows`, Fenster 1440×900, 100 %. Echte `info`, zwei echte
   Storage-Läufe (2 Worker × QD 8 und 1 Worker × QD 1) auf `D:\sbench\testdatei.dat`,
   Live-Ansicht während der Messung, ein dritter Lauf wird gestoppt, Vergleich der beiden
   vollständigen Läufe, Support-Paket `--all --anonymize`. Dazu Explorer-Ordner und
   Konsolenfenster mit echten CLI-Aufrufen ([`gui/tools/capture_window.py`](../gui/tools/capture_window.py)).
3. Artifact `guide-screenshots` laden, prüfen (`report.json`: `ok`, keine abgeschnittenen
   Seiten) und nach `docs/img/guide/` committen:

   ```
   gh run download <run-id> -n guide-screenshots -D docs/img/guide
   ```

Lokal (ohne Windows) lässt sich der Ablauf gegen den Mock testen:
`QT_QPA_PLATFORM=offscreen python -m sbgui --guide /tmp/guide --size 1440x900` (Bilder dann
mit Mock-Daten, nicht einchecken).

Die alten Mockups (`--screenshot`, Mock-Kern mit Beispieldaten) bleiben als Entwicklungswerkzeug
erhalten, werden aber nicht mehr eingecheckt.
