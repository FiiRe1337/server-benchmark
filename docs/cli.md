# CLI-Vertrag `sbench.exe`

Dieses Dokument beschreibt die Schnittstelle zwischen dem C-Messkern (`sbench.exe`) und
der PySide6-GUI. Die GUI startet `sbench.exe` ausschließlich als Unterprozess und
kommuniziert nur über Kommandozeilenargumente, Dateien, JSON-Lines-Events, Exit-Codes
und ein benanntes Stop-Event. Alle Datenformate sind als JSON Schema (draft 2020-12)
in [`schema/`](../schema/) definiert, Beispiele liegen in [`schema/examples/`](../schema/examples/).

| Format | Schema | Beispiel |
|---|---|---|
| Job-Datei | [`sb-job-1.schema.json`](../schema/sb-job-1.schema.json) | [`job-storage-mix.json`](../schema/examples/job-storage-mix.json), [`job-full.json`](../schema/examples/job-full.json) |
| Ergebnis | [`sb-result-1.schema.json`](../schema/sb-result-1.schema.json) | [`result-storage.json`](../schema/examples/result-storage.json) |
| Fortschritt (JSON Lines) | [`sb-progress-1.schema.json`](../schema/sb-progress-1.schema.json) | [`progress-storage.jsonl`](../schema/examples/progress-storage.jsonl) |
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
Netz-Ticks `mbps`.

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
sendet `done` mit `aborted: true` und endet mit Exit-Code **3**.

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

### `sbench export`

```
sbench export <result.json> --csv <out.csv> [--style iometer|flat]
```

- `flat` (Standard): eine Zeile pro Test bzw. pro Worker/Access-Spec mit Spalten
  `test,type,scope,id,iops,mbps,read_iops,write_iops,read_mbps,write_mbps,lat_mean_us,lat_p99_us,lat_max_us,errors`;
  Trennzeichen Komma, Dezimalpunkt, UTF-8.
- `iometer`: an das Iometer-Ergebnis-CSV angelehnte Spaltennamen
  (`Target Type`, `Target Name`, `Access Specification Name`, `IOps`, `MBps (Decimal)`,
  `Average Response Time`, …) zur Weiterverarbeitung in vorhandenen Auswertungen.
  Nur Storage-Tests werden exportiert.

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
sbench import-icf <datei.icf> --out <job.json>
```

Wandelt eine Iometer-Konfiguration (ICF) in einen Job `sb-job/1` um (Worker, Targets,
Queue Depth, Access-Specs mit Gewichten, Laufzeit). Felder ohne Entsprechung
(z. B. Netzwerk-Worker, Ramp-up-Varianten, mehrere Manager) werden nicht übernommen und
als Warnungen gemeldet: Ausgabe `sb-validate/1` auf stdout mit `ok: true` und
`warnings` (Code `W_ICF_UNSUPPORTED`, `path` = JSON Pointer auf das betroffene Ziel im
erzeugten Job bzw. `""`). Exit-Codes: 0 ok (auch mit Warnungen), 1 Datei nicht lesbar,
2 ICF ungültig oder nicht konvertierbar.

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

## Exit-Codes

| Code | Bedeutung |
|---|---|
| 0 | Erfolg |
| 1 | Laufzeitfehler (I/O-Fehler, Netz nicht erreichbar, `E_PIPE_CONNECT`, interner Fehler) |
| 2 | Ungültiger Job bzw. ungültige Argumente (inkl. `E_DESTRUCTIVE_NOT_ALLOWED`) |
| 3 | Abgebrochen (`sbench stop`, Strg+C); Teilergebnis wurde geschrieben |
| 4 | Fehlende Rechte (z. B. `physical`/`volume`-Target ohne Administratorrechte) |

Bei 0 und 3 existiert die Ergebnisdatei immer. Bei 1 wird – soweit möglich – ein
Teilergebnis mit `aborted: true` und den Fehlern in `tests[].errors` geschrieben.
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

`E_IO`, `E_ACCESS_DENIED`, `E_NET_CONNECT`, `E_NET_BIND`, `E_TIMEOUT`, `E_PIPE_CONNECT`
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
