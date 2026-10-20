# Messkern (sbcore) – Methodik und Grenzen

Dieses Dokument beschreibt, **wie** der Messkern misst und wo die Grenzen liegen. Die
Schnittstelle (Befehle, JSON-Formate, Exit-Codes) ist in [cli.md](cli.md) und `schema/`
festgelegt; bei Widersprüchen gilt der Vertrag.

## Aufbau

| Baustein | Inhalt |
|---|---|
| `sbcore_static` | Statische Bibliothek mit dem gesamten Kern (C11/C17, reines Win32) |
| `sbcore.dll` | Dieselbe Bibliothek als DLL; öffentliche API in `core/include/sb/sb.h` (`sb_run`, `sb_stop`, `sb_validate`, `sb_info`, `sb_export_csv`, `sb_bundle`, `sb_list_running`) |
| `sbench.exe` | Kommandozeile, statisch gegen `sbcore_static` gelinkt |
| Drittanbieter | cJSON 1.7.18 (MIT), miniz 3.0.2 (MIT) unter `third_party/` mit Lizenzdateien |

## Storage-Engine

### Ablauf eines Tests

1. **preparing** – Testdateien anlegen. Mit `SeManageVolumePrivilege` (Admin) wird die
   Datei per `SetEndOfFile` + `SetFileValidData` sofort auf volle Größe gebracht, ohne
   Daten zu schreiben. Sonst wird sie vollständig mit Zufallsdaten beschrieben (1 MiB
   Blöcke, ungepuffert). Eine vorhandene, ausreichend große Datei wird weiterverwendet.
   Fortschritt: Event `progress` (Einheit `bytes`) etwa einmal pro Sekunde.
2. **Worker einrichten** – je Worker ein Thread, ein eigenes Handle auf das Ziel und
   **ein eigener I/O Completion Port**. Worker *i* arbeitet auf Target *i mod n*.
3. **warmup** – Last läuft, wird aber nicht gezählt. Event `progress` (Einheit `s`).
4. **measure** – gezählt wird jede I/O, die **in der Messphase gestartet und vor ihrem
   Ende abgeschlossen** wurde. I/Os, die über das Ende hinauslaufen, fallen heraus (kein
   Verzerren durch angeschnittene I/Os).
5. **cleanup** – laufende I/Os werden mit `CancelIoEx` abgebrochen, Threads beendet,
   Handles und Puffer freigegeben. Danach löscht sbench Testdateien, die es in diesem Test
   selbst angelegt hat (`CreateFileW` meldete nicht `ERROR_ALREADY_EXISTS`); das gilt nach
   normalem Ende, nach `stop`/Strg+C und nach Fehlern. Vorher vorhandene Dateien bleiben
   unangetastet, auch wenn sbench sie vergrößert hat. `--keep-files` schaltet das Löschen
   ab. Ergebnis pro Test: `targets[].created`/`deleted`. Wird der Prozess hart beendet
   (Task-Manager, `TerminateProcess`), bleibt die Datei liegen.

### I/O-Pfad

- Öffnen mit `FILE_FLAG_OVERLAPPED`, bei `cache = unbuffered` zusätzlich
  `FILE_FLAG_NO_BUFFERING | FILE_FLAG_WRITE_THROUGH` (entspricht DiskSpd `-Sh`),
  bei `writethrough` nur `FILE_FLAG_WRITE_THROUGH`, bei `buffered` keins von beiden.
- Jeder Worker hält **queue_depth** I/Os gleichzeitig offen. Abgeschlossene I/Os werden
  mit `GetQueuedCompletionStatusEx` gebündelt abgeholt und sofort neu gestartet.
- Puffer kommen aus `VirtualAlloc` (seitenausgerichtet, erfüllt die Ausrichtung für
  ungepufferte I/O). Lesepuffer: einer je offener I/O. Schreibdaten: ein Bereich
  von 4 MiB mit Zufallsdaten (xoshiro256**), aus dem jede Schreib-I/O einen
  wechselnden Ausschnitt nimmt. Die Daten sind damit nicht komprimierbar und nicht
  deduplizierbar.
- **Access-Specs:** Pro I/O wird gewichtet (`weight`, Summe 100) eine Spec gewählt,
  danach Lesen/Schreiben nach `read_pct` und zufällig/sequentiell nach `random_pct`.
