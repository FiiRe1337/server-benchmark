# CLI-Vertrag `sbench.exe`

Dieses Dokument beschreibt die Schnittstelle zwischen dem C-Messkern (`sbench.exe`) und
der PySide6-GUI. Die GUI startet `sbench.exe` ausschließlich als Unterprozess und
kommuniziert nur über Kommandozeilenargumente, Dateien, JSON-Lines-Events, Exit-Codes
und ein benanntes Stop-Event. Alle Datenformate sind als JSON Schema (draft 2020-12)
in [`schema/`](../schema/) definiert, Beispiele liegen in [`schema/examples/`](../schema/examples/).

| Format | Schema | Beispiel |
|---|---|---|
| Job-Datei | [`sb-job-1.schema.json`](../schema/sb-job-1.schema.json) | [`job-storage-mix.json`](../schema/examples/job-storage-mix.json), [`job-full.json`](../schema/examples/job-full.json), [`job-compute.json`](../schema/examples/job-compute.json) |
| Ergebnis | [`sb-result-1.schema.json`](../schema/sb-result-1.schema.json) | [`result-storage.json`](../schema/examples/result-storage.json), [`result-compute.json`](../schema/examples/result-compute.json) |
| Fortschritt (JSON Lines) | [`sb-progress-1.schema.json`](../schema/sb-progress-1.schema.json) | [`progress-storage.jsonl`](../schema/examples/progress-storage.jsonl), [`progress-compute.jsonl`](../schema/examples/progress-compute.jsonl) |
| Systeminfo | [`sb-info-1.schema.json`](../schema/sb-info-1.schema.json) | [`info.json`](../schema/examples/info.json) |
| Validierung | [`sb-validate-1.schema.json`](../schema/sb-validate-1.schema.json) | [`validate-error.json`](../schema/examples/validate-error.json), [`validate-warnings.json`](../schema/examples/validate-warnings.json) |
| Laufende Läufe | [`sb-runs-1.schema.json`](../schema/sb-runs-1.schema.json) | [`runs.json`](../schema/examples/runs.json) |
| Diagnose-Bundle (`manifest.json`) | [`sb-bundle-1.schema.json`](../schema/sb-bundle-1.schema.json) | [`bundle-manifest.json`](../schema/examples/bundle-manifest.json) |

**Vertragsstand 1.1.** Gegenüber 1.0 kamen nur optionale Felder, das Event `progress`,
das Format `sb-runs/1` und neue Befehle hinzu. Die Formatversionen (`sb-job/1` usw.)
bleiben daher unverändert. sbench ab Vertrag 1.1 gibt die neuen Felder `run_id`, `meta.elevated`,
`meta.data_dir`, `meta.ram_free_bytes`, `meta.numa_nodes` und `warnings` immer aus.
`numa_nodes[].cpus` ist eine Liste globaler logischer Prozessor-Indizes
(Prozessorgruppe × 64 + Bitnummer); `sb-runs/1` nennt je Lauf zusätzlich `job_name`.

