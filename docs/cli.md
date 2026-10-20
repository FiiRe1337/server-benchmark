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
| Validierung | [`sb-validate-1.schema.json`](../schema/sb-validate-1.schema.json) | [`validate-error.json`](../schema/examples/validate-error.json) |

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

## Befehle

### `sbench run`

```
sbench run --job <job.json> --out <result.json>
           [--progress stdout | pipe:\\.\pipe\sbench-<id> | file:<pfad.jsonl>]
           [--tick-ms <ms>] [--id <id>]
```

| Option | Bedeutung |
|---|---|
| `--job <f>` | Job-Datei (`sb-job/1`). Wird vor dem Start vollständig validiert (wie `sbench validate`). |
| `--out <f>` | Ergebnisdatei (`sb-result/1`). Wird zunächst als `<f>.tmp` geschrieben und am Ende atomar umbenannt. |
| `--progress` | Ziel für Fortschritts-Events (`sb-progress/1`, JSON Lines). Ohne Angabe werden keine Events ausgegeben. |
| `--tick-ms <ms>` | Intervall der `tick`-Events in Millisekunden. Standard **1000**, Minimum 100. |
| `--id <id>` | Lauf-ID (`[A-Za-z0-9_-]{1,64}`), u. a. für `sbench stop`. Ohne Angabe erzeugt sbench eine ID und meldet sie im `start`-Event (`run_id`). |

Progress-Ziele:

- `stdout` – ein Event pro Zeile; sbench ruft nach **jeder** Zeile `fflush` auf, damit
  die GUI ohne Pufferverzögerung liest.
- `pipe:\\.\pipe\sbench-<id>` – die **GUI legt die Named Pipe als Server an**
  (`CreateNamedPipe`, Byte-Modus, nur ausgehend aus Sicht von sbench) und wartet auf die
  Verbindung; sbench verbindet sich als **Client** (`CreateFile` auf den Pipe-Namen,
  bis zu 10 s Wiederholung bei `ERROR_PIPE_BUSY`/`ERROR_FILE_NOT_FOUND`). Dadurch
  funktioniert der Kanal auch, wenn sbench per `runas` erhöht gestartet wird und stdout
  der GUI nicht erreicht. Die Standard-DACL einer Pipe erlaubt Mitgliedern der
  Administratorgruppe Vollzugriff, ein erhöhter Client kann sich also mit einer von der
  nicht erhöhten GUI angelegten Pipe verbinden.
- `file:<pfad.jsonl>` – Events werden an die Datei angehängt und nach jeder Zeile
  geflusht (für Tests und Fehlersuche).

Ablauf der Events: `start` → pro Test `phase` (`preparing`, `warmup`, `measure`,
`cleanup`) mit `tick`-Events **nur während `measure`** (`t` = Sekunden seit Beginn der
Messphase) → `done` (immer das letzte Event, auch bei Abbruch oder Laufzeitfehler,
sofern ein Ergebnis geschrieben wurde). `error`-Events können jederzeit auftreten.
Phasen ohne Bedeutung für einen Testtyp (z. B. `warmup` bei `hyperv_info`) dürfen
entfallen. Storage-Ticks enthalten immer `iops`, `mbps`, `lat_us`, `cpu_pct`;
`workers` ist optional. CPU-Ticks nutzen `ops_per_s`, Memory-Ticks `gbps`,
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

### `sbench info --json`

Gibt Systeminformationen und mögliche Testziele (Volumes, physische Disks,
Netzwerkadapter) als `sb-info/1` auf stdout aus. Ohne Administratorrechte können
Angaben zu physischen Disks unvollständig sein (z. B. fehlendes Modell).

### `sbench validate --job <f>`

Prüft eine Job-Datei gegen `sb-job/1` **und** die semantischen Regeln (siehe unten),
gibt `sb-validate/1` auf stdout aus und endet mit **0** (gültig) oder **2** (ungültig).
`path` ist ein JSON Pointer (RFC 6901), z. B. `/tests/0/access_specs/1/block_size`.

Semantische Regeln (über das JSON Schema hinaus):

| Code | Regel |
|---|---|
| `E_SCHEMA` | Verstoß gegen das JSON Schema (Typ, Pflichtfeld, Wertebereich, unbekanntes Feld). |
| `E_WEIGHT_SUM` | Summe der `weight`-Werte der `access_specs` eines Tests ist nicht 100. |
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
```

## Exit-Codes

| Code | Bedeutung |
|---|---|
| 0 | Erfolg |
| 1 | Laufzeitfehler (I/O-Fehler, Netz nicht erreichbar, interner Fehler) |
| 2 | Ungültiger Job bzw. ungültige Argumente |
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
- **Schreibtests auf `volume`/`physical` zerstören Daten.** Windows blockiert Schreiben
  auf Bereiche eingehängter Volumes; sbench bricht in diesem Fall mit `E_ACCESS_DENIED`
  ab. Die GUI muss vor solchen Tests ausdrücklich bestätigen lassen.
- Die GUI selbst läuft nicht erhöht; nur `sbench.exe` wird bei Bedarf per `runas`
  gestartet. Deshalb laufen Fortschritt über die Named Pipe und Stop über das benannte
  Event, nicht über stdout/stdin oder Prozess-Handles.

## Laufzeit-Fehlercodes (Auswahl)

`E_IO`, `E_ACCESS_DENIED`, `E_NET_CONNECT`, `E_NET_BIND`, `E_TIMEOUT`, `E_PIPE_CONNECT`,
`E_INTERNAL`. Codes folgen dem Muster `E_[A-Z0-9_]+`; neue Codes dürfen ohne
Versionswechsel ergänzt werden, die GUI muss unbekannte Codes generisch anzeigen.

## Beispiel: Aufruf aus der GUI

```
sbench validate --job C:\Temp\job.json
sbench run --job C:\Temp\job.json --out C:\Temp\result.json ^
           --progress pipe:\\.\pipe\sbench-20261020-081502-a1b2 --tick-ms 250 ^
           --id 20261020-081502-a1b2
sbench stop --id 20261020-081502-a1b2
sbench export C:\Temp\result.json --csv C:\Temp\result.csv --style iometer
```