- **Offsets:** zufällig gleichverteilt auf `align`-Raster (Standard = `block_size`) im
  ganzen Ziel; sequentiell fortlaufend je Worker. Teilen sich mehrere Worker ein Target,
  starten ihre sequentiellen Zeiger gleichmäßig verteilt (z. B. bei 2 Workern bei 0 und
  der Hälfte), damit sie nicht dieselben Blöcke lesen.
- **burst_len / delay_ms:** Nach `burst_len` I/Os einer Spec pausiert der Worker
  `delay_ms` Millisekunden (wie Iometer „Burst Length“ / „Transfer Delay“).
- **Zufallszahlen:** xoshiro256**, initialisiert per splitmix64. Der Seed ist fest
  (`0x5be7c0de00000000 ^ Testindex`, je Worker abgeleitet), weil `sb-job/1` kein
  Seed-Feld hat. Zwei Läufe desselben Jobs erzeugen daher dieselbe Offset-Folge.

### Volume- und Disk-Ziele

`\\.\X:` und `\\.\PhysicalDriveN` brauchen Administratorrechte (sonst Exit 4 vor dem
Start). Größe und Sektorgrößen kommen aus `IOCTL_DISK_GET_LENGTH_INFO` und
`IOCTL_STORAGE_QUERY_PROPERTY` (Access-Alignment). Schreibtests auf diese Ziele
zerstören Daten und laufen nur, wenn das Target `allow_destructive: true` hat **und**
`sbench run` mit `--allow-destructive` aufgerufen wird.

## Messung

- **Zeitmessung:** `QueryPerformanceCounter` unmittelbar vor `ReadFile`/`WriteFile` und
  beim Abholen der Completion. Die Latenz enthält damit auch die Zeit, bis der Worker die
  Completion abholt. Bei sehr hoher Last (viele IOPS je Worker) ist das ein kleiner
  Aufschlag, wie bei DiskSpd und Iometer auch.
- **Histogramm (HDR-ähnlich):** Werte in Nanosekunden. Unter 256 ns exakt, darüber
  128 Unterklassen je Zweierpotenz → relative Klassenbreite ≤ 0,78 %. Obergrenze ca.
  275 s. Perzentile = Mitte der Klasse, begrenzt auf min/max. Mittelwert und
  Standardabweichung werden exakt aus Summe und Quadratsumme berechnet, min/max exakt.
- **Ausgabe:** `histogram` im Ergebnis mit 32 Klassen je Zweierpotenz (gröber als intern,
  damit die Datei klein bleibt) und einer Überlaufklasse `le_us: null`.
- **Ticks:** Jeder Worker führt zwei Intervall-Puffer (Zähler + Histogramm). Der
  Haupt-Thread fordert im Takt `--tick-ms` einen Wechsel an, der Worker bestätigt ihn
  zwischen zwei I/Os, ohne Sperren im I/O-Pfad. Ein letztes Teilintervall wird
  gemeldet, wenn es mindestens 10 % eines Ticks lang ist.
- **CPU:** `cpu_pct` = Systemlast über alle logischen Prozessoren aus `GetSystemTimes`.
  `proc_cpu_pct` = CPU-Zeit von sbench (`GetProcessTimes`) bezogen auf alle logischen
  Prozessoren (gleiche Skala).
- **MB/s** sind dezimal (10⁶ Bytes/s). DiskSpd meldet MiB/s (× 1,048576 umrechnen).

## Systeminformationen (`info`)

- **OS:** `RtlGetVersion` (nicht von Kompatibilitäts-Shims verfälscht), Produktname und
  Installationsart (Server Core / Desktop) aus der Registry; Windows 11 wird an der
  Build-Nummer erkannt.
- **CPU:** Modell aus CPUID (x64) bzw. Registry (ARM64), Sockel/Kerne/Threads aus
  `GetLogicalProcessorInformationEx`.