**Vertragsstand 1.2 (Meilenstein 2).** Die Testtypen `cpu` und `memory` sind implementiert
(Abschnitt [Testtypen `cpu` und `memory`](#testtypen-cpu-und-memory-vertrag-12)). Neu sind
nur optionale Felder: im Job `warmup_s`, `numa_node`, `mem_node`, `nontemporal`, `simd`; im
Ergebnis weitere `summary`-, `per_worker`- und `timeseries`-Felder sowie
`meta.numa_nodes[].ram_method` (Issue #8); im Tick `latency_ns`. Die Formatversionen bleiben
unverändert. `net_client`, `net_server` und `hyperv_info` melden weiterhin `E_NOT_IMPLEMENTED`.

**Vertragsstand 1.3.** Nur zusätzliche, optionale Felder; Formatversionen unverändert
(Abschnitt [Vertrag 1.3](#vertrag-13-metric-threadscpus-teilergebnisse-score)). sbench ab 1.3
gibt sie immer aus: `tick.metric`, `tick.score` (cpu), im ersten `phase`-Event eines Tests
`threads` und (cpu/memory) `cpus` mit SMT-Geschwistern, je Test `partial` und `stopped_in`,
`meta.cpu_score` (Score-Formel und Referenzwerte, auch in `sb-info`), `summary.score_reference`
(cpu), `summary.proc_cpu_pct` (alle Typen), `summary.cpu_pct` (storage),
`timeseries[].proc_cpu_pct`, `timeseries[].score` (cpu), `tick_ms` im Ergebnis und die
CSV-Spalten `partial,stopped_in`. **Einzige Lockerung:** Messwerte der `summary` dürfen `null`
sein, aber nur bei `partial: true` mit `stopped_in` `preparing`/`warmup` (Test vor der
Messphase beendet); vorher stand dort 0.

**Vertragsstand 1.3.1.** Drei zusätzliche, optionale Felder: `tests[].cpus` im Ergebnis
(cpu/memory), gleiches Format wie `phase.cpus`, `errors[].fatal` und `done.exit_code`. Geändert: `aborted`
(Ergebnis und `done`) ist nur noch bei einem Stopp (Exit 3) `true`, bei einem Laufzeitfehler
(Exit 1) `false` (Abschnitt [Vertrag 1.3.1](#vertrag-131-cpus-im-ergebnis-aborted-und-fatal)).
Formatversionen unverändert.

## Allgemeines

- Alle JSON-Ausgaben sind UTF-8 ohne BOM, Zeilenende LF.
- Jedes Dokument trägt im Feld `schema` seine Formatversion (z. B. `"sb-job/1"`).
  Inkompatible Änderungen erhöhen die Nummer (`sb-job/2`); kompatible Erweiterungen
  (neue optionale Felder) nicht. Die GUI muss unbekannte Hauptversionen ablehnen.
- Pfade in JSON sind Windows-Pfade; Backslashes werden in JSON escaped
  (`"\\\\.\\PhysicalDrive1"` entspricht `\\.\PhysicalDrive1`).
- Menschenlesbare Log-Ausgaben gehen immer nach **stderr**. stdout enthält nur die
  jeweils dokumentierte maschinenlesbare Ausgabe.
- Meldungstexte (`msg`) sind deutsch; maßgeblich für Programmlogik ist nur `code`.

### Einheiten

| Feld | Einheit |
|---|---|
| Größen im Job (`file_size`, `block_size`, `align`, `buffer_size`, `packet_size`) | Integer in Bytes oder String mit `KiB`/`MiB`/`GiB` (Basis 1024), z. B. `"4KiB"`, `"16 GiB"` |
| `*_bytes` | Bytes |
| `iops` | I/Os pro Sekunde |
| `mbps` bei Storage | MB/s dezimal (10^6 Bytes/s, wie Iometer „MBps (Decimal)“) |
| `mbps` bei Netz (`net_client`, `net_server`) | Mbit/s (10^6 Bit/s) |
| `gbps` bei Memory | GB/s dezimal (10^9 Bytes/s) |
| `lat_us` | Mikrosekunden; `latency_ns` Nanosekunden; `rtt_ms`, `jitter_ms` Millisekunden |
| `t`, `*_s` | Sekunden |
| `cpu_pct`, `loss_pct`, `*_pct` | Prozent (0–100) |
| `cpu_pct` (tick, timeseries) | **Systemlast**: alle logischen Prozessoren der Maschine, 0–100 % |
| `proc_cpu_pct` (tick) | CPU-Last **des sbench-Prozesses**, ebenfalls in Prozent der gesamten Maschine (gleiche Skala wie `cpu_pct`; 100 = alle logischen Prozessoren voll, **nicht** ein Kern) |

### Datenverzeichnis (`data_dir`)

sbench legt Laufzeitdateien in einem Datenverzeichnis ab. Es wird in dieser Reihenfolge
bestimmt:

1. globale Option `--data-dir <pfad>` (bei jedem Befehl, vor oder nach dem Befehlsnamen),
2. Umgebungsvariable `SBENCH_DATA_DIR`,
3. `data\` neben `sbench.exe`, wenn beschreibbar (portabler Betrieb),
4. `%ProgramData%\sbench`.

Relative Pfade (Regel 1 und 2) gelten relativ zum Arbeitsverzeichnis. Das Verzeichnis und
die Unterordner `results\`, `jobs\`, `logs\`, `runs\` werden bei Bedarf angelegt. Ist
der `--data-dir`-Pfad nicht anlegbar oder nicht beschreibbar, endet sbench mit Exit-Code
**2** (`E_ARGS` auf stderr).

Erhöhter und nicht erhöhter Prozess müssen dasselbe Verzeichnis nutzen. Ein per
`runas` (ShellExecuteEx) gestarteter Prozess erbt die Umgebung der GUI nicht
zuverlässig. Die GUI übergibt deshalb `--data-dir` ausdrücklich auf der Kommandozeile;
ohne die Option ergibt sich das gemeinsame Verzeichnis über Regel 3 oder 4,
`SBENCH_DATA_DIR` ist für Sonderfälle (Tests, systemweit gesetzt). Die GUI
ermittelt den Pfad über `sbench info --json`. Der tatsächlich verwendete Pfad
steht in `meta.data_dir` (in `sb-result` bezogen auf den Zeitpunkt des Laufs, ebenso
`meta.elevated`).

| Pfad | Inhalt |
|---|---|
| `<data_dir>\runs\<run_id>.jsonl` | Alle Fortschritts-Events des Laufs (immer, unabhängig von `--progress`) |
| `<data_dir>\runs\<run_id>.lock.json` | Lock-Datei `{run_id, job_name, pid, started_utc, job, out}` (Schema: `sb-runs-1.schema.json#/$defs/lock_file`); wird beim Ende des Laufs gelöscht |
| `<data_dir>\runs\index.jsonl` | Eine Zeile pro beendetem Lauf `{run_id, started_utc, ended_utc, out, exit_code}`; Grundlage für `bundle --all` |
| `<data_dir>\logs\<run_id>.log` | Menschenlesbares Log (entspricht der stderr-Ausgabe) |

## Befehle

### `sbench run`

```
sbench run --job <job.json> --out <result.json>
           [--progress stdout | pipe:\\.\pipe\sbench-<id> | file:<pfad.jsonl>]
           [--tick-ms <ms>] [--id <id>] [--allow-destructive] [--keep-files]
```

| Option | Bedeutung |
|---|---|
| `--job <f>` | Job-Datei (`sb-job/1`). Wird vor dem Start vollständig validiert (wie `sbench validate`). |
| `--out <f>` | Ergebnisdatei (`sb-result/1`). Wird zunächst als `<f>.tmp` geschrieben und am Ende atomar umbenannt. Das Feld `run_id` im Ergebnis entspricht dem Log-Namen und dem Eintrag unter `runs\`. |
| `--progress` | Ziel für Fortschritts-Events (`sb-progress/1`, JSON Lines). Ohne Angabe werden keine Events ausgegeben. |
| `--tick-ms <ms>` | Intervall der `tick`-Events in Millisekunden. Standard **1000**, Minimum 100. |
| `--allow-destructive` | Erlaubt Schreibtests (`read_pct` < 100) auf `volume`/`physical`-Targets, die zusätzlich `allow_destructive: true` tragen. Fehlt eines von beiden, endet `run` vor dem Start mit `E_DESTRUCTIVE_NOT_ALLOWED` und Exit-Code **2**. |
| `--keep-files` | Von sbench angelegte Testdateien (`kind: file`) nach dem Test **nicht** löschen, z. B. um große Dateien in weiteren Läufen wiederzuverwenden. Ohne die Option löscht sbench jede Testdatei, die es selbst angelegt hat, nach dem Test – nach normalem Ende, nach `sbench stop` und nach Fehlern. Dateien, die vor dem Lauf schon existierten, bleiben immer unangetastet (auch wenn sbench sie vergrößert hat). Im Ergebnis steht pro Storage-Test `targets[]` mit `created` und `deleted`. |
| `--id <id>` | Lauf-ID (`[A-Za-z0-9_-]{1,64}`), u. a. für `sbench stop`. Ohne Angabe erzeugt sbench eine ID und meldet sie im `start`-Event (`run_id`). |

Progress-Ziele:

- `stdout` – ein Event pro Zeile; sbench ruft nach **jeder** Zeile `fflush` auf, damit
  die GUI ohne Pufferverzögerung liest.
- `pipe:\\.\pipe\sbench-<id>` – die **GUI legt die Named Pipe als Server an**,
  sbench verbindet sich als **Client**. Dadurch funktioniert der Kanal auch, wenn sbench
  per `runas` erhöht gestartet wird und stdout der GUI nicht erreicht (siehe
  [Named Pipe](#named-pipe-fortschrittskanal)).
- `file:<pfad.jsonl>` – Events werden an die Datei angehängt und nach jeder Zeile
  geflusht (für Tests und Fehlersuche).

**Unabhängig vom gewählten Ziel** schreibt sbench jedes Event zusätzlich nach
`<data_dir>\runs\<run_id>.jsonl` (Flush nach jeder Zeile) und legt beim Start
`<data_dir>\runs\<run_id>.lock.json` an, die beim Ende des Laufs gelöscht wird.
So kann sich die GUI nach einem Neustart über `sbench list --running --json` wieder
anhängen.

#### Named Pipe (Fortschrittskanal)

- Die GUI erstellt die Pipe mit `CreateNamedPipe` (Byte-Modus, `PIPE_ACCESS_INBOUND`
  aus Sicht der GUI, eine Instanz) und dem Flag **`PIPE_REJECT_REMOTE_CLIENTS`**.
- Security Descriptor: Zugriff **nur** für die SID des aktuellen Benutzers und
  **`BUILTIN\Administrators`** (z. B. SDDL `D:P(A;;GA;;;<Benutzer-SID>)(A;;GA;;;BA)`).
  Der Administrators-Eintrag ist nötig, weil ein per `runas` erhöhter sbench mit dem
  Admin-Token läuft, dessen Benutzer-SID sich (bei anderem Admin-Konto) unterscheiden kann.
- sbench wartet mit `WaitNamedPipe` (Timeout **10 s**) und öffnet die Pipe dann mit
  `CreateFile` zum Schreiben. Gelingt das nicht, endet sbench mit Exit-Code **1** und
  dem Fehler `E_PIPE_CONNECT` (Event in `<data_dir>\runs\<run_id>.jsonl` und auf stderr).
- Bricht die Pipe während des Laufs ab (z. B. GUI beendet), **läuft sbench weiter**,
  schreibt Log, Fortschrittsdatei und Ergebnis normal und protokolliert eine Warnung
  im Log. Der Exit-Code ändert sich dadurch nicht.

Ablauf der Events: `start` → pro Test `phase` (`preparing`, `warmup`, `measure`,
`cleanup`; optional mit `duration_s` = geplante Dauer, falls bekannt) mit
`progress`-Events etwa einmal pro Sekunde während `preparing` (z. B. Anlegen der
Testdatei, `unit: "bytes"`) und `warmup` (`unit: "s"`) sowie `tick`-Events **nur während `measure`** (`t` = Sekunden seit Beginn der
Messphase) → `done` (immer das letzte Event, auch bei Abbruch oder Laufzeitfehler,
sofern ein Ergebnis geschrieben wurde). `error`-Events können jederzeit auftreten.
Phasen ohne Bedeutung für einen Testtyp (z. B. `warmup` bei `hyperv_info`) dürfen
entfallen. Storage-Ticks enthalten immer `iops`, `mbps`, `lat_us`, `cpu_pct`;
optional sind `read_iops`, `write_iops`, `read_mbps`, `write_mbps`, `proc_cpu_pct` und
`workers` (je Worker `id`, `iops`, `mbps`, optional `lat_us{mean,p99}`). CPU-Ticks nutzen `ops_per_s`, Memory-Ticks `gbps`,
Netz-Ticks `mbps`. Ab Vertrag 1.3 nennt `metric` in jedem Tick den Hauptwert (`iops` |
`ops_per_s` | `gbps` | `latency_ns` | `mbps`), und das erste `phase`-Event eines Tests
(`preparing`) trägt `threads` und bei cpu/memory `cpus` (siehe [Vertrag 1.3](#vertrag-13-metric-threadscpus-teilergebnisse-score)).

Ausführung: Tests laufen in der Reihenfolge des Jobs nacheinander. Bei `storage`
werden die Worker reihum auf die Targets verteilt (Worker *i* → Target *i* mod Anzahl
Targets); `queue_depth` gilt pro Worker. Die `access_specs` werden pro I/O gemäß
`weight` (Anteil an der Zahl der I/Os, Summe 100) gewählt.

### `sbench stop`

```
sbench stop --id <id>
```

Setzt das benannte Event `Global\sbench-stop-<id>`. `sbench run` legt dieses Event beim
Start an und prüft es laufend. Kann `run` kein `Global\`-Objekt anlegen (fehlendes
`SeCreateGlobalPrivilege` ohne Erhöhung), wird ersatzweise `Local\sbench-stop-<id>`
verwendet; `stop` versucht beide Namen.

Nach dem Stop beendet `run` den laufenden Test sofort, schreibt das **Teilergebnis**
trotzdem nach `--out` (mit `aborted: true`, bereits abgeschlossene Tests vollständig,
der laufende Test mit den bis dahin gemessenen Werten, nicht begonnene Tests fehlen),
sendet `done` mit `aborted: true` und endet mit Exit-Code **3**. Ab Vertrag 1.3 trägt der
laufende Test `partial: true` und `stopped_in`; bei einem Stopp in `preparing` oder `warmup`
sind seine Messwerte `null` (siehe [Teilergebnisse](#teilergebnisse-partial-stopped_in)).

`sbench stop` selbst endet mit 0, wenn das Event gesetzt wurde, und mit 1, wenn kein
Lauf mit dieser ID existiert.

### `sbench list --running --json`

Gibt die laufenden Läufe als `sb-runs/1` auf stdout aus:
`{"schema": "sb-runs/1", "runs": [{run_id, job_name, pid, started_utc, out, progress_file}]}`.
`job_name` ist `name` aus der Job-Datei, sonst deren Dateiname ohne Endung.
Grundlage sind die Lock-Dateien in `<data_dir>\runs\`. Ein Eintrag gilt nur als
laufend, wenn der Prozess `pid` existiert und seine Startzeit zu `started_utc` passt
(Schutz gegen wiederverwendete PIDs). Verwaiste Lock-Dateien werden ignoriert und
entfernt.

Nach einem Neustart ruft die GUI `list` auf, liest `progress_file` von Anfang an
(bisherige Events) und folgt der Datei dann weiter („tail“), bis das Event `done`
erscheint. Stoppen ist wie gewohnt über `sbench stop --id <run_id>` möglich.

### `sbench info --json`

Gibt Systeminformationen und mögliche Testziele (Volumes, physische Disks,
Netzwerkadapter) als `sb-info/1` auf stdout aus. Ohne Administratorrechte können
Angaben zu physischen Disks unvollständig sein (z. B. fehlendes Modell).

### `sbench validate --job <f>`

Prüft eine Job-Datei gegen `sb-job/1` **und** die semantischen Regeln (siehe unten),
gibt `sb-validate/1` auf stdout aus und endet mit **0** (gültig) oder **2** (ungültig).
`path` ist ein JSON Pointer (RFC 6901), z. B. `/tests/0/access_specs/1/block_size`.

Zusätzlich zu `errors` enthält die Ausgabe `warnings` (gleicher Aufbau). Warnungen
ändern `ok` nicht und führen nicht zu Exit-Code 2:

| Code | Bedeutung |
|---|---|
| `W_NEEDS_ADMIN` | Der Job braucht Administratorrechte, die gerade fehlen. Bei Raw-Zugriff (`volume`/`physical`) würde `run` mit Exit-Code **4** enden; bei `SetFileValidData` (Anlegen von Testdateien) gibt es nur einen langsameren Fallback. |
| `W_DESTRUCTIVE` | Schreibtest auf `volume`/`physical` mit `allow_destructive: true`: die Daten auf dem Ziel werden zerstört; `run` braucht zusätzlich `--allow-destructive`. |
| `W_LOW_DISK_SPACE` | Wenig freier Platz auf dem Volume einer Testdatei (vor oder nach dem Anlegen). |
| `W_LOW_MEMORY` | `memory`: Die Puffer aller Threads (`threads` × `buffer_size`) belegen mehr als 50 % des freien Arbeitsspeichers (bzw. des Knotens `mem_node`). |
| `W_OVERSUBSCRIBED` | `cpu`/`memory`: `threads` ist größer als die Zahl der logischen Prozessoren (bzw. des Knotens `numa_node`); Prozessoren werden mehrfach belegt. |
| `W_THREADS_IGNORED` | `memory` mit `mode: latency` und `threads` > 1: Latenz wird immer mit einem Thread gemessen. |
| `W_NONTEMPORAL_IGNORED` | `nontemporal: true` bei `op: read`: wirkt nur bei `write` und `copy`. |
| `W_NONTEMPORAL_UNSUPPORTED` | `nontemporal: true` auf ARM64: dort gibt es keine umgesetzten Streaming Stores; es werden normale Stores genutzt (`summary.nontemporal: false`). |

Semantische Regeln (über das JSON Schema hinaus):

| Code | Regel |
|---|---|
| `E_SCHEMA` | Verstoß gegen das JSON Schema (Typ, Pflichtfeld, Wertebereich, unbekanntes Feld). |
| `E_WEIGHT_SUM` | Summe der `weight`-Werte der `access_specs` eines Tests ist nicht 100. |
| `E_DESTRUCTIVE_NOT_ALLOWED` | Schreibtest (`read_pct` < 100 in mindestens einer Access-Spec) auf ein `volume`/`physical`-Target ohne `allow_destructive: true`. `run` meldet denselben Code (Exit 2) auch, wenn das Target die Freigabe hat, aber `--allow-destructive` fehlt. |
| `E_BLOCK_ALIGN` | Bei `cache: unbuffered` sind `block_size`/`align` kein Vielfaches der logischen Sektorgröße des Ziels. |
| `E_TARGET_NOT_FOUND` | Datei-Verzeichnis, Volume oder physische Disk existiert nicht. |
| `E_TARGET_SIZE` | `file_size` bzw. Zielgröße kleiner als `block_size`. |
| `E_UDP_RATE` | `udp_rate_mbps` bei `proto: tcp` angegeben bzw. bei UDP-Durchsatztest fehlend. |
| `E_NUMA_NODE` | `numa_node` nennt keinen Knoten mit logischen Prozessoren bzw. `mem_node` keinen vorhandenen Knoten (`meta.numa_nodes[].id`). |
| `E_SIMD_UNSUPPORTED` | `simd` wird von CPU oder Betriebssystem nicht unterstützt (z. B. `avx512` ohne AVX-512F, `neon` auf x64, `avx2` auf ARM64). |
| `E_BUFFER_SIZE` | `buffer_size` kleiner als 8 KiB. |
| `E_OUT_OF_MEMORY` | `threads` × `buffer_size` ist größer als 90 % des freien Arbeitsspeichers (bzw. des Knotens `mem_node`). Zur Laufzeit derselbe Code, wenn `VirtualAllocExNuma` scheitert. |

### `sbench export`

```
sbench export <result.json> --csv <out.csv> [--style iometer|flat]
```

- `flat` (Standard): eine Zeile pro Test bzw. pro Worker/Access-Spec mit Spalten
  `test,type,scope,id,iops,mbps,read_iops,write_iops,read_mbps,write_mbps,lat_mean_us,lat_p99_us,lat_max_us,errors,ops_per_s,gbps,latency_ns,score`;
  Trennzeichen Komma, Dezimalpunkt, UTF-8. Die Spalten `ops_per_s` (cpu), `gbps` und
  `latency_ns` (memory) sowie `score` (cpu) sind seit Vertrag 1.2 hinten angehängt; die
  Positionen der älteren Spalten bleiben gleich. Bei `cpu`/`memory` gibt es Zeilen
  `scope = total` und `scope = worker` (je Thread). Ab Vertrag 1.3 folgen `partial`
  (`true`/`false`, auf allen Zeilen eines Tests) und `stopped_in`.
  **Leeres Feld** heißt „kein Wert“: die Spalte gilt nicht für Testtyp bzw. `scope` (z. B.
  `errors` und `score` auf cpu/memory-Worker-Zeilen, `score` bei storage, `read_mbps`/`write_mbps`
  auf Worker-/Spec-Zeilen) oder der Messwert ist `null` (Teilergebnis). `0` ist immer ein
  gemessener Wert. `scope` ist endgültig: `total` (Summe des Tests, bei allen Testtypen),
  `worker` (je Worker/Thread), `spec` (nur storage, je Access-Spec).
- `iometer`: an das Iometer-Ergebnis-CSV angelehnte Spaltennamen
  (`Target Type`, `Target Name`, `Access Specification Name`, `IOps`, `MBps (Decimal)`,
  `Average Response Time`, …) zur Weiterverarbeitung in vorhandenen Auswertungen.
  Nur Storage-Tests werden exportiert (gewollt: das Iometer-Format hat keine Spalten für
  ops/s, GB/s oder ns; cpu/memory stehen im `flat`-Export).

### `sbench bundle`

```
sbench bundle --out <bundle.zip> [--all] [--results <result.json> ...]
              [--include <ordner>]... [--anonymize]
```

Erstellt ein Diagnose-/Austausch-ZIP. `manifest.json` folgt `schema/sb-bundle-1.schema.json`
(Dateiliste mit Größe und SHA-256, `anonymized`, `includes`); Beispiel:
`schema/examples/bundle-manifest.json`. Zusätzlich zu `results/`, `runs/` und `logs/`
enthält das Bundle je Lauf die Job-Kopie unter `jobs/<run_id>.json`. Optional
`--progress` wie bei `run` (Events `start`, `phase`, `progress` in Bytes, `done`).

| Option | Bedeutung |
|---|---|
| `--out <zip>` | Zieldatei. |
| `--all` | Alle beendeten Läufe aus `<data_dir>\runs\index.jsonl`: Ergebnisdateien (soweit noch vorhanden), Fortschrittsdateien und Logs. |
| `--results <f> ...` | Nur diese Ergebnisdateien, jeweils mit zugehöriger Fortschrittsdatei und Log (Zuordnung über das Feld `run_id` im Ergebnis). |
| `--include <ordner>` | Mehrfach angebbar. Nimmt einen fremden Ordner rekursiv unter `extra/<ordnername>/` auf, z. B. GUI-Logs und Profile. |
| `--anonymize` | Ersetzt in **allen JSON- und Log-Dateien** des Bundles (auch unter `extra/`) Hostnamen, Benutzernamen in Pfaden, IPv4-/IPv6-Adressen, MAC-Adressen und Disk-Seriennummern durch Pseudonyme (`host-1`, `user-1`, `10.0.0.x`, `fd00::x`, `02:00:00:00:00:xx`, `serial-1` …). Die Zuordnung ist innerhalb eines Bundles konsistent (gleicher Wert → gleiches Pseudonym) und wird nicht mitgespeichert. Andere Dateitypen werden unverändert übernommen. |

Ohne `--all`/`--results` enthält das Bundle nur `manifest.json`, eine aktuelle
`info.json` und die `--include`-Ordner. Aufbau: `manifest.json`, `info.json`,
`results/`, `runs/`, `logs/`, `extra/<ordnername>/`. Im Manifest stehen u. a.
`anonymized: true|false` und `includes: [...]` (die aufgenommenen Ordnernamen).
Exit-Codes: 0 ok, 1 Laufzeitfehler, 2 ungültige Argumente.

### `sbench import-icf` (Meilenstein 2)

```
sbench import-icf <datei.icf> [-o|--out <job.json>] [--target <pfad>]... [--json]
```

Wandelt eine Iometer-Konfiguration (`.icf`, Iometer 1.1.0 und 2006.07.27; ANSI oder UTF-8,
CRLF oder LF) in einen Job `sb-job/1` mit `storage`-Tests um.

| Option | Wirkung |
|---|---|
| `-o`, `--out <job.json>` | Job in die Datei schreiben (UTF-8). stdout: `sb-validate/1` (Vertrag der GUI). Die Datei wird nur bei Exit 0 geschrieben. |
| ohne `-o` | Job auf stdout, Fehler und Warnungen als Text (`CODE [pfad]: meldung`) auf stderr. |
| `--json` | Ohne `-o`: nur `sb-validate/1` auf stdout, kein Job (Prüfmodus). Mit `-o` ohne zusätzliche Wirkung. |
| `--target <pfad>` | Mehrfach angebbar. Ersetzt die Targets aus dem ICF in ihrer Reihenfolge; die Anzahl muss gleich sein (sonst `E_ICF_TARGET_COUNT`). Hat das ICF keine Targets, werden die angegebenen übernommen (ein Worker je Target). `kind` wird aus dem Pfad bestimmt: `\\.\PhysicalDriveN` → `physical`, `\\.\X:` → `volume`, sonst `file`. |

**Abbildung**

| Iometer | sb-job/1 |
|---|---|
| Access Specification (einem Worker zugewiesen; Iometer führt sie nacheinander aus) | je ein Test `storage`, `name` = Name der Spec, Reihenfolge wie im ICF. Ohne Zuweisung: alle Specs (`W_ICF_NO_ASSIGNMENT`, wenn es Worker gibt). |
| Zeile `size,% of size,% reads,% random,delay,burst,align,reply` | ein Eintrag in `access_specs`: `block_size` = size, `weight` = % of size (Summe muss 100 sein), `read_pct`, `random_pct`; `delay_ms` und `burst_len` nur bei delay > 0; `align` nur bei align > 0 |
| Test Description | `name` des Jobs (sonst Dateiname ohne Endung) |
| Run Time (h/min/s), Ramp Up Time | `duration_s`, `warmup_s` |
| Outstanding I/Os (pro Worker und Target) | `queue_depth` |
| Disk-Worker × Targets | `workers` = Anzahl der Paare (Worker, Target); sbench verteilt die Worker reihum auf `targets` |
| Target `D: "Label"` | `{"path": "D:\\iobw.tst", "kind": "file"}`, `file_size` = Disk maximum size × 512 Bytes |
| Target `PHYSICALDRIVE:N` | `{"path": "\\\\.\\PhysicalDriveN", "kind": "physical"}` ohne `allow_destructive` |
| – | `cache: "unbuffered"` (wie Iometer) |

**Warnungen** (`ok: true`, Exit 0; `path` = JSON Pointer in den erzeugten Job bzw. `""`,
ICF-Zeilennummern in `msg`):

| Code | Bedeutung |
|---|---|
| `W_ICF_RUN_TIME` | Run Time 0 (unbegrenzt) oder fehlend → `duration_s` = 60 |
| `W_ICF_DISK_SIZE` | Disk maximum size 0 (Iometer füllt das Laufwerk) → Testdatei 8 GiB; bei physischen Disks wird die Größe nicht übernommen (sbench testet die ganze Disk) |
| `W_ICF_ALIGN` | align 0 (Iometer: Sektorgrenzen) → ohne `align`, sbench richtet zufällige Offsets an der `block_size` aus |
| `W_ICF_REPLY` | reply ≠ 0 (nur Netzwerk) wird ignoriert |
| `W_ICF_CYCLING` | Test Type ≠ NORMAL (Worker-/Target-/Queue-Depth-Cycling): ein Durchgang mit den Worker-Einstellungen |
| `W_ICF_QD_MIXED` | Worker mit unterschiedlichen Outstanding I/Os → Wert des ersten Workers |
| `W_ICF_SPEC_MIXED` | Worker mit unterschiedlichen Access Specs → Specs des ersten Workers |
| `W_ICF_WORKER_SPLIT` | Iometer-Worker mit mehreren Targets → ein sbench-Worker je Target |
| `W_ICF_TARGET_LAYOUT` | Targets mit unterschiedlich vielen Workern → reihum verteilt |
| `W_ICF_MANAGERS` | mehrere Manager → nur der erste (sbench misst lokal) |
| `W_ICF_NETWORK_WORKER` | Netzwerk-Worker werden ignoriert (dafür `net_client`/`net_server`) |
| `W_ICF_DATA_PATTERN` | Data pattern ≠ 2 (Full random) bei Schreibanteil: sbench schreibt immer Zufallsdaten |
| `W_ICF_SECTOR` | size/align kein Vielfaches von 512 |
| `W_ICF_ZERO_WEIGHT` | Zeile mit 0 % of size wird weggelassen |
| `W_ICF_NO_TARGET` | kein Disk-Worker mit Target und kein `--target` → Platzhalter `D:\sbtest.dat` (8 GiB) |
| `W_ICF_NO_WORKERS` | keine Manager-Liste → 1 Worker je Target, `queue_depth` 1 |
| `W_ICF_UNSUPPORTED` | sonstige Felder ohne Entsprechung (Starting Sector, fixed seed, connection rate, Burst ohne Delay, unbekannte Abschnitte oder Targets) |
| `W_ICF_VALIDATE` | Der erzeugte Job wurde wie bei `validate` geprüft; umgebungsabhängige Fehler (`E_TARGET_NOT_FOUND`, `E_DESTRUCTIVE_NOT_ALLOWED`, `E_BLOCK_ALIGN`, `E_TARGET_SIZE`) werden nur gewarnt, der ursprüngliche Code steht am Anfang von `msg`. Vor `run` den Job anpassen. |

Warnungen von `validate` (z. B. `W_NEEDS_ADMIN`, `W_LOW_DISK_SPACE`, `W_DESTRUCTIVE`) werden
unverändert durchgereicht.

**Fehler** (`ok: false`, Exit 2, keine Job-Datei): `E_ICF_FORMAT` (keine Iometer-Datei,
fehlende `Version`-Zeile, keine Access Specifications, Datei abgeschnitten), `E_ICF_PARSE`
(Zeile nicht lesbar, z. B. falsche Spaltenzahl), `E_ICF_VALUE` (Wert außerhalb des Bereichs),
`E_ICF_SPEC` (Summe `% of size` ≠ 100, Spec ohne Zeilen, doppelt, unbekannte Spec bei einem
Worker), `E_ICF_TARGET_COUNT` (`--target` passt nicht). Exit 1: ICF-Datei nicht lesbar
(`E_IO`) oder Ausgabedatei nicht schreibbar.

Beispiel:

```
sbench import-icf fileserver.icf -o fileserver.json --target E:\sbench\test.dat
sbench validate --job fileserver.json
sbench run --job fileserver.json
```

### `sbench version`

Gibt auf stdout in der ersten Zeile `sbench <semver>` aus, danach je Zeile eine
unterstützte Formatversion, z. B.:

```
sbench 0.1.0
sb-job/1
sb-result/1
sb-progress/1
sb-info/1
sb-validate/1
sb-runs/1
```

#### `sbench version --json`

Maschinenlesbar, z. B. für die GUI oder einen Release-Workflow:

```json
{
  "version": "0.1.0-beta.1",
  "commit": "5670020c3d…",
  "arch": "x64",
  "sha256": "<SHA-256 von sbench.exe, hexadezimal klein>",
  "formats": ["sb-job/1", "sb-result/1", "sb-progress/1", "sb-info/1", "sb-validate/1", "sb-runs/1", "sb-bundle/1"]
}
```

`version` ist dieselbe SemVer wie in der ersten Zeile von `sbench version`, `commit` der
Git-Commit des Builds (`unknown`, falls beim Bauen nicht bekannt), `arch` `x64` oder
`arm64`, `sha256` der Hash der ausgeführten EXE-Datei. Exit-Code 0; 1, wenn die EXE nicht
lesbar war (dann `sha256: null`).

## Testtypen `cpu` und `memory` (Vertrag 1.2)

Methodik und Definitionen: [`docs/core.md`](core.md#cpu--und-memory-engine-meilenstein-2).
Beispiele: [`job-compute.json`](../schema/examples/job-compute.json),
[`result-compute.json`](../schema/examples/result-compute.json),
[`progress-compute.jsonl`](../schema/examples/progress-compute.jsonl).

### Job-Felder

| Feld | Typ | Standard | Bedeutung |
|---|---|---|---|
| `threads` | Integer ≥ 0 | 0 | Anzahl Threads; 0 = alle logischen Prozessoren (bzw. alle des Knotens `numa_node`). Bei `mode: latency` immer 1. |
| `workload` (cpu) | `int` \| `float` \| `crypto` \| `mixed` | `mixed` | Rechenlast, siehe `docs/core.md`. |
| `mode` (memory) | `bandwidth` \| `latency` | – (Pflicht) | Durchsatz oder Latenz abhängiger Ladezugriffe. |
| `op` (memory) | `read` \| `write` \| `copy` | `read` | Nur bei `bandwidth`. |
| `buffer_size` (memory) | Größe | – (Pflicht) | Puffer **pro Thread**, mindestens 8 KiB. Für Arbeitsspeicher statt Cache deutlich größer als der L3-Cache wählen (256 MiB bis 1 GiB). |
| `warmup_s` | Zahl ≥ 0 | 1 | Aufwärmen vor der Messung (Takt, Caches, TLB). |
| `duration_s` | Zahl > 0 | – (Pflicht) | Messdauer. |
| `numa_node` | Integer | alle Knoten | Nur Prozessoren dieses Knotens nutzen. |
| `mem_node` (memory) | Integer | Knoten des Threads | Puffer auf diesem Knoten anlegen; `numa_node` ≠ `mem_node` misst remote. |
| `nontemporal` (memory) | Boolean | `false` | Streaming Stores für `write`/`copy` (x64). |
| `simd` (memory) | `auto` \| `scalar` \| `sse2` \| `avx2` \| `avx512` \| `neon` | `auto` | Befehlssatz der Bandbreiten-Kernel; `auto` = AVX2, sonst SSE2 (x64) bzw. NEON (ARM64). AVX-512 nur auf ausdrückliche Anforderung. |

Threads werden fest an logische Prozessoren gebunden, auch über Prozessorgruppen hinweg
(Systeme mit mehr als 64 logischen Prozessoren). Reihenfolge der Auswahl: zuerst ein
Thread pro physischem Kern, die NUMA-Knoten abwechselnd, danach die SMT-Geschwister.
`threads: 1` misst also einen Kern auf Knoten 0, `threads: 2` je einen Kern auf zwei
Knoten (falls vorhanden).

### Ergebnis

`summary` (cpu): `workload`, `threads`, `ops_per_s`, `score`, `ops`, `cpus` (gebundene
Prozessoren in Thread-Reihenfolge), `cpu_pct`, optional `numa_node`.

`summary` (memory): `mode`, `op`, `threads`, `buffer_size_bytes` (tatsächliche Größe),
`gbps` + `simd` + `nontemporal` + `bytes` (bandwidth) bzw. `latency_ns` + `loads`
(latency), `cpus`, `cpu_pct`, optional `numa_node`, `mem_node`.

`per_worker[]`: `id`, `cpu`, `node`, bei memory `mem_node`, dazu `ops_per_s`, `gbps` oder
`latency_ns`. `timeseries[]`: `t`, `ops_per_s` | `gbps` | `latency_ns`, `cpu_pct`.

Einheiten: `gbps` = 10^9 Byte/s, Summe aller Threads; bei `copy` zählen gelesene **und**
geschriebene Bytes (wie STREAM). `latency_ns` = mittlere Zeit eines abhängigen Ladezugriffs.
`score` = `ops_per_s` ÷ Referenz-Rate eines Threads × 1000 (Referenzen in `docs/core.md`).

### Fortschritt

- `preparing` (nur memory): `progress` mit `unit: "bytes"` beim Anlegen und
  Beschreiben der Puffer (Summe aller Threads).
- `warmup`: `progress` mit `unit: "s"`.
- `measure`: `tick` mit `ops_per_s` (cpu), `gbps` (bandwidth) oder `latency_ns`
  (latency), dazu `cpu_pct`, `proc_cpu_pct` und – außer bei latency – `workers[]`
  mit `id` und dem Wert pro Thread. Ab 1.3 zusätzlich `metric` und bei cpu `score`.
- Stop (`sbench stop`, Strg+C) beendet die Messung innerhalb von ≤ 250 ms; das
  Teilergebnis enthält die bis dahin gemessenen Werte (ab 1.3 mit `partial: true`).

## Vertrag 1.3: metric, threads/cpus, Teilergebnisse, Score

Alle Felder sind optional im Schema und werden von sbench ab 1.3 immer ausgegeben. Beispiele:
[`progress-compute.jsonl`](../schema/examples/progress-compute.jsonl),
[`result-compute.json`](../schema/examples/result-compute.json),
[`progress-partial.jsonl`](../schema/examples/progress-partial.jsonl),
[`result-partial.json`](../schema/examples/result-partial.json).

### Ticks

| Feld | Bedeutung |
|---|---|
| `metric` | Name des Hauptwerts in diesem Tick: `iops` (storage), `ops_per_s` (cpu), `gbps` (memory bandwidth), `latency_ns` (memory latency), `mbps` (net, später). Das gleichnamige Feld ist immer vorhanden. |
| `score` | nur cpu: Score des Intervalls = `ops_per_s` ÷ `meta.cpu_score.ref_ops_per_s[workload]` × 1000. |
| `workers[]` | Jeder Worker/Thread genau einmal, aufsteigend nach `id` (0 … n−1, wie `per_worker[].id` und `phase.cpus[].id`). Werte sind **Raten im Intervall** (IOPS, MB/s, ops/s, GB/s), keine Summen. Storage: Hat ein Worker den Intervallwechsel noch nicht quittiert (≤ 250 ms Wartezeit), zählt sein Intervall zum nächsten Tick; er steht dann mit 0 drin. `lat_us` fehlt bei Workern ohne abgeschlossene I/O im Intervall. memory latency hat kein `workers[]` (immer genau ein Thread, Wert = Gesamtwert). |

### phase-Event beim Teststart

Das erste `phase`-Event eines Tests (`preparing`) trägt `threads`: bei cpu/memory die
tatsächliche Thread-Anzahl (auch bei `threads: 0` im Job schon aufgelöst; latency immer 1),
bei storage die Anzahl Worker. cpu/memory zusätzlich `cpus[]`, ein Eintrag je Thread in
Thread-Reihenfolge:

| Feld | Bedeutung |
|---|---|
| `id` | Thread-Index (= `workers[].id`) |
| `cpu` | globaler Index = `group` × 64 + `number` (wie `meta.numa_nodes[].cpus`, `summary.cpus`) |
| `group`, `number` | Prozessorgruppe und Nummer in der Gruppe (`PROCESSOR_NUMBER`) |
| `numa_node` | NUMA-Knoten des Prozessors |
| `core` | laufende Nummer des physischen Kerns innerhalb des Laufs; gleicher Wert = SMT-Geschwister |
| `smt_sibling_of` | globaler Index des ersten logischen Prozessors desselben Kerns, `null` für diesen selbst (bzw. ohne SMT) |

Bei mehr Threads als Prozessoren (`W_OVERSUBSCRIBED`) wiederholen sich `cpu`-Werte.
Storage-Worker werden nicht gebunden; dort gibt es kein `cpus`.

### Teilergebnisse (`partial`, `stopped_in`)

Jeder Test im Ergebnis trägt `partial` (`false` = vollständig gelaufen). `partial: true`
heißt: der Test endete vorzeitig durch `sbench stop`/Strg+C oder einen Laufzeitfehler (Ursache
in `errors[]`); `stopped_in` nennt die Phase (`preparing`, `warmup`, `measure`, Namen wie im
`phase`-Event).

| Ende in | Test im Ergebnis | Messwerte |
|---|---|---|
| vor dem Test (Stop zwischen zwei Tests) | fehlt | – |
| `preparing`, `warmup` | steht drin, `partial: true` | alle Messwerte der `summary` `null` (nicht 0): storage `iops`, `mbps`, `read_*`, `write_*`, `lat_us`, `errors`, `io_count`, `cpu_pct`, `proc_cpu_pct`; cpu `ops_per_s`, `score`, `ops`, `cpu_pct`, `proc_cpu_pct`; memory `gbps`/`latency_ns`, `bytes`/`loads`, `cpu_pct`, `proc_cpu_pct`. `per_worker`, `per_spec`, `histogram`, `timeseries` sind leer. Konfiguration (`threads`, `cpus`, `buffer_size_bytes`, `simd`, `targets[]`) bleibt gesetzt. |
| `measure` | steht drin, `partial: true` | Werte über die tatsächlich gemessene Zeit (Raten bezogen auf diese Zeit) |
| – | `partial: false` | vollständig |

Nach dem abgebrochenen Test folgende Tests fehlen. `aborted` ist ab Vertrag 1.3.1 nur bei einem
Stopp `true`, bei einem Laufzeitfehler `false` (siehe [Vertrag 1.3.1](#vertrag-131-cpus-im-ergebnis-aborted-und-fatal)).
Ein `null`-Messwert kommt nur bei `partial: true` mit `stopped_in` `preparing`/`warmup` vor.

### Score und Referenzwerte

`meta.cpu_score` (in `sb-result` und `sb-info`): `formula`, `points` (1000), `reference_threads`
(1), `reference_system` und `ref_ops_per_s` je workload (`int`, `float`, `crypto`, `mixed`).
`summary.score_reference` (cpu) ist die Referenz des Tests; `score = ops_per_s ÷
score_reference × 1000`. Die GUI muss keine Werte aus `docs/core.md` übernehmen.

### CPU-Last und Zeitreihe

- `summary.proc_cpu_pct` (alle Typen) und `summary.cpu_pct` (jetzt auch storage): Mittel über
  die Messphase, Skala wie im Tick.
- `timeseries[]` hat einen Punkt je `tick`-Event, also im Takt `--tick-ms`, nicht fest 1 s;
  das Intervall steht in `tick_ms` des Ergebnisses. Neu: `timeseries[].proc_cpu_pct`, bei cpu
  `timeseries[].score`.

### Fortschritt `preparing`

| Testtyp | Häufigkeit | `total` |
|---|---|---|
| storage | ≥ 1 s Abstand beim Füllen einer Testdatei, plus ein Event je fertiger Datei | Summe `file_size` aller `kind: file`-Targets |
| memory | etwa alle 0,3 s (≥ 250 ms Abstand), plus ein Abschluss-Event mit `done = total` | `threads` × `summary.buffer_size_bytes` (tatsächliche, abgerundete Größe), auch bei `copy` (beide Pufferhälften werden beschrieben) |
| cpu | keine `progress`-Events (nichts vorzubereiten) | – |

`warmup`: storage etwa jede Sekunde, cpu/memory etwa alle 0,5 s (`unit: "s"`).

## Vertrag 1.3.1: `cpus` im Ergebnis, `aborted` und `fatal`

### `cpus` im Ergebnis

Bei cpu- und memory-Tests steht `cpus[]` zusätzlich direkt im Testobjekt des Ergebnisses
(`tests[].cpus`, neben `summary`), mit demselben Inhalt wie `phase.cpus` im ersten
`phase`-Event des Tests (Felder `id`, `cpu`, `group`, `number`, `numa_node`, `core`,
`smt_sibling_of`, siehe [phase-Event beim Teststart](#phase-event-beim-teststart)). Damit kann
die GUI SMT-Geschwister und NUMA-Knoten auch nachträglich, ohne Progress-Strom, über
`per_worker[].id` zuordnen.

- Optional im Schema (`$defs/cpu_binding` in `sb-result-1.schema.json`); sbench ab 1.3.1 gibt es
  bei cpu/memory immer aus, sobald die Threads verteilt sind, also auch bei `partial: true`.
  Es fehlt nur, wenn die Prozessor-Topologie nicht lesbar war.
- `tests[].cpus[i].cpu` = `summary.cpus[i]` = `per_worker[i].cpu`; `numa_node` = `per_worker[i].node`.
- Bei storage, net_client, net_server und hyperv_info ist `cpus` nicht erlaubt.
- Beispiele: [`result-compute.json`](../schema/examples/result-compute.json),
  [`result-partial.json`](../schema/examples/result-partial.json).

### `aborted` und `errors[].fatal`

| Ende des Laufs | Exit | `aborted` (Ergebnis, `done`) | betroffener Test | folgende Tests |
|---|---|---|---|---|
| vollständig | 0 | `false` | – | – |
| Stopp (`sbench stop`, Strg+C) | 3 | `true` | `partial: true`, `stopped_in`; kein `fatal` | fehlen |
| Laufzeitfehler (z. B. `E_TARGET_SIZE`, `E_OUT_OF_MEMORY`, zu viele I/O-Fehler eines Workers) | 1 | **`false`** (bis 1.3: `true`) | `partial: true`, `stopped_in`; genau ein `errors[]`-Eintrag mit `fatal: true` | fehlen, werden nicht mehr ausgeführt |

- `fatal: true` steht an genau dem `errors[]`-Eintrag, der den Test und damit den Lauf beendet
  hat, also höchstens einmal im Ergebnis und nur im letzten Test. Fehlt das Feld, gilt `false`.
  Nicht abbrechende Fehler (z. B. einzelne fehlgeschlagene I/Os unter der Abbruchgrenze) haben
  kein `fatal`. Bei storage kann derselbe Eintrag zusammengefasste I/O-Fehler (`count`) tragen.
- Das `error`-Event im Progress-Strom bleibt unverändert (ohne `fatal`).
- Beispiele: [`result-fatal.json`](../schema/examples/result-fatal.json),
  [`progress-fatal.jsonl`](../schema/examples/progress-fatal.jsonl).

### `done.exit_code`

Das `done`-Event trägt ab 1.3.1 `exit_code` (Integer), genau den Exit-Code, mit dem sbench
direkt danach endet. `done` wird nur geschrieben, wenn die Ergebnisdatei geschrieben ist,
deshalb kommen dort praktisch nur 0, 1 und 3 vor (2 und 4 enden ohne `done`); das Schema
erlaubt 0–4.

- `exit_code: 3` genau dann, wenn `aborted: true`.
- `exit_code: 1` genau dann, wenn das Ergebnis einen `errors[]`-Eintrag mit `fatal: true`
  hat; im Progress-Strom steht davor das zugehörige `error`-Event.
- Optional im Schema; ältere sbench-Versionen (bis 1.3) schreiben es nicht.
- Beispiele: [`progress-fatal.jsonl`](../schema/examples/progress-fatal.jsonl) (1),
  [`progress-partial.jsonl`](../schema/examples/progress-partial.jsonl) (3).

Empfohlene Reihenfolge für Clients (z. B. die GUI, wenn sie den Prozess nicht selbst
gestartet hat oder sein Exit-Code nicht verfügbar ist, etwa bei `runas`):

1. `exit_code` der Zeile zum Lauf in `<data_dir>\runs\index.jsonl`.
2. Sonst `done.exit_code` aus dem Progress-Strom bzw. der Fortschrittsdatei.
3. Sonst Ableitung aus dem Ergebnis (bzw. `done.aborted` bei Läufen bis 1.3):
   `aborted: true` → 3; ein `errors[]`-Eintrag mit `fatal: true` → 1; sonst 0. Bei
   Ergebnissen bis 1.3 bedeutet `aborted: true` Stopp **oder** Laufzeitfehler; unterscheiden
   lässt sich das dann nur über die Fehler in `tests[].errors`.

## Exit-Codes

| Code | Bedeutung |
|---|---|
| 0 | Erfolg |
| 1 | Laufzeitfehler (I/O-Fehler, Netz nicht erreichbar, `E_PIPE_CONNECT`, interner Fehler) |
| 2 | Ungültiger Job bzw. ungültige Argumente (inkl. `E_DESTRUCTIVE_NOT_ALLOWED`) |
| 3 | Abgebrochen (`sbench stop`, Strg+C); Teilergebnis wurde geschrieben |
| 4 | Fehlende Rechte (z. B. `physical`/`volume`-Target ohne Administratorrechte) |

Bei 0 und 3 existiert die Ergebnisdatei immer. Bei 1 wird – soweit möglich – ein
Teilergebnis mit den Fehlern in `tests[].errors` geschrieben; ab Vertrag 1.3.1 mit
`aborted: false` und `fatal: true` an dem Fehler, der den Lauf beendet hat (vorher
`aborted: true`).
Bei 2 und 4 wird keine Messung gestartet und keine Ergebnisdatei geschrieben;
die Ursache steht in einem `error`-Event (mit `field` als JSON Pointer bei 2).

## Rechte und Sicherheit

- Zugriff auf **physische Disks** (`\\.\PhysicalDriveN`) und **Volumes** (`\\.\D:`)
  erfordert Administratorrechte. Ohne Erhöhung endet `run` mit Exit-Code 4.
- **`SetFileValidData`** (schnelles Anlegen großer Testdateien ohne Nullen-Schreiben)
  benötigt Administratorrechte bzw. `SeManageVolumePrivilege`. Ohne diese Rechte legt
  sbench die Testdatei durch Schreiben an (langsamer, Phase `preparing`).
- **Schreibtests auf `volume`/`physical` zerstören Daten.** Sie laufen nur mit
  doppelter Freigabe: `allow_destructive: true` im Target **und** `sbench run
  --allow-destructive`. Die GUI setzt den Schalter erst nach ausdrücklicher Bestätigung.
  Windows blockiert zusätzlich Schreiben auf Bereiche eingehängter Volumes; sbench
  bricht in diesem Fall mit `E_ACCESS_DENIED` ab.
- Die GUI selbst läuft nicht erhöht; nur `sbench.exe` wird bei Bedarf per `runas`
  gestartet. Deshalb laufen Fortschritt über die Named Pipe und Stop über das benannte
  Event, nicht über stdout/stdin oder Prozess-Handles.

## Laufzeit-Fehlercodes (Auswahl)

`E_IO`, `E_ACCESS_DENIED`, `E_OUT_OF_MEMORY` (Puffer nicht anforderbar), `E_NUMA_NODE`,
`E_SIMD_UNSUPPORTED`, `E_NET_CONNECT`, `E_NET_BIND`, `E_TIMEOUT`, `E_PIPE_CONNECT`
(Named Pipe nach 10 s nicht erreichbar), `E_DESTRUCTIVE_NOT_ALLOWED`, `E_INTERNAL`. Codes folgen dem Muster `E_[A-Z0-9_]+`; neue Codes dürfen ohne
Versionswechsel ergänzt werden, die GUI muss unbekannte Codes generisch anzeigen.

## Build & Plattformen

- **Mindestversion:** Windows 10 1607 / Windows Server 2016.
- Kompiliert mit `_WIN32_WINNT=0x0A00`; neuere APIs werden zur Laufzeit geprüft
  (`GetProcAddress`) und nur bei Verfügbarkeit genutzt.
- **Statische CRT** (`/MT`), damit keine VC-Redistributable nötig ist.
- **x64 und ARM64** werden nativ gebaut (keine Emulation).
- `.github/workflows/build-windows.yml` ist zusätzlich per `workflow_call` aufrufbar
  (z. B. `uses: ./.github/workflows/build-windows.yml` mit `needs:`), optionaler Input
  `version` (string). Outputs: `version`, `commit`, `cli_artifact_x64`, `cli_artifact_arm64`.
  Die Artifacts `sbench-cli-x64` und `sbench-cli-arm64` (je genau eine `sbench.exe`) haben
  stabile Namen.
  Bei `push`/`pull_request` läuft er nur bei Änderungen an Kern-Pfaden (siehe `docs/release.md`).
- Releases (Tag `v*`) baut und veröffentlicht `.github/workflows/release.yml`
  (Kern + GUI, Versionsprüfung, `SHA256SUMS.txt`), siehe `docs/release.md`.
- Die CI lädt Smoke-Artifacts mit dem Namen `sbench-smoke-<runner>-<arch>` hoch,
  z. B. `sbench-smoke-windows-2022-x64`. Sie enthalten `result.json`,
  `progress.jsonl` und `info.json` eines kurzen Testlaufs und dienen als Daten für die
  Screenshots im README.

## Beispiel: Aufruf aus der GUI

```
sbench --data-dir C:\ProgramData\sbench validate --job C:\Temp\job.json
sbench --data-dir C:\ProgramData\sbench run --job C:\Temp\job.json --out C:\Temp\result.json ^
           --progress pipe:\\.\pipe\sbench-20261020-081502-a1b2 --tick-ms 250 ^
           --id 20261020-081502-a1b2
sbench list --running --json
sbench stop --id 20261020-081502-a1b2
sbench bundle --out C:\Temp\diag.zip --results C:\Temp\result.json --include "%LOCALAPPDATA%\sbench-gui\logs" --anonymize
sbench export C:\Temp\result.json --csv C:\Temp\result.csv --style iometer
```
