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
  mehr gepflegt). Muss manuell geprüft werden.
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