- **RAM:** `GetPhysicallyInstalledSystemMemory`, frei aus `GlobalMemoryStatusEx`.
- **NUMA:** `GetLogicalProcessorInformationEx(RelationNumaNodeEx)` (Fallback
  `RelationNumaNode`), auch für Knoten über mehrere Prozessorgruppen; `cpus` enthält
  globale Indizes Prozessorgruppe × 64 + Bitnummer (bei nicht voll belegten Gruppen also
  mit Lücken), freier Speicher je Knoten aus `GetNumaAvailableMemoryNodeEx`.
  **RAM pro Knoten (Issue #8):** Win32 hat keine API für den Gesamt-RAM eines Knotens.
  `ram_method` nennt die Quelle von `ram_bytes`:
  - `total` – nur ein Knoten: `ram_bytes` = für Windows nutzbarer physischer RAM
    (`GlobalMemoryStatusEx.ullTotalPhys`).
  - `srat` – mehrere Knoten: ACPI-Tabelle SRAT über `GetSystemFirmwareTable('ACPI', 'SRAT')`
    (keine Adminrechte nötig). Summiert werden die Memory-Affinity-Einträge (Typ 1) je
    Proximity Domain, die aktiv und weder Hotplug noch nichtflüchtig sind; die Domain wird
    per `GetNumaProximityNodeEx` dem Knoten zugeordnet. Plausibilitätsprüfung: alle
    Domains zugeordnet, Summe zwischen der Hälfte des sichtbaren und dem installierten RAM
    (`GetPhysicallyInstalledSystemMemory`) + 5 %. Die SRAT-Werte enthalten auch vom
    Firmware/Hypervisor reservierte Bereiche; die Summe kann daher etwas über
    `meta.ram_bytes` liegen.
  - `estimate` – SRAT fehlt oder ist unplausibel (z. B. manche VMs, Container): Aufteilung
    des Gesamt-RAM im Verhältnis des freien Speichers der Knoten (ungenau).
- **Hypervisor:** Alle verfügbaren Methoden werden ausgewertet und unter
  `partition_info.methods` ausgegeben:
  1. CPUID (nur x64): Hypervisor-Bit, Vendor (Leaf 0x40000000), Privileg
     *CreatePartitions* (Leaf 0x40000003) → Root-Partition = `host`.
  2. Registry `HKLM\SOFTWARE\Microsoft\Virtual Machine\Guest\Parameters` → Gast.
  3. Dienst `vmms` installiert → Hyper-V-Rolle vorhanden.
  4. `IsProcessorFeaturePresent(PF_VIRT_FIRMWARE_ENABLED)`.
  5. WMI `Win32_ComputerSystem` (`HypervisorPresent`, Modell/Hersteller).
  `role_source` nennt die Methode, die die Rolle bestimmt hat; `provisional: true` heißt
  Einschätzung ohne eindeutigen Beleg. **Grenze verschachtelt (nested):** Ein Hyper-V-Host,
  der selbst eine VM ist (z. B. GitHub-Runner auf Azure), meldet per CPUID `host`, hat aber
  gleichzeitig Gast-Registry und WMI-Modell „Virtual Machine“. Beide Angaben stehen in
  `methods`; die Rolle bezieht sich immer auf die Partition, in der sbench läuft.
- **Volumes, Disks, NICs:** `FindFirstVolume`/`GetVolumePathNamesForVolumeName` (nur Volumes mit Laufwerksbuchstaben)/`GetVolumeInformation`,
  `\\.\PhysicalDrive0..127` per IOCTL, `GetAdaptersAddresses` (IPv4/IPv6, Linkspeed).

## Lauf-Verwaltung und Datenverzeichnis

- Datenverzeichnis: `SBENCH_DATA_DIR` → `data\` neben `sbench.exe` (falls beschreibbar)
  → `%ProgramData%\sbench`. Legt sbench `%ProgramData%\sbench` neu an, erhalten
  authentifizierte Benutzer Änderungsrechte (vererbt), damit eine nicht erhöhte GUI die
  Dateien eines per `runas` erhöhten Laufs lesen und aufräumen kann.
- Je Lauf: `logs\<run_id>.log` (UTC-Zeitstempel, Windows-Fehlertexte), `jobs\<run_id>.json`
  (Job-Kopie), `runs\<run_id>.jsonl` (alle Events, unabhängig von `--progress`),
  `runs\<run_id>.lock.json` (während des Laufs) und eine Zeile in `runs\index.jsonl`
  am Ende (auch bei Abbruch oder Fehler, mit `exit_code`).
- `list --running` prüft je Lock-Datei, ob der Prozess noch läuft und vor `started_utc`
  gestartet wurde (Schutz gegen wiederverwendete PIDs). Verwaiste Locks werden gelöscht.
- Stop: benanntes Event `Global\sbench-stop-<id>` (Rückfall `Local\`), DACL erlaubt
  einem nicht erhöhten Prozess das Setzen. Strg+C setzt dasselbe Event.

## Bundle und Anonymisierung

`--anonymize` arbeitet auf allen `.json`-, `.jsonl`- und `.log`-Dateien (auch `extra/`,
`info.json`, `manifest.json`). Ersetzt werden:

| Was | Erkennung | Pseudonym |
|---|---|---|
| Hostnamen | eigene Namen (DNS, NetBIOS, FQDN, physischer Hyper-V-Host aus der Registry) und alle Werte von JSON-Schlüsseln `host`, `hostname`, `*physical_host*`; Vergleich ohne Groß-/Kleinschreibung an Wortgrenzen | `host-1` … |
| Benutzernamen | Pfadteil nach `\Users\`, `/Users/` (auch JSON-maskiert); ausgenommen `Public`, `Default` | `user-1` … |
| IPv4 | vier Oktette 0–255 (nicht 0.0.0.0, 127.x, 255.255.255.255) | `10.0.0.1` … |
| IPv6 | Hex-Gruppen mit `::` oder genau 7 Doppelpunkten, inkl. Zonen-ID | `fd00::1` … |
| MAC | sechs Hex-Paare mit `:` oder `-` | `02:00:00:00:00:01` … |
| Disk-Seriennummern | Seriennummern der lokalen Disks (`IOCTL_STORAGE_QUERY_PROPERTY`) und Werte von JSON-Schlüsseln `*serial*` | `serial-1` … |

**Grenzen:** Die Erkennung ist heuristisch. Fremde Hostnamen, die nur als Freitext
(nicht unter einem der Schlüssel) vorkommen, werden nicht erkannt. Dateinamen im ZIP
und Binärdateien bleiben unverändert. Die Zuordnung gilt nur innerhalb eines Bundles und
wird nicht gespeichert.

## CPU- und Memory-Engine (Meilenstein 2)

### Ablauf

1. **Threadauswahl** (`topo.c`): Topologie aus `GetLogicalProcessorInformationEx`
   (Gruppen, Kerne mit SMT-Geschwistern, NUMA-Knoten). Reihenfolge: erst ein logischer
   Prozessor je physischem Kern, die Knoten abwechselnd (bzw. nur `numa_node`), dann die
   SMT-Geschwister; bei mehr Threads als Prozessoren beginnt die Liste von vorn
   (`W_OVERSUBSCRIBED`). `threads: 0` = alle (des Knotens).
2. **Binden:** Jeder Thread wird angehalten erzeugt, per `SetThreadGroupAffinity` auf
   genau einen logischen Prozessor gebunden (funktioniert über Prozessorgruppen hinweg,
   also auch bei mehr als 64 logischen Prozessoren) und mit
   `SetThreadIdealProcessorEx` markiert.
3. **preparing** (memory): Jeder Thread legt seinen Puffer selbst mit
   `VirtualAllocExNuma` auf `mem_node` bzw. dem eigenen Knoten an und beschreibt ihn
   vollständig (First Touch), damit die physischen Seiten auf dem Zielknoten liegen.
   Normale 4-KiB-Seiten (keine Large Pages: dafür wäre `SeLockMemoryPrivilege` nötig).
4. **warmup** und **measure**: Die Threads arbeiten in Batches, deren Größe sich auf
   0,5–2 ms einregelt; nach jedem Batch wird ein eigener, cache-zeilen-getrennter Zähler
   aktualisiert. Der Hauptthread liest die Zähler zu Beginn und Ende der Messphase und pro
   Tick (QPC-Zeitstempel). Die Messung enthält damit höchstens einen Batch Unschärfe je
   Thread an den Rändern (≤ 2 ms bei ≥ 1 s Messdauer).

### memory: Bandbreite

| op | Kernel | gezählt |
|---|---|---|
| `read` | lädt den ganzen Puffer sequenziell und verknüpft die Werte per XOR/ADD (kein Wegoptimieren) | gelesene Bytes |
| `write` | schreibt den Puffer sequenziell | geschriebene Bytes |
| `copy` | kopiert die erste Pufferhälfte in die zweite | gelesene + geschriebene Bytes (STREAM-Zählweise) |

`simd`: `scalar` (64-Bit-Zugriffe, Vektorisierung abgeschaltet), `sse2` (128 Bit),
`avx2` (256 Bit), `avx512` (512 Bit, nur auf ausdrückliche Anforderung, weil der Takt
auf manchen CPUs sinkt), `neon` (128 Bit, ARM64). `auto` = `avx2`, sonst `sse2` (x64) bzw.
`neon` (ARM64). Erkennung per CPUID und XGETBV (das Betriebssystem muss die
YMM/ZMM-Register sichern). `nontemporal` nutzt `_mm_stream_*` + `sfence` (x64) und
umgeht die Caches beim Schreiben; das zeigt die echte Schreibbandbreite ohne
Read-for-Ownership. GB/s = 10^9 Byte/s, Summe aller Threads.

### memory: Latenz

Pointer Chasing: Der Puffer wird in 64-Byte-Zeilen geteilt, die in zufälliger Reihenfolge
(zufällige Permutation nach Sattolo, fester Seed) zu **einem** Zyklus über alle Zeilen verkettet werden. Jeder
Zugriff hängt vom vorherigen ab, Hardware-Prefetcher können nicht vorhersagen.
`latency_ns` = Messzeit ÷ Anzahl Sprünge, immer mit einem Thread. Bei Puffern über dem
L3-Cache enthält der Wert auch TLB-Fehlzugriffe (4-KiB-Seiten), wie bei Intel MLC ohne
Large Pages; deshalb liegt er typischerweise etwas über MLC-`--idle_latency`. Puffer im
L1/L2 (z. B. 16 KiB) zeigen die Cache-Latenz.

### cpu

| workload | 1 Operation |
|---|---|
| `int` | eine Runde aus Addition, Multiplikation, Rotation, XOR, Shift und einem Tabellenzugriff auf vier abhängigen 64-Bit-Ketten |
| `float` | drei Multiplikations-/Additionsketten und eine Division (double) |
| `crypto` | SHA-256 über einen 64-Byte-Block (portable Implementierung ohne SHA-Befehle) |
| `mixed` | 128 `int` + 64 `float` + SHA-256 einer 64-Byte-Nachricht (mit Padding zwei Blöcke); auf dem Referenzsystem je etwa ein Drittel der Zeit |

Die Kernel sind skalar und laufen auf x64 und ARM64 mit identischer Arbeit; sie messen die
Ganzzahl-/Gleitkomma-Leistung je Kern und die Skalierung über Kerne, nicht SIMD-Durchsatz.

**Score:** `score = ops_per_s / Referenz × 1000`. Referenz = ops/s **eines** Threads auf
dem GitHub-Runner `windows-2022` mit AMD EPYC 9V74 (Smoke-Lauf der M2-Kalibrierung, siehe
PR #13). Die Werte sind ab Vertrag 1.2 fest; GitHub-Runner wechseln die CPU-Typen, ein
Score von 1000 auf einem Runner ist deshalb kein Testkriterium. 1000 heißt „ein Thread der Referenzmaschine“,
ein 16-Kern-System mit gleicher Kernleistung erreicht im Mehrthreadtest etwa 16 000
(plus SMT-Anteil). Die Referenzen stehen in `sb_cpu_reference()` (`compute.c`).

| workload | Referenz (ops/s, 1 Thread) |
|---|---|
| `int` | 3,25 · 10^8 |
| `float` | 1,75 · 10^8 |
| `crypto` | 4,15 · 10^6 |
| `mixed` | 8,0 · 10^5 |

## Grenzen und bekannte Abweichungen

- `net_client`, `net_server` und `hyperv_info` erzeugen ein `error`-Event
  `E_NOT_IMPLEMENTED` und werden übersprungen; sie erscheinen nicht in `tests[]` des
  Ergebnisses.
- Storage-Worker werden nicht gebunden; Windows verteilt die Threads. cpu/memory binden
  jeden Thread (siehe oben).
- memory nutzt keine Large Pages; Latenzwerte großer Puffer enthalten TLB-Fehlzugriffe.
- Die CPU-Kernel sind skalar; Vergleiche mit Cinebench oder 7-Zip zeigen Tendenzen,
  keine gleichen Zahlen (siehe `docs/abnahme-m2.md`).
- Selbst angelegte Testdateien werden nach jedem Test gelöscht. Nutzen mehrere Tests
  eines Jobs dieselbe Datei, legt jeder Test sie neu an; zum Wiederverwenden großer Dateien
  `--keep-files` angeben.
- Fester Zufalls-Seed (kein Feld im Vertrag).
- Gelesene Daten werden nicht geprüft (keine Datenintegritäts-Prüfung).
- Höchstens 64 Targets und 64 Access-Specs je Test.
- `export --style iometer` erzeugt ein vereinfachtes Iometer-CSV (Kopf `'Results`,
  Zeilen `ALL` und je Worker `WORKER`), nicht jede Iometer-Spalte ist belegt.
- Exit-Code 4 (fehlende Rechte) ist in der CI nicht prüfbar, weil die Runner als Admin
  laufen; die Vorabprüfung ist im Code getrennt implementiert und im Abnahmetest manuell
  zu prüfen.
