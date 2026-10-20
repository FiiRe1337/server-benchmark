# sbench GUI – Plan (Entwurf v0.3, 20.10.2026)

Portable Windows-GUI für den Messkern `sbench.exe` (Devend). Python 3.13 + PySide6 6.12 + pyqtgraph, ausgeliefert als **eine** EXE.
Schnittstelle ist ausschließlich der CLI-Vertrag in diesem Repo (**Vertrag 1.1**, `main` @ 5670020 = PR #1 + PR #3 + Kern M1 PR #2: `docs/cli.md`, `docs/core.md`, `schema/*.schema.json`). **Kein ctypes/DLL-Binding an den Messkern** (ctypes nur für Win32-Pipe/UAC in `sbgui/pipe_win.py`). Einzige Schema-Quelle ist `schema/` auf `main` (inkl. `sb-bundle/1`); die frühere GUI-Kopie unter `gui/contract/` ist entfernt.

Code: `gui/` · Mockups (**Mockup mit Beispieldaten**, Mock-Kern): `docs/img/` (1440×900) und `docs/img/rdp1366/` (1366×768); 150 % wird lokal/in der CI nach `docs/img/scale150/` gerendert (nicht eingecheckt).

## 1. Architektur

```
 Views (QWidget-Seiten)  ←Signals/Slots→  ViewModels/Controller (MainWindow, Seiten-Logik)
                                              │
                         model.py (reine Funktionen auf sb-*/1-Dokumenten, Schema-Check, Labels, Einheiten)
                                              │
            sbench.py  Binding-Schicht: QProcess → sbench.exe  (run / stop / info / validate / list / export / version / bundle / import-icf)
                       RunSession (stdout-JSONL) · ElevatedRunSession (Named Pipe + runas) · TailSession (runs/<id>.jsonl, Re-Attach) · Replay
                                              │
            buffers.py RingBuffer (numpy, 36 000 Zeilen ≈ 2,5 h @ 250 ms) + Min/Max-Downsampling (≤ 1 500 Punkte/Kurve)
```

- **MVVM-artig:** Seiten kennen nur Signale (`run_requested`, `stop_requested`, `result_ready`, `elevate_requested`, `support_requested`); Prozess-/Dateizugriff liegt in `sbench.py`. Model-Funktionen sind Qt-frei und unit-testbar.
- **Kein Polling-Thread nötig:** `QProcess.readyReadStandardOutput` liefert asynchron im GUI-Thread; Zeilen werden gepuffert, je Zeile `json.loads`, Event-Signal. Charts werden entkoppelt per **QTimer 10 Hz** neu gezeichnet (Tick-Rate 100–1000 ms ist unabhängig davon). Falls JSON-Parsing bei sehr vielen Workern spürbar wird: Reader in `QThread` verschieben (gleiche Signale).
- **Event-Regeln (Vertrag):** `start` (run_id, tests) → je Test `phase` preparing/warmup/measure/cleanup → `tick` *nur in measure*, `t` ab Messbeginn (GUI addiert Offset je Test für eine durchgehende x-Achse) → `done` (immer letztes Event). Ergebnis-JSON **erst nach `done` lesen** (Kern schreibt `<out>.tmp` + Rename). Unbekannte Hauptversionen (`sb-result/2`) werden abgelehnt, unbekannte `E_*`-Codes generisch angezeigt.
- **Exit-Codes:** 0 ok · 1 Laufzeitfehler (ggf. Teilergebnis) · 2 ungültiger Job (Fehler mit `field`-Pointer → Editor markiert Feld) · 3 abgebrochen (Teilergebnis, `aborted: true`) · 4 fehlende Rechte → Dialog „Als Administrator neu starten“.
- **Erhöhte Läufe (Windows-only, `sbgui/pipe_win.py`, auf echtem Windows noch ungetestet):** GUI bleibt `asInvoker`. Vor dem Start legt sie `\\.\pipe\sbench-<run_id>` per `CreateNamedPipeW` an: `PIPE_ACCESS_INBOUND | FILE_FLAG_FIRST_PIPE_INSTANCE`, `PIPE_REJECT_REMOTE_CLIENTS`, DACL `D:P(A;;GA;;;<User-SID>)(A;;GA;;;BA)` (nur eigenes Konto + Administratoren). Dann `ShellExecuteExW("runas", sbench.exe run … --progress pipe:…)`; UAC-Abbruch → Hinweis, kein Lauf. sbench verbindet sich bis zu 10 s, sonst `E_PIPE_CONNECT`. Lebendigkeit per `OpenProcess`/`GetExitCodeProcess`. Der Mock und alle Linux-Checks nutzen `--progress stdout`. Stop immer über `sbench stop --id` (Named Event), Kill nur als Notfall.
- **Re-Attach:** sbench schreibt jeden Lauf zusätzlich nach `<data_dir>\runs\<run_id>.jsonl` (+ `lock.json` während des Laufs, `runs/index.jsonl` danach). GUI-Start → `sbench list --running --json` (`{"schema":"sb-runs/1","runs":[…]}`) → Dialog „Laufender Test gefunden“ (Ignorieren / Stoppen / Wieder verbinden). `TailSession` liest die Datei von Anfang an, folgt ihr bis `done`, Exit-Code aus `index.jsonl`; verschwindet der Lauf aus `list --running` ohne `done` → „Verbindung verloren“.
- **Datenordner:** wird **nicht** in der GUI berechnet. Kern-Regel (cli.md 1.1): `SBENCH_DATA_DIR` → `data\` neben `sbench.exe` (wenn beschreibbar) → `%ProgramData%\sbench`. Die GUI zeigt und nutzt ausschließlich `meta.data_dir` aus `sbench info --json`. Eigene Dateien der GUI liegen in `%LOCALAPPDATA%\sbench-gui\` (`logs/gui-*.log`, `profiles/`, `results/<run_id>.json` als `--out`) bzw. `SBENCH_GUI_DIR`; sie kommen per `bundle --include` ins Support-ZIP.
- **Toleranz:** die 1.1-Felder (`meta.elevated/data_dir/ram_free_bytes/numa_nodes`, `warnings[]`, `progress`, R/W-Split, Worker-`lat_us`, `proc_cpu_pct`, `name`) sind im Schema optional, werden vom Kern immer ausgegeben; die GUI kommt ohne sie aus („Rechte unbekannt“, abgeleitete Labels, kombinierte Kurven, Spalten „–“).

## 2. Screens (Mockups)

| Screen | Datei | Inhalt |
|---|---|---|
| Übersicht | `01_dashboard.png` | Karten CPU/RAM/Hypervisor/Netz aus `sbench info --json` (sb-info/1), Volumes, physische Disks, NICs, Schnellstart-Kacheln |
| Testprofil | `02_profile_editor.png` | Profilliste, Ziele (file/volume/physical, Worker→Ziel-Zuordnung i mod n), **gewichtete Access Specs** (Gewicht, Block, % Lesen, % Random, Align, Burst, Delay) mit Mix-Balken, Worker/QD/Cache/Warmup/Dauer/Tick, Live-JSON-Vorschau |
| Validierung | `02b_profile_validation.png` | `sbench validate` → sb-validate/1; `errors[]` und `warnings[]` (W_NEEDS_ADMIN, W_DESTRUCTIVE, W_LOW_DISK_SPACE) in der Karte „Prüfung“, Pfade markieren Zelle/Feld/Ziel |
| Destruktiv | `02c_destructive_confirm.png` | Rot gestalteter Dialog: je Volume/Disk-Ziel mit Schreibanteil muss der Zielname eingetippt werden; erst dann `allow_destructive: true` im Job **und** `--allow-destructive` beim Start |
| Rechte | `02d_elevation_offer.png` | bei W_NEEDS_ADMIN: „Als Administrator starten“ (runas) oder „Ohne Rechte starten“ (nur ohne Raw-Ziele) |
| ICF-Import | `02e_icf_import.png` | Menü „Import ▾ → Iometer-Profil importieren (.icf)“ → `sbench import-icf` → Warnungen (W_ICF_UNSUPPORTED) vor Übernahme |
| Live-Lauf | `03_live_run.png` (Replay), `03b_live_preparing.png`, `03c_live_qprocess.png` (QProcess gegen Mock) | Phasen-Chips, Fortschrittsbalken auch in preparing (Bytes) / warmup (s) aus `progress` + `phase.duration_s`, KPI IOPS/MB/s/Ø/p99, CPU System + sbench-Prozess, Schalter „Lesen/Schreiben getrennt“, Worker-Tabelle mit Ø µs/p99 µs, Event-Log, Stop |
| Re-Attach | `03d_reattach_offer.png` | beim GUI-Start gefundener Lauf (sb-runs/1) |
| Vergleich | `04_compare.png` | 2–3 Läufe (z. B. Host vs. VM vs. VM-SAN), gruppierte Balken je Test (Metrik wählbar), Perzentil-Kurve p50…p99.99 (log), Delta-Tabelle mit Ampel |
| Export | `05_export.png` | Auswahl, Formate, Optionen, HTML-Vorschau, **„Ergebnisse für Support exportieren“** |
| Support-Dialog | `06_support_dialog.png` | Umfang alle/ausgewählte, GUI-Logs, Profile, **Anonymisieren** |
| Support-Toast | `07_support_export_done.png` | Erfolgsmeldung mit Anzahl Dateien, „Ordner öffnen“ |

**Kleine Bildschirme / RDP:** Mindestgröße 1280×720. Unter 1400 px Breite klappt die Sidebar auf Icons (64 px) zusammen (Umschalter ☰). Seiten liegen in ScrollAreas; Seitenspalten (`Responsive`-Container) stapeln sich unterhalb der Summe ihrer Mindestbreiten bzw. eines Breakpoints. Geprüft per Screenshot-Bericht (`content_min`, `h_scroll`, `v_scroll`): bei 1440×900, 1366×768 und 1280×720 kein horizontales Scrollen; nur der Profileditor scrollt vertikal (Prüfungs-Karte + JSON).

Der Support-Button sitzt zusätzlich dauerhaft im Sidebar-Footer; „Auswahl für Support exportieren“ (Vergleich/Export) ruft `sbench bundle --results <files>` auf.

## 3. Profilformat

- Profile **sind** `sb-job/1`-Dateien (keine eigene Hülle): `profiles/*.json`, direkt an `sbench run --job` übergebbar. GUI-Metadaten (Favorit, Farbe, zuletzt benutzt) in separater `profiles/.gui-meta.json`, damit Jobs schema-konform bleiben (`additionalProperties: false`).
- Anzeige-Namen: optionales `name` je Test und je Access Spec (1.1). Editor: Namensspalte editierbar, abgeleitete Namen kursiv/gedimmt („8K rand 70/30“). Vergleich matcht Tests über `name`, sonst über das abgeleitete Label.
- Schnellstart-Profile werden mit ausgeliefert (Four Corners, OLTP 8K, 1M seq, CPU, RAM, Netz, Hyper-V-Info, „Vollständiger Lauf“ = Devends `job-full.json`).
- Iometer-`.icf`-Import über **`sbench import-icf <icf> --out <job>`** (Kern, M2); die GUI zeigt die Warnungen (nicht abbildbare Felder) und übernimmt das Ergebnis als Profil. Der Mock parst nur die Access-Spec-Sektion.

## 4. Export

| Format | Quelle | Bemerkung |
|---|---|---|
| JSON | Kopie von sb-result/1 | Basis für Vergleich/Re-Import |
| CSV flat / Iometer | `sbench export <result> --csv <out> --style flat|iometer` | Kern bleibt alleinige CSV-Wahrheit |
| HTML-Bericht | GUI (`report.py`) | eigenständig, Charts als eingebettete PNG (pyqtgraph `ImageExporter`) |
| PDF | GUI: gleiches HTML → `QTextDocument` → `QPdfWriter` (A4) | kein WebEngine nötig |
| Support-ZIP | `sbench bundle --all|--results … [--include <dir>]… [--anonymize] --progress stdout --out <zip>` | manifest.json (sb-bundle/1, SHA-256 je Datei, `anonymized`, `includes`), info.json, results/, runs/, logs/, `extra/<ordner>/` (GUI-Logs, Profile); Anonymisieren ersetzt Host/Benutzer/IP/MAC/Seriennr. durch Pseudonyme |

## 5. Packaging

- **PyInstaller one-file, windowed** (`gui/sbench-gui.spec`): `sbench.exe` als `datas` eingebettet (unverändert kopiert, SHA-256 zur Build-Zeit in `sbgui/_build_info.py`; zur Laufzeit Kopie nach `<data_dir>\bin\sbench-<version>.exe`, Hash-Prüfung vor jedem Start, nie aus `_MEI…` ausgeführt, siehe `gui/README.md`), `sb_contract.py`, Schemas (`schema/`), Profile, Demo-Daten, Fonts (Inter, JetBrains Mono, OFL) als `datas`, unnötige Qt-Module (Quick, QML, WebEngine) ausgeschlossen (QtOpenGL bleibt, pyqtgraph braucht es). Erwartete Größe ~45–60 MB *(Schätzung)*. Kein UPX (AV-Fehlalarme).
- **One-file-Fallen:** Entpackt nach `%TEMP%\_MEIxxxx` und löscht beim Beenden → sbench.exe beim ersten Start nach `sbench-data\bin\` kopieren (Hash-Prüfung) und von dort starten, v. a. für erhöhte Läufe (überlebt GUI-Ende, kein Start aus `%TEMP%`, AppLocker-freundlicher). `--self-test`/`--screenshot` schreiben JSON per `--report <datei>`, da windowed EXE kein stdout hat.
- **Nuitka** als Alternative (`--onefile --enable-plugin=pyside6`): schnellerer Start, weniger AV-Treffer, aber längere CI-Builds und MSVC nötig; Entscheidung nach M2 anhand Startzeit/AV-Scan.
- **Rechte/UAC:** EXE-Manifest `asInvoker`; normale Datei-Ziele brauchen keinen Admin. Admin nur für `volume`/`physical` (und schnelles `SetFileValidData`) → UAC-Prompt nur für sbench.exe. Schreibtests auf Volume/Disk zerstören Daten → doppeltes Opt-in: `allow_destructive` am Ziel **und** `--allow-destructive` bei `run`; die GUI setzt beides erst nach Tippen des Zielnamens (auch wenn das Profil das Flag schon trägt).
- **Server Core:** GUI erfordert Desktop Experience, auf Server Core die CLI nutzen.
- Code-Signing (Authenticode) einplanen, sonst SmartScreen-Warnung bei portabler EXE.

## 6. CI (GitHub Actions)

- `ci.yml` (später): Lint, Unit-Tests (`model.py`, Ringpuffer, Parser), `tools/check_mock.py` gegen Devends Schemas, Headless-GUI-Test (`QT_QPA_PLATFORM=offscreen`, `tools/live_qprocess_check.py`).
- `release.yml`: PyInstaller-Build auf `windows-2022`, Core-Binaries aus Devends Release-Artefakt, Signieren, Upload.
- `screenshots.yml` (Entwurf liegt unter `.github/workflows/`): siehe Abschnitt 7.

## 7. Kompatibilität & CI-Screenshots

Details mit Quellen: **`docs/kompatibilitaet.md`** (Matrix OS × CLI/GUI × physisch/VM).

1. **Zielsysteme:** Qt 6.12 unterstützt offiziell Windows 10 ab 1809, Windows 11 und ARM64; Windows Server wurde von Qt nie offiziell unterstützt, nur „kompatibel“. **Qt 6.12 ist die letzte Version mit Windows 10**; in Qt 6.13 wurde die inoffizielle Server-2016-Kompatibilität entfernt. Python 3.13 hat als Minimum 1607 (= Server 2016). → **Pins: Python 3.13, `PySide6-Essentials>=6.12,<6.13`.** Server 2016 (1607): Qt ≥ 6.8.0 lief dort nicht, Fix ab 6.8.4/6.9.1 → mit 6.12 „erwartet, inoffiziell“, Pflichttest auf echter 2016-VM. Optionen, falls es scheitert: (A) eigener Qt-6.12-Build mit Patch, (B) **CLI-only auf 2016 + GUI auf Admin-PC** (empfohlener Fallback, 2016 endet 12.01.2027), (C) PySide2/Qt 5.15 – nicht empfohlen (Doppelpflege, Qt 5 EOL).
2. **Server Core:** App-Compat-FOD (`Add-WindowsCapability -Online -Name ServerCore.AppCompatibility~~~~0.0.1.0`, ab Server 2019) bringt u. a. explorer.exe, mmc, perfmon; für 2016 nicht verfügbar. Ob `qwindows.dll` (dwmapi, uxtheme, …) auf Core ohne/mit FOD lädt: **ungetestet – michi muss es auf einer echten Server-Core-VM prüfen** (`sbench-gui.exe --self-test --report st.json`, `QT_DEBUG_PLUGINS=1`). GUI erkennt Core (`HKLM\…\CurrentVersion\InstallationType = "Server Core"`) und nutzt dann Qt-eigene Dateidialoge (`DontUseNativeDialog`) und mitgelieferte Fonts. Empfehlung: GUI auf Core nur mit FOD als „best effort“; Standard = CLI auf dem Core-Host, Auswertung (Result-JSON/Support-ZIP) in der GUI auf einem anderen Rechner; später ggf. Remote-Modus (sbench via PowerShell-Remoting/OpenSSH starten, Progress per `file:` auf Share). TUI nur, falls Nachfrage.
3. **DPI:** Qt 6 ist standardmäßig Per-Monitor-DPI-aware V2, Rundungs-Policy `PassThrough` (Default, zusätzlich explizit gesetzt) → 125/150/175 % exakt. Layouts nur in geräteunabhängigen Pixeln; pyqtgraph-Linienbreiten/Fonts skalieren mit (geprüft: 150 %-Render 2160×1350, lokal; CI: `docs/img/scale150/`). Test in CI bei 100 % und 150 % (`QT_SCALE_FACTOR`), manuell zusätzlich 125 %/200 % und Monitorwechsel.
4. **Ohne GPU / RDP / VM:** `pg.setConfigOptions(useOpenGL=False)` (ist auch pyqtgraph-Default), nur Qt Widgets mit Raster-Paint-Engine, kein QtQuick/QOpenGLWidget → `opengl32sw.dll` und `QT_OPENGL=software` werden nicht benötigt und nicht gebündelt. Kurven per Min/Max-Downsampling auf ≤ 1 500 Punkte, Redraw 10 Hz, Antialiasing per Option abschaltbar für langsame RDP-Sitzungen.
5. **README-Screenshots in Windows-CI:** `sbench-gui --screenshot <dir> [--scale 1.5] [--size 1366x768] [--replay progress.jsonl] [--info info.json] [--results dir] [--report f.json]` rendert deterministisch per `QWidget.grab()` 13 Screens (Overlays sind Kind-Widgets und damit im Grab). Live-View per **Replay** ohne Timer. Datenquelle: Smoke-Artefakt **`sbench-smoke-windows-2022-x64`** (`result.json`, `progress.jsonl`, `info.json`, cli.md 1.1), bis dahin `gui/demo/` (Mock). Workflow-Entwurf `.github/workflows/screenshots.yml` (nur `workflow_dispatch` + Tags) rendert 100 %, 150 % und 1366×768 nach `docs/img/` und öffnet einen PR. Zusätzlich Job `selftest` (windows-2022, windows-2025, windows-11-arm): EXE bauen, `--self-test --platforms windows,offscreen`, `dumpbin /dependents` für Qt6Gui/Qt6Widgets/qwindows, und Versuch im Container `servercore:ltsc2022` (continue-on-error; Container haben by design keine GUI, FOD dort vermutlich nicht installierbar – *unverifiziert*). windows-2019 ist abgekündigt.
6. **`--self-test`** (implementiert): startet je Plattform-Plugin (`windows`, `offscreen`) einen Kindprozess (fehlendes Plugin bricht sonst den Prozess ab), baut das Hauptfenster, grabbt einen Frame, meldet Qt-/PySide-Version, DPR, Fonts und unter Windows die geladenen DLLs; Exit 0/1 + JSON (`sbench-gui-selftest/1`).

## 8. Meilensteine

| M | Inhalt | Ergebnis |
|---|---|---|
| **M1 Mock-GUI** (läuft) | Screens, Theme, QProcess-Binding gegen schema-konformen Mock, Replay, Screenshot-/Self-Test-Modus, Schema-Checks | dieser Prototyp |
| **M2 Storage gegen echten Kern** | `sbench.exe` aus feat/core-m1, Named-Pipe-Server + runas, Stop/Teilergebnis, Profil speichern/laden, Datenordner, PyInstaller-EXE, CI-Screenshots aus echtem Smoke-Run | erste portable EXE |
| **M3 CPU/RAM/Netz/Hyper-V** | Editor + Live-Ansichten je Testtyp (ops/s, GB/s, Mbit/s, RTT), net_server-Gegenstelle, Hyper-V-Info-Seite | volle Testabdeckung |
| **M4 Vergleich/Export/Polish** | Vergleich aller Typen, HTML/PDF, Support-ZIP (sb-bundle/1), .icf-Import, 125/200 %-DPI, Server-2016/Core-Tests, Signing | Release 1.0 |

## 9. Abweichungen: Schema (gilt) vs. frühere Vorgaben

Stand Vertrag 1.0; mit 1.1 erledigt sind: `name` je Test/Spec, meta `elevated/data_dir/ram_free_bytes/numa_nodes`, `warnings[]`, `progress`, R/W-Split, Worker-Latenz, `proc_cpu_pct`, sb-runs/1.


- `--progress stdout` statt `jsonl`; ohne `--progress` keine Events. `--tick-ms` Default 1000, Minimum 100.
- `start` hat `run_id` (nicht `id`) + `tests`; neue Phase `preparing`; **Ticks nur in `measure`**, `t` ab Messbeginn; Tick ohne `phase`/`progress`; `workers[]` nur `id/iops/mbps` (keine Worker-Latenz).
- Targets sind Objekte `{path, kind: file|volume|physical, file_size nur bei file}`; `file_size` je Target statt je Test. Kein `name` je Test/Spec; `burst_len` statt `burst`; Cache `writethrough` (ohne Bindestrich); Größen nur `KiB|MiB|GiB` (Integer) oder Bytes.
- Validierung: `errors[].path` + `code` (nicht `pointer`/`field`); Error-Events nutzen `field`. Exit 2 auch für ungültige Argumente.
- `sbench export … --csv <out.csv>` schreibt in Datei (nicht stdout). Neues `sbench version`. `stop` → Exit 1 bei unbekannter ID.
- Result: Top-Level `kern_version`, `started_utc`, `ended_utc`, `aborted`; Job-Kopie je Test in `config`; Perzentile `p90, p95, p99, p99_9, p99_99` (+ `stddev`); zusätzlich `per_spec`; Histogramm log2, nicht kumulativ, `le_us: null` = Überlauf.
- Meta/Info: `os` als Objekt, nur `ram_bytes` (kein freier RAM/NUMA/Takt), `hypervisor {present, vendor, role: none|host|guest}` (nicht „vm“), VM-Anzahl nur über Test `hyperv_info`, NICs ohne RDMA-Flag, Volumes mit `\\.\C:`-Pfad.
- `sbench bundle`/sb-bundle/1 ist noch nicht Teil des Vertrags (kommt mit feat/core-m1).

## 10. Offene Fragen an Devend (Stand Vertrag 1.1)

1. `sb-result/1` hat kein `run_id`: `bundle --results <datei>` muss Ergebnis → Lauf über `runs/index.jsonl` (`out`) zuordnen. `run_id` ins Result aufnehmen?
2. Stop bei erhöhtem Lauf: kann ein nicht erhöhtes `sbench stop --id` das vom erhöhten Prozess erzeugte Event öffnen (Global/Local, DACL)? Muss real geprüft werden.
3. `sb-runs/1` enthält keinen Job-Namen (nur `lock.json`): für den Re-Attach-Dialog `job` bzw. Job-Name in `runs[]` aufnehmen?
4. `numa_nodes[].cpus` ist Liste **oder** Anzahl – GUI kann beides; welche Form liefert der Kern?
5. Smoke-Artefakt-Name: cli.md sagt `sbench-smoke-<runner>-<arch>`, die CI in `feat/core-m1` lädt `smoke-results-<os>-<arch>` hoch – bitte vereinheitlichen.
6. `sb-bundle/1` auf `main` bringen (inkl. `runs/`, `extra/`, `anonymized`, `includes`, wie in cli.md 1.1 beschrieben).

## 11. Offene Punkte

- [ ] Auf echtem Windows ungetestet: Named Pipe mit DACL + `PIPE_REJECT_REMOTE_CLIENTS`, runas/UAC-Abbruch, Prozess-Lebendigkeit, Stop über Rechtegrenze, Re-Attach nach GUI-Absturz, native Dateidialoge, PyInstaller-EXE.
- [ ] Server 2016 (Desktop) und Server 2019/2022/2025 real testen (michi, VMs) → Matrix aktualisieren. Server Core: CLI.
- [ ] Python 3.13 (Security-Phase, keine neuen Binaries) vs. 3.14 (Bugfix) entscheiden, nachdem 3.14 auf Server 2016 getestet ist.
- [ ] PyInstaller vs. Nuitka nach Startzeit/AV-Test in M2; Code-Signing-Zertifikat klären.
- [ ] Barrierefreiheit (Kontrast, Tastaturbedienung), Hell-Theme optional; i18n falls Englisch gewünscht.

## Anhang: Prototyp-Dateien (`gui/`)

`mock_sbench.py` (CLI-Mock, Vertrag 1.1) · `sb_contract.py` (Schema-Laden aus `../schema` + sb-bundle-Vorabversion) · `sbgui/` (App; `pipe_win.py` Windows-only) · `tools/check_mock.py` (Schema- und Semantik-Check für JSON, JSONL-Streams, Bundle-ZIPs) · `tools/live_qprocess_check.py` (E2E gegen Mock) · `tools/make_demo.py` (Demo-Daten neu erzeugen) · `profiles/` · `demo/` · `sbench-gui.spec` · `requirements.txt`.
