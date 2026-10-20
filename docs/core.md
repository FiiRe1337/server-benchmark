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
- **NUMA:** `GetNumaNodeProcessorMaskEx`; `cpus` enthält globale Indizes
  Prozessorgruppe × 64 + Bitnummer (bei nicht voll belegten Gruppen also mit Lücken), freier Speicher je Knoten aus `GetNumaAvailableMemoryNodeEx`.
  **Grenze:** Win32 liefert keinen Gesamt-RAM je Knoten. Bei einem Knoten ist
  `ram_bytes` der installierte RAM, bei mehreren Knoten der installierte RAM geteilt
  durch die Knotenzahl (Näherung, mindestens der freie Speicher des Knotens).
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

## Grenzen und bekannte Abweichungen (M1)

- Nur `storage` ist implementiert. `cpu`, `memory`, `net_client`, `net_server` und
  `hyperv_info` erzeugen ein `error`-Event `E_NOT_IMPLEMENTED` und werden übersprungen;
  sie erscheinen nicht in `tests[]` des Ergebnisses.
- Kein Pinning der Worker auf CPUs oder NUMA-Knoten; Windows verteilt die Threads.
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
