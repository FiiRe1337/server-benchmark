# Kompatibilität von sbench (Meilenstein 1)

sbench ist ein reines Win32-Programm (Kommandozeile, keine GUI-Abhängigkeiten) mit
statischer C-Laufzeit (`/MT`). Es braucht keine VC-Redistributable, kein .NET und keine
PowerShell. Gebaut wird für **x64** und **ARM64** (nativ, ARM64 per Cross-Build
`cmake -A ARM64` auf x64).

## Mindestversion und verwendete APIs

Kompiliert mit `_WIN32_WINNT=0x0A00` (Windows 10). Alle direkt gebundenen APIs gibt es
spätestens seit Windows 8 / Server 2012, also sicher in **Windows 10 1607 /
Server 2016**. `RtlGetVersion` (ntdll) wird per `GetProcAddress` geladen.

| Bereich | APIs | verfügbar seit |
|---|---|---|
| I/O | `CreateFileW` (`FILE_FLAG_NO_BUFFERING`, `WRITE_THROUGH`, `OVERLAPPED`), `CreateIoCompletionPort`, `GetQueuedCompletionStatusEx`, `CancelIoEx`, `SetFileValidData` | Vista / XP |
| Zeit | `QueryPerformanceCounter`, `GetSystemTimes`, `GetProcessTimes`, `GetTickCount64` | XP / Vista |
| CPU/NUMA | `GetLogicalProcessorInformationEx`, `GetActiveProcessorCount`, `GetNumaHighestNodeNumber`, `GetNumaNodeProcessorMaskEx`, `GetNumaAvailableMemoryNodeEx`, CPUID (nur x64) | Windows 7 |
| Speicher | `GlobalMemoryStatusEx`, `GetPhysicallyInstalledSystemMemory`, `VirtualAlloc` | Vista SP1 |
| Hypervisor | `IsProcessorFeaturePresent(PF_VIRT_FIRMWARE_ENABLED)`, Registry `Guest\Parameters`, Dienst `vmms`, WMI `Win32_ComputerSystem` | Windows 8 |
| Netzwerk/Info | `GetAdaptersAddresses`, `DeviceIoControl` (`IOCTL_STORAGE_QUERY_PROPERTY`, `IOCTL_DISK_GET_LENGTH_INFO`) | XP / Vista |
| Sicherheit | `AdjustTokenPrivileges`, `CheckTokenMembership`, SDDL-Stop-Event | XP |

Neuere APIs (nach 1607) werden nicht verwendet. Falls künftig nötig, nur über
`GetProcAddress` mit Rückfallweg.

## Matrix

Legende: **CI** = automatisch in jedem PR geprüft (Unit-Tests + Smoke-Test bzw.
Container-Smoke), **manuell** = von michi bei der Abnahme zu prüfen
([abnahme-m1.md](abnahme-m1.md)), – = nicht vorgesehen.

„GUI“ bedeutet hier die Installationsvariante mit Desktop-Oberfläche, „CLI“ Server Core
bzw. Hyper-V Server. sbench selbst ist immer eine Konsolenanwendung.

| Betriebssystem | Variante | Arch | Physisch | VM (Hyper-V-Gast) | Prüfung |
|---|---|---|---|---|---|
| Windows Server 2025 | GUI | x64 | manuell | **CI** (`windows-2025`, Azure-VM) | CI + manuell |
| Windows Server 2025 | CLI (Core) | x64 | manuell | manuell | manuell |
| Windows Server 2022 | GUI | x64 | manuell (Hyper-V-Host) | **CI** (`windows-2022`, Azure-VM) | CI + manuell |
| Windows Server 2022 | CLI (Core) | x64 | – | **CI** (Container `servercore:ltsc2022`, Prozessisolation) | CI |
| Windows Server 2019 | CLI (Core) | x64 | – | **CI, optional** (Container `servercore:ltsc2019`, `--isolation=hyperv`, `continue-on-error`) | CI (nicht blockierend) + manuell |
| Windows Server 2019 | GUI | x64 | manuell | manuell | manuell |
| Hyper-V Server 2019 | CLI | x64 | manuell | – | manuell |
| Windows Server 2016 | GUI | x64 | manuell | manuell | manuell |
| Windows Server 2016 | CLI (Core) | x64 | manuell | manuell | manuell |
| Windows 11 | GUI | x64 | manuell | manuell | manuell |
| Windows 11 | GUI | ARM64 | manuell (z. B. Snapdragon) | **CI** (`windows-11-arm`, Azure-VM) | CI + manuell |
| Windows 10 (22H2 / LTSC 2019/2021) | GUI | x64 | manuell | manuell | manuell |

### Was die CI pro Runner prüft

