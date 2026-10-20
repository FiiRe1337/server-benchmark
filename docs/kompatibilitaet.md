# Kompatibilitätsmatrix (Entwurf, Stand 20.10.2026)

Status: **unterstützt** = offiziell vom Hersteller (Qt/Python/Microsoft) unterstützt *und* bei uns getestet ·
**erwartet** = laut Herstellerangaben/abgeleitet lauffähig, bei uns noch nicht getestet ·
**ungetestet** = unklar, muss auf echter Maschine geprüft werden · **nicht unterstützt** = bekannt nicht lauffähig/ausgeschlossen.

Noch ist **nichts** auf echter Windows-Hardware getestet; "unterstützt" kommt daher in der Tabelle noch nicht vor.
Annahmen: GUI = PyInstaller-EXE mit Python 3.13 + PySide6-Essentials 6.12.x (Qt 6.12), x64. CLI = `sbench.exe` (C, Win32-API, kein Qt). Laut cli.md 1.1: Mindestversion Windows 10 1607 / Server 2016, statische CRT (`/MT`), x64 und ARM64 nativ.

| OS | CLI physisch | CLI VM (Hyper-V) | GUI physisch | GUI VM (Hyper-V, RDP/VMConnect) | Hinweise |
|---|---|---|---|---|---|
| Windows 11 (23H2+) x64 | erwartet | erwartet | erwartet | erwartet | Qt 6.12: offiziell [Q1]; Python 3.13 offiziell [P1] |
| Windows 10 22H2 x64 | erwartet | erwartet | erwartet | erwartet | Qt 6.12 offiziell ab 1809 [Q1]; **Qt 6.12 ist die letzte Qt-Version mit Windows 10** [Q1][Q2] |
| Windows Server 2025 (Desktop) | erwartet | erwartet | erwartet | erwartet | Windows-Server ist bei Qt nie offiziell gelistet, nur „kompatibel“ [Q3]; 2025 basiert auf 24H2 (Win-11-Codebasis) – *unverifiziert* |
| Windows Server 2022 (Desktop) | erwartet | erwartet | erwartet | erwartet | wie oben; Basis 21H2 [Q3] |
| Windows Server 2019 (Desktop) | erwartet | erwartet | erwartet | erwartet | Basis 1809 = Qt-Minimum [Q1][Q3] |
| Windows Server 2016 (Desktop) | erwartet | erwartet | ungetestet | ungetestet | Qt ≥ 6.8.0 brach 1607, Fix in 6.8.4/6.9.1 (QTBUG-134075) [Q4][Q5]; inoffizielle 2016-Kompatibilität **in Qt 6.13 entfernt** [Q3]. Python 3.13 Minimum = 1607 (`NTDDI_WIN10_RS1`) [P2]. MS-Extended-Support endet 12.01.2027 [P3] |
| Server Core 2019/2022/2025 ohne App-Compat-FOD | erwartet | erwartet | ungetestet | ungetestet | GUI-APIs vorhanden, aber kein Explorer/Shell → native Dateidialoge fraglich *(unverifiziert)* |
| Server Core 2019/2022/2025 **mit** App-Compat-FOD | erwartet | erwartet | ungetestet | ungetestet | FOD bringt u. a. `explorer.exe`, `mmc.exe`, `perfmon.exe` [M1]; ab Server 2019 [M1] |
| Server Core 2016 | erwartet | erwartet | nicht unterstützt | nicht unterstützt | kein App-Compat-FOD für 2016 [M1] + Qt-1607-Risiko → CLI oder Remote-GUI |
| Container `servercore:ltsc2022` | erwartet | – | nicht unterstützt (interaktiv) | – | Container haben by design keine GUI/RDP [M2][M3]; nur `--self-test` (offscreen) sinnvoll. FOD in Containern: *unverifiziert, vermutlich nicht installierbar* (MS nennt nur „Server Core installations and images“ [M1]) |
| Container `server:ltsc2022` | erwartet | – | nicht unterstützt (interaktiv) | – | enthält mehr GUI-APIs als servercore [M3]; Alternative für Self-Test |
| Windows 11 ARM64 | ungetestet (x64-Emulation oder ARM64-Build von sbench) | – | ungetestet | – | PySide6-Essentials hat `win_arm64`-Wheels (6.10–6.12) [Q6]; Qt offiziell ARM64 [Q1]; PyInstaller-ARM64 *unverifiziert*; Fallback: x64-EXE unter Emulation |

> **GUI erfordert Desktop Experience, auf Server Core die CLI nutzen.** Auf Server Core läuft `sbench.exe` (CLI) normal; Ergebnisse bzw. das Support-ZIP werden in der GUI auf einem anderen Rechner ausgewertet. Die Core-Zeilen unten bleiben als „ungetestet“ dokumentiert, sind aber kein Ziel für M1/M2.

