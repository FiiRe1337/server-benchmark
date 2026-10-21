# Abnahme Meilenstein 2 – Anleitung für den Tester (CPU, RAM, NUMA)

Diese Anleitung beschreibt, wie die Testtypen `cpu` und `memory` aus Meilenstein 2 und die
NUMA-Angaben (Issue #8) auf echter Hardware geprüft werden. Wir vergleichen mit
**Intel MLC**, **AIDA64**, **`winsat mem`**, **7-Zip** und **Cinebench**; den Import von
Iometer-Konfigurationen (`sbench import-icf`) mit **Iometer** (Abschnitt 10). Zum Schluss
packst du alles in ein Bundle und schickst es zurück.

Methodik und Definitionen der Kennzahlen: [core.md](core.md#cpu--und-memory-engine-meilenstein-2).
Felder und Fehlercodes: [cli.md](cli.md#testtypen-cpu-und-memory-vertrag-12). Was die CI
automatisch prüft: [kompatibilitaet.md](kompatibilitaet.md).

---

## 1. Voraussetzungen

| Was | Details |
|---|---|
| Betriebssystem | Windows 10/11 (ab 1607) oder Windows Server 2016 bis 2025, auch Server Core. x64 oder ARM64. |
| Rechte | Für sbench **keine** Adminrechte nötig. Intel MLC braucht eine Administrator-Konsole. |
| Hardware | Ideal: ein Server mit **2 Sockeln** (2 NUMA-Knoten) für Abschnitt 6, dazu ein normaler PC/Laptop. |
| Ruhe auf dem System | Keine VMs, Backups, Updates oder Virenscans. Energieplan „Höchstleistung“ (`powercfg /setactive SCHEME_MIN`). Laptop am Netzteil. |
| Intel MLC | <https://www.intel.com/content/www/us/en/developer/articles/tool/intelr-memory-latency-checker.html>, `Windows\mlc.exe` nach `C:\Tools\mlc\`. Nur x64, verlässlich nur auf Intel-CPUs. |
| AIDA64 (optional) | Extreme oder Engineer, Testversion genügt: *Tools → Cache & Memory Benchmark*. Nur mit Desktop-Oberfläche. |
| `winsat` | In Windows 10/11 enthalten; auf Server-Versionen meist nicht vorhanden (dann überspringen). |
| 7-Zip | <https://www.7-zip.org>, Kommandozeile `7z.exe` (x64 oder ARM64). |
| Cinebench (optional) | Cinebench 2024 (Maxon), nur mit Desktop-Oberfläche. |

## 2. Artefakt und Job-Dateien

Wie in [abnahme-m1.md](abnahme-m1.md), Abschnitt 2: Artefakt `sbench-cli-<arch>` des
neuesten grünen Laufs auf `main` (bzw. des PR-Branches `feat/m2-mem-cpu`) nach
`C:\Tools\sbench\` entpacken. Die Job-Dateien aus `jobs\abnahme-m2\` (im Repository bzw. im
Artefakt `sbench-windows-<arch>`) nach `C:\Tools\sbench\jobs-m2\` kopieren.

## 3. Schnellcheck und NUMA-Angaben (Issue #8)

```bat
cd /d C:\Tools\sbench
sbench version
sbench info --json > C:\Abnahme\info.json
sbench validate --job jobs-m2\03-ram-bandbreite.json
```

In `info.json` unter `meta.numa_nodes` prüfen:

| Prüfung | Erwartet |
|---|---|
| Anzahl Knoten | wie im Task-Manager (*Leistung → CPU → Rechtsklick → Graph ändern in → NUMA-Knoten*) bzw. `coreinfo -n` (Sysinternals) |
| `cpus` je Knoten | disjunkt, zusammen alle logischen Prozessoren (`meta.cpu.threads`) |
| `ram_method` | ein Knoten: `total`; mehrere Knoten: `srat` (sonst notieren, dann ist es `estimate`) |
| `ram_bytes` je Knoten | Summe der DIMMs des Sockels (siehe unten), Abweichung ≤ 3 % (Firmware-Reservierungen) |
| Summe `ram_bytes` | ungefähr `meta.ram_bytes` (bei `srat` bis ca. 3 % darüber) |

RAM pro Sockel nachzählen (PowerShell):

```powershell
Get-CimInstance Win32_PhysicalMemory | Select-Object DeviceLocator, BankLabel, @{n='GiB';e={$_.Capacity/1GB}}
```

Die `DeviceLocator`-Namen (z. B. `CPU1_DIMM_A1`, `P2-DIMMB1`) zeigen den Sockel. Auf einem
Hyper-V-Host geht es direkter: `Get-VMHostNumaNode | Select NodeId, MemoryTotal, MemoryAvailable`
(MemoryTotal in MB). Ergebnis in die Tabelle in Abschnitt 8 eintragen.

## 4. Job-Dateien

| Nr. | Job-Datei | Inhalt | Dauer ca. |
|---|---|---|---|
| 01 | `01-cpu-1-thread.json` | int, float, crypto, mixed mit 1 Thread | 2,5 min |
| 02 | `02-cpu-alle-threads.json` | dasselbe mit allen logischen Prozessoren | 2,5 min |
| 03 | `03-ram-bandbreite.json` | read, write, write NT, copy, copy NT; alle Threads, 256 MiB pro Thread | 2 min |
| 04 | `04-ram-bandbreite-1-thread.json` | read scalar/auto, copy; 1 Thread, 1 GiB | 40 s |
| 05 | `05-latenz.json` | Latenz mit 16 KiB, 256 KiB, 8 MiB, 1 GiB (L1, L2, L3, RAM) | 50 s |
| 06 | `06-numa-lokal-remote.json` | Latenz und Bandbreite Knoten 0 → RAM 0 bzw. RAM 1, CPU nur Knoten 0 (**nur bei ≥ 2 Knoten**) | 1,5 min |
| 07 | `07-simd-x64.json` | read 1 Thread mit SSE2 und AVX2 (**nur x64**) | 30 s |
| 08 | `08-avx512-optional.json` | read mit AVX-512 (**nur CPUs mit AVX-512F**, sonst `E_SIMD_UNSUPPORTED`) | 40 s |

`threads: 0` heißt alle logischen Prozessoren. Bei 03 belegt sbench `threads` × 256 MiB;
auf Maschinen mit wenig RAM meldet `validate` `W_LOW_MEMORY` (unkritisch) oder
`E_OUT_OF_MEMORY` – dann `buffer_size` auf `"128MiB"` senken und notieren. Der Puffer
soll insgesamt mindestens das Vierfache des L3-Caches sein.

## 5. Messung

Jeden Job mit sbench **zweimal** laufen lassen; weichen die Läufe um mehr als 3 % (CPU)
bzw. 5 % (RAM) voneinander ab, ist das System zu unruhig (Abschnitt 9).

```bat
for %f in (jobs-m2\0*.json) do sbench run --job %f --out C:\Abnahme\%~nf.json --progress stdout
sbench export C:\Abnahme\03-ram-bandbreite.json --csv C:\Abnahme\03.csv --style flat
```

Die Kennzahlen stehen im Ergebnis unter `tests[].summary` (`ops_per_s`, `score`, `gbps`,
`latency_ns`) bzw. im CSV in den Spalten `ops_per_s`, `gbps`, `latency_ns`, `score`
(Zeilen `scope = total`). Einheiten: GB/s = 10^9 Byte/s, ns, Operationen pro Sekunde.

### 5.1 Intel MLC (RAM, nur x64/Intel)

Administrator-Konsole. MLC ohne `-L` starten (keine Large Pages), damit beide Programme mit
4-KiB-Seiten messen. MLC meldet **MB/sec mit 1 MB = 10^6 Byte**, also GB/s = MB/sec ÷ 1000.

| sbench | MLC-Befehl | MLC-Wert |
|---|---|---|
| 05 Latenz 1 GiB | `mlc --idle_latency -b1g` | „Each iteration took … ns“ |
| 05 Latenz 16 KiB / 256 KiB / 8 MiB | `mlc --idle_latency -b16` bzw. `-b256` bzw. `-b8m` | dto. |
| 03 read alle Threads | `mlc --max_bandwidth -b256m` | `ALL Reads` |
| 03 copy (NT) alle Threads | dto. | `1:1 Reads-Writes` (zählt Lese- und Schreibbytes, wie sbench `copy`) |
| 06 Latenz 0 → 0 / 0 → 1 | `mlc --latency_matrix -b1g` | Zeile 0, Spalten 0 und 1 |
| 06 read 0 → 0 / 0 → 1 | `mlc --bandwidth_matrix -b256m` | Zeile 0, Spalten 0 und 1 |

MLC schaltet als Administrator die Hardware-Prefetcher ab, sonst nutzt es zufällige
Zugriffe (Meldung „enabling random access“). sbench greift immer zufällig zu.

### 5.2 AIDA64 (RAM, optional)

*Tools → Cache & Memory Benchmark → Start Benchmark* (alle Threads). Werte für
**Memory Read / Write / Copy** (MB/s) und **Latency** (ns) notieren, ebenso L1/L2/L3-Latenz.
Vergleich: Read ↔ 03 `read`, Write ↔ 03 `write NT` (AIDA64 schreibt mit Streaming
Stores), Copy ↔ 03 `copy NT` (zählt wie sbench beide Richtungen), Latency ↔ 05 `1 GiB`,
L1/L2/L3 ↔ 05 `16 KiB`/`256 KiB`/`8 MiB`. AIDA64 rechnet MB/s dezimal; GB/s = MB/s ÷ 1000.

### 5.3 `winsat mem` (RAM, Windows 10/11)

```bat
winsat mem -v > C:\Abnahme\winsat-mem.txt
```

WinSAT misst einen Kopiervorgang und meldet `Memory Performance … MB/s`. Microsoft
dokumentiert weder Zählweise (einfach oder Lesen + Schreiben) noch MB-Definition. Deshalb
nur Plausibilität: Der WinSAT-Wert sollte zwischen der Hälfte und dem Ganzen von sbench
03 `copy` (in MB/s, also GB/s × 1000) liegen. Kein Abnahmekriterium; Wert notieren.

### 5.4 7-Zip (CPU)

```bat
7z b -mmt1 > C:\Abnahme\7z-1t.txt
7z b > C:\Abnahme\7z-alle.txt
```

Wert: Zeile `Tot:` die letzte Zahl (Gesamt-MIPS) bzw. `Avr:` für Komprimieren/Entpacken.
7-Zip ist ganzzahl- und cachelastig. Absolute Zahlen sind nicht vergleichbar, verglichen
wird die **Skalierung**: `7z alle ÷ 7z 1T` gegen `sbench int alle ÷ int 1T` (Job 02 ÷ 01).

### 5.5 Cinebench 2024 (CPU, optional)

*CPU (Multi Core)* und *CPU (Single Core)* je einmal. Vergleich der Skalierung
`Multi ÷ Single` mit `sbench mixed alle ÷ mixed 1T` bzw. `float alle ÷ float 1T`.

## 6. NUMA: lokal und remote (nur Systeme mit ≥ 2 Knoten)

Job 06 bindet alle Threads an Knoten 0 und legt die Puffer einmal auf Knoten 0 (lokal)
und einmal auf Knoten 1 (remote) an. Erwartet:

- `latency_ns` remote deutlich höher als lokal (typisch Faktor 1,5–2),
- `gbps` remote niedriger als lokal,
- `summary.cpus` enthält nur Prozessoren aus `meta.numa_nodes[0].cpus`,
- `per_worker[].mem_node` = 0 bzw. 1.

Vergleich mit MLC `--latency_matrix` / `--bandwidth_matrix` (5.1): Verhältnis remote ÷
lokal. Systeme mit mehr als 64 logischen Prozessoren haben mehrere Prozessorgruppen; dann
zusätzlich prüfen, dass Job 02 in `summary.cpus` Indizes ≥ 64 enthält und
`summary.threads` = `meta.cpu.threads` ist (alle Gruppen genutzt).

## 7. Auswertung und Toleranz

| Kennzahl | Vergleich | Toleranz |
|---|---|---|
| RAM read, alle Threads (03) | MLC `ALL Reads`, AIDA64 Read | **± 10 %** |
| RAM copy NT, alle Threads (03) | MLC `1:1 Reads-Writes`, AIDA64 Copy | **± 15 %** |
| RAM write NT, alle Threads (03) | AIDA64 Write | ± 15 % |
| Latenz 1 GiB (05) | MLC `--idle_latency -b1g` | sbench **0 bis + 25 %** über MLC (sbench zählt TLB-Fehlzugriffe mit) |
| Latenz 16 KiB (05) | MLC `-b16`, AIDA64 L1 | ± 20 % oder ± 0,5 ns |
| Latenz remote ÷ lokal (06) | MLC `--latency_matrix` | ± 15 % |
| Bandbreite remote ÷ lokal (06) | MLC `--bandwidth_matrix` | ± 20 % |
| CPU-Skalierung int (02 ÷ 01) | 7-Zip alle ÷ 1T | ± 20 % |
| CPU-Skalierung mixed/float (02 ÷ 01) | Cinebench Multi ÷ Single | ± 20 % |
| sbench Lauf zu Lauf | – | CPU ≤ 3 %, RAM ≤ 5 % |
| `ram_bytes` je Knoten (3.) | DIMMs je Sockel bzw. `Get-VMHostNumaNode` | ≤ 3 % |

Abweichung in Prozent: `(sbench − Vergleich) / Vergleich × 100`. CPU-Absolutwerte (ops/s,
Score) werden nicht gegen andere Programme geprüft; sie dienen dem Vergleich zwischen
Maschinen mit sbench. Score 1000 ≈ ein Thread des Referenz-Runners.

## 8. Ergebnis-Tabelle (Vorlage)

| Prüfung | sbench | Vergleich (Tool) | Abweichung | ok? |
|---|---|---|---|---|
| NUMA-Knoten / `ram_method` | | | | |
| `ram_bytes` Knoten 0 / 1 | | | | |
| 03 read alle (GB/s) | | MLC / AIDA64 | | |
| 03 copy NT alle (GB/s) | | MLC / AIDA64 | | |
| 03 write NT alle (GB/s) | | AIDA64 | | |
| 05 Latenz 16 KiB / 1 GiB (ns) | | MLC / AIDA64 | | |
| 06 Latenz lokal / remote (ns) | | MLC | | |
| 06 read lokal / remote (GB/s) | | MLC | | |
| 01/02 int Skalierung | | 7-Zip | | |
| 01/02 mixed Skalierung | | Cinebench | | |
| `winsat mem` (Plausibilität) | | WinSAT | | |

## 9. Häufige Ursachen für Abweichungen

- **Turbo/Energiesparen:** Ein-Thread-Werte schwanken mit dem Takt. Energieplan
  „Höchstleistung“, Laptop am Netzteil, zwischen den Läufen 30 s Pause.
- **Zu kleiner Puffer:** Liegt `threads` × `buffer_size` im L3-Cache, misst man den Cache.
  Gesamtgröße mindestens 4 × L3.
- **SMT:** `threads: 0` nutzt auch SMT-Geschwister. MLC `--max_bandwidth` tut das ebenfalls;
  andere Tools nicht immer.
- **VM:** In einer VM hängen Ergebnisse von vNUMA und Host-Last ab; Abnahme auf dem
  physischen System (Host) machen, VM nur ergänzend.
- **ARM64:** Kein MLC und kein AIDA64; nur 7-Zip, Cinebench (falls verfügbar) und
  Lauf-zu-Lauf-Stabilität prüfen. `nontemporal` wirkt dort nicht
  (`W_NONTEMPORAL_UNSUPPORTED`).

## 10. Iometer-ICF importieren und mit Iometer vergleichen

`sbench import-icf` übernimmt eine Iometer-Konfiguration (`.icf`) als Job. Geprüft wird,
dass der Job dasselbe misst wie Iometer. Details zur Abbildung und zu den Warnungen:
[cli.md](cli.md#sbench-import-icf-meilenstein-2). Nur x64 mit Desktop-Oberfläche
(Iometer 1.1.0, siehe [abnahme-m1.md](abnahme-m1.md), Abschnitt 1); Rechner und Laufwerk
`D:` wie in Meilenstein 1.

**10.1 Konfiguration in Iometer anlegen** (Iometer als Administrator)

1. Entweder eigene Einstellungen setzen oder `tests\fixtures\icf\standard-profiles.icf`
   aus dem Repository öffnen (*File → Open Config*; enthält die Standardprofile File Server,
   Web Server, Workstation und Database). Fragt Iometer beim Öffnen, welche Teile übernommen
   werden, die Manager-Zuordnung abwählen und Worker/Targets von Hand setzen.
2. Einstellungen: ein Disk-Worker, Target `D:`, *Maximum Disk Size* `16777216` Sektoren
   (8 GiB), *# of Outstanding I/Os* `32`, *Ramp Up Time* 10 s, *Run Time* 1 min,
   *Test Type* „Normal“ (kein Cycling). Den Access Specs des Workers die gewünschten
   Profile in der gewünschten Reihenfolge zuweisen. Für identische Offsets in der Access
   Specification *Align I/Os on: Request Size Boundaries* wählen; sonst meldet der Import
   `W_ICF_ALIGN`.
3. Konfiguration speichern (*Save Test Configuration*) nach `C:\Abnahme\icf\profil.icf`.
4. Iometer laufen lassen und das Ergebnis speichern (`C:\Abnahme\icf\iometer-profil.csv`).

**10.2 Importieren und mit sbench messen** (normale Konsole)

```bat
cd C:\Tools\sbench
sbench import-icf C:\Abnahme\icf\profil.icf -o C:\Abnahme\icf\profil.json
sbench validate --job C:\Abnahme\icf\profil.json
sbench run --job C:\Abnahme\icf\profil.json --out C:\Abnahme\icf\result-profil.json
sbench export C:\Abnahme\icf\result-profil.json --csv C:\Abnahme\icf\sbench-profil.csv --style iometer
```

- `import-icf` gibt einen Bericht `sb-validate/1` aus. Erwartet: `"ok": true`, Exit 0, und
  bei den Einstellungen aus 10.1 keine Warnungen außer ggf. `W_ICF_ALIGN` und
  `W_LOW_DISK_SPACE`/`W_NEEDS_ADMIN`. Jede Warnung notieren.
- In `profil.json` prüfen: ein Test je zugewiesener Access Specification, Gewichte =
  „% of size“, `block_size`, `read_pct`, `random_pct` wie in Iometer, `queue_depth` 32,
  `warmup_s` 10, `duration_s` 60, Target `D:\iobw.tst` mit 8 GiB.
- sbench nutzt dieselbe Testdatei `D:\iobw.tst` wie Iometer (gleiche Lage auf dem Datenträger).
  Iometer und sbench nacheinander laufen lassen, nie gleichzeitig.
- Kaputte Datei prüfen: `sbench import-icf tests\fixtures\icf\broken-weights.icf --json`
  → Exit 2, `E_ICF_SPEC`.

**10.3 Vergleich**

Pro Profil (Test) die Zeile „ALL“ aus der Iometer-CSV mit dem Test in
`sbench-profil.csv` vergleichen:

| Kennzahl | Toleranz |
|---|---|
| IOPS, Profile mit einer Zeile (Workstation, Database) | **± 5 %** |
| IOPS, gemischte Profile (File Server, Web Server) | **± 10 %** (unterschiedliche Zufallsfolgen bei sehr ungleichen Blockgrößen) |
| MB/s (Iometer „MBps (Decimal)“) | wie IOPS |
| mittlere Latenz | ± 10 % (Richtwert) |
| Lese-/Schreibanteil der I/Os | ± 2 Prozentpunkte |

Weicht ein Wert ab, zuerst die Warnungen des Imports prüfen (z. B. `W_ICF_ALIGN`,
`W_ICF_DATA_PATTERN`, `W_ICF_CYCLING`) und den Lauf beider Tools einmal wiederholen.

| Profil | Iometer IOPS | sbench IOPS | Abw. | Iometer MB/s | sbench MB/s | Abw. | Warnungen | ok? |
|---|---|---|---|---|---|---|---|---|
| File Server | | | | | | | | |
| Web Server | | | | | | | | |
| Workstation | | | | | | | | |
| Database | | | | | | | | |

Danach `D:\iobw.tst` löschen. Der Ordner `C:\Abnahme\icf` liegt in `C:\Abnahme` und kommt mit
`--include C:\Abnahme` ins Bundle (Abschnitt 11).

## 11. Bundle zurückschicken

```bat
sbench bundle --all --include C:\Abnahme --out C:\Abnahme\abnahme-m2.zip
```

Das ZIP enthält `info.json`, alle Ergebnisse, Logs und den Ordner `C:\Abnahme` (MLC-,
WinSAT-, 7-Zip-Ausgaben, deine Tabelle). Mit `--anonymize` werden Hostname, Benutzer und
Adressen ersetzt.