| Job | Inhalt |
|---|---|
| Build x64 / ARM64 (`windows-latest`) | MSVC, `/MT`, `/guard:cf`; `dumpbin /dependents` prüft, dass nur System-DLLs (kein `VCRUNTIME*.dll`, kein `ucrtbase.dll`) gebunden sind; Artefakte `sbench-windows-<arch>`, `sbench-cli-<arch>` |
| Test `windows-2022` x64, `windows-2025` x64, `windows-11-arm` arm64 | Unit-Tests; Smoke-Test: info/validate gegen Schema, Storage-Lauf (256 MiB, 4K random 70/30, QD8, 2 Worker, 5 s), Pipe, Pipe-Fehler/-Abbruch, Stop (Exit 3), `list --running`, nicht implementierte Testtypen, Export, Bundle (`--all`, `--results`, `--include`, `--anonymize`), Volume-Lesetest auf `\\.\C:`; Artefakt `sbench-smoke-<runner>-<arch>` |
| Container `servercore:ltsc2022` (auf `windows-2022`) | Server Core ohne Python: version, info, validate, run, export, list, bundle; Schema-Prüfung der Ausgaben danach auf dem Host |
| Container `servercore:ltsc2019` (Hyper-V-Isolation) | wie oben, Kernel 17763; Fehlschlag blockiert den PR nicht |

### Hinweise

- **Runner `windows-2019`:** von GitHub zum 30.06.2025 abgeschaltet (Issue
  actions/runner-images#12045). Ein Job mit diesem Label würde nicht mehr starten. Kernel
  17763 (Server 2019) prüfen wir deshalb über den Container `servercore:ltsc2019` mit
  Hyper-V-Isolation; der Container-Job läuft (Stand dieses PRs) erfolgreich.
- **Server 2016 (Build 14393):** In der CI nicht verfügbar (kein Runner, und
  `servercore:ltsc2016` läuft auf aktuellen Hosts nur mit Hyper-V-Isolation und wird nicht
  mehr gepflegt). Muss manuell geprüft werden: Ablauf in `docs/abnahme-m1.md`,
  Abschnitt 8.3 (Issue #7). Ergebnis: *noch offen*.
- **Container und Hypervisor-Rolle:** In einem Container mit Prozessisolation teilt sbench
  den Kernel des Hosts. Auf dem `windows-2022`-Runner meldet `info` deshalb
  `role = host` (CPUID-Bit *CreatePartitions*), obwohl der Runner selbst eine Azure-VM ist:
  Der Runner hat Hyper-V aktiviert und läuft verschachtelt (nested). Mit Hyper-V-Isolation
  (ltsc2019) meldet sbench `role = guest`. Details zur Erkennung in [core.md](core.md).
- **ARM64:** Kein CPUID. Die Hypervisor-Rolle kommt aus Registry (`Guest\Parameters`),
  Dienst `vmms`, `IsProcessorFeaturePresent` und WMI; `partition_info.role_source` nennt
  die verwendete Quelle, `provisional: true` kennzeichnet eine Einschätzung ohne CPUID.
- **Iometer** gibt es nur für x64 mit Desktop-Oberfläche. Auf Core-Systemen und ARM64 nur
  mit DiskSpd vergleichen.

## GUI (sbench-gui, Entwurf, Stand 20.10.2026)

Für `sbench.exe` gilt die Matrix oben; die CLI-Spalten unten geben nur die Erwartung aus Sicht der GUI-Planung wieder.

Status: **unterstützt** = offiziell vom Hersteller (Qt/Python/Microsoft) unterstützt *und* bei uns getestet ·
**erwartet** = laut Herstellerangaben/abgeleitet lauffähig, bei uns noch nicht getestet ·
**ungetestet** = unklar, muss auf echter Maschine geprüft werden · **nicht unterstützt** = bekannt nicht lauffähig/ausgeschlossen.

Die GUI ist noch nicht auf echter Windows-Hardware getestet (CI: `gui-ci.yml` prüft auf `windows-2022`/`windows-2025` nur Lint, Unit-Tests und `--self-test` offscreen von Quelle und EXE); "unterstützt" kommt daher in der Tabelle noch nicht vor.
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

### Wichtige Folgerungen

1. **Pin `PySide6-Essentials>=6.12,<6.13`** (Qt 6.12 LTS): letzte Qt-Linie mit Windows 10 [Q2] und damit mit Server 2019/2022 (Win-10-Basis) sowie inoffiziell Server 2016 [Q3]. Ab 6.13 kein Win-10-CI mehr [Q3].
2. **Python 3.13** bündeln: dokumentiertes Minimum 1607/Server 2016 [P2]. 3.13 ist seit 10/2026 nur noch Security-Phase ohne neue Binaries [P4]; 3.14 ist Bugfix-Phase – Server-2016-Support folgt aus der PEP-11-Regel (Release während MS-Extended-Support) [P1], ist aber *nicht explizit dokumentiert → testen*.
3. Server 2016: Option A (empfohlen) Qt 6.12 + Smoke-Test auf echter 2016-VM; Option B CLI-only + Remote-GUI. Option C PySide2/Qt 5.15 **nicht empfohlen** (Qt 5.15 Open Source EOL, keine aktuellen Python-Wheels *– unverifiziert, nicht recherchiert*).
4. Server Core: GUI nur „best effort“ mit FOD; Standardweg dort = CLI (`sbench run`) + Ergebnis/Support-ZIP in der GUI auf einer Admin-Workstation auswerten.
5. ARM64: zunächst x64-EXE unter Emulation; nativer ARM64-Build erst, wenn `windows-11-arm`-Job grün ist.

### Quellen
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