**Kleine Auflösungen:** Mindestfenster 1280×720, getestet (offscreen) bei 1440×900, 1366×768 (typische RDP-Sitzung) und 1280×720 sowie 150 % Skalierung.

## Wichtige Folgerungen

1. **Pin `PySide6-Essentials>=6.12,<6.13`** (Qt 6.12 LTS): letzte Qt-Linie mit Windows 10 [Q2] und damit mit Server 2019/2022 (Win-10-Basis) sowie inoffiziell Server 2016 [Q3]. Ab 6.13 kein Win-10-CI mehr [Q3].
2. **Python 3.13** bündeln: dokumentiertes Minimum 1607/Server 2016 [P2]. 3.13 ist seit 10/2026 nur noch Security-Phase ohne neue Binaries [P4]; 3.14 ist Bugfix-Phase – Server-2016-Support folgt aus der PEP-11-Regel (Release während MS-Extended-Support) [P1], ist aber *nicht explizit dokumentiert → testen*.
3. Server 2016: Option A (empfohlen) Qt 6.12 + Smoke-Test auf echter 2016-VM; Option B CLI-only + Remote-GUI. Option C PySide2/Qt 5.15 **nicht empfohlen** (Qt 5.15 Open Source EOL, keine aktuellen Python-Wheels *– unverifiziert, nicht recherchiert*).
4. Server Core: GUI nur „best effort“ mit FOD; Standardweg dort = CLI (`sbench run`) + Ergebnis/Support-ZIP in der GUI auf einer Admin-Workstation auswerten.
5. ARM64: zunächst x64-EXE unter Emulation; nativer ARM64-Build erst, wenn `windows-11-arm`-Job grün ist.

## Quellen
- [Q1] Qt 6.12 Supported Platforms – https://doc.qt.io/qt-6/supported-platforms.html (Windows 10 1809+, Windows 11, ARM64; „Qt 6.12 will be the last version to support Windows 10“)
- [Q2] Qt Blog „Windows 10 End-of-Life Plans in Qt“ – https://www.qt.io/blog/windows-10-eol-plans-in-qt
- [Q3] Qt development list, 18.09.2026 – https://lists.qt-project.org/pipermail/development/2026-September/047475.html und …/047478.html (Server 2016 inoffiziell in 6.13 entfernt; „Qt has never supported Windows Server“)
- [Q4] qtbase Commit 5ed1dcd „Fix usage of API functions on Windows 1607“ (QTBUG-134075) – https://github.com/qt/qtbase/commit/5ed1dcd1cc3adf26c591eefc161495a4c313d15d
- [Q5] Praxisbericht Nextcloud (Qt6Core `SetThreadDescription` auf Server 2016) – https://github.com/nextcloud/desktop/issues/8064 *(Drittquelle)*
- [Q6] Qt-for-Python-Downloadindex (win_arm64-Wheels) – https://download.qt.io/official_releases/QtForPython/pyside6-essentials/ ; PyPI – https://pypi.org/project/PySide6/
- [P1] PEP 11 – https://peps.python.org/pep-0011/
- [P2] CPython Issue #113723 (Minimum `NTDDI_WIN10_RS1` für 3.13) – https://github.com/python/cpython/issues/113723
- [P3] CPython Issue #121458 (Server 2016 Extended Support bis 12.01.2027) – https://github.com/python/cpython/issues/121458 ; MS Lifecycle: https://learn.microsoft.com/en-us/lifecycle/products/windows-server-2016 *(nicht separat abgerufen)*
- [P4] Python Devguide „Status of Python versions“ – https://devguide.python.org/versions/
- [M1] Microsoft Learn „Server Core App Compatibility Feature on Demand“ – https://learn.microsoft.com/en-us/windows-server/get-started/server-core-app-compatibility-feature-on-demand
- [M2] Microsoft Learn „Lift and shift to containers“ – https://learn.microsoft.com/en-us/virtualization/windowscontainers/quick-start/lift-shift-to-containers
- [M3] microsoft/Windows-Containers Issue #306 (GUI by design nicht vorhanden; `server`-Image hat GUI-APIs) – https://github.com/microsoft/Windows-Containers/issues/306
- [G1] windows-2019-Runner abgekündigt (30.06.2025) – https://github.com/actions/runner-images/issues/12045 ; ARM64-Image GA – https://github.blog/changelog/2026-08-20-windows-11-arm64-vs2026-image-generally-available/ *(nur Suchergebnis, nicht abgerufen)*
