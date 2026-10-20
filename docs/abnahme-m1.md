# Abnahme Meilenstein 1 – Anleitung für den Tester

Diese Anleitung beschreibt, wie der Messkern `sbench.exe` aus Meilenstein 1 auf echter
Hardware geprüft wird. Wir vergleichen sbench mit **DiskSpd** (Microsoft) und optional
**Iometer**. Zum Schluss packst du alle Ergebnisse in ein Bundle und schickst es zurück.

Welche Windows-Versionen und Varianten die CI bereits automatisch prüft und welche nur
du von Hand prüfen kannst, steht in [kompatibilitaet.md](kompatibilitaet.md).

---

## 1. Voraussetzungen

| Was | Details |
|---|---|
| Betriebssystem | Windows 10/11 (ab 1607) oder Windows Server 2016 bis 2025, auch Server Core und Hyper-V Server. x64 oder ARM64. |
| Rechte | **Administrator-Konsole** (Rechtsklick → „Als Administrator ausführen“). Ohne Adminrechte werden Testdateien langsamer angelegt (siehe Abschnitt 10). |
| Testlaufwerk | Ein Laufwerk mit mindestens **20 GiB** frei, z. B. `D:`. Für Job 07 ein zweites Laufwerk `E:` (sonst Pfad anpassen, siehe 4.). |
| DiskSpd | Version 2.2 oder neuer: <https://github.com/microsoft/diskspd/releases>. `amd64\diskspd.exe` bzw. `ARM64\diskspd.exe` nach `C:\Tools\diskspd\` kopieren. |
| Iometer (optional) | Version 1.1.0: <https://www.iometer.org>. Nur x64 und nur mit Desktop-Oberfläche. |
| Ruhe auf dem System | Keine anderen VMs, Backups, Updates oder Virenscans während der Messung. Energieplan „Höchstleistung“ (`powercfg /setactive SCHEME_MIN`). |
| Virenscanner | Ausnahme für den Testordner bzw. die Testdateien (z. B. `Add-MpPreference -ExclusionPath D:\sbtest.dat`), sonst schwanken die Werte. |

Python oder eine andere Laufzeit brauchst du **nicht**. `sbench.exe` ist eine einzelne
Datei mit statischer C-Laufzeit.

## 2. Artefakt herunterladen

1. Auf GitHub das Repository öffnen → **Actions** → Workflow **„Windows-Build (sbench)“**
   → den neuesten grünen Lauf des Branches `feat/core-m1` (oder später `main`) öffnen.
2. Unten unter **Artifacts** herunterladen:
   - `sbench-cli-x64` (nur `sbench.exe`) für normale PCs/Server,
   - `sbench-cli-arm64` für ARM64-Geräte,
   - `sbench-windows-x64` enthält zusätzlich `sbcore.dll`, Header und Doku.
3. Alternativ mit der GitHub-CLI: `gh run download <run-id> -n sbench-cli-x64`.
4. Sobald ein Tag `v*` existiert, liegen dieselben Dateien unter **Releases**.
5. ZIP entpacken, z. B. nach `C:\Tools\sbench\`. Die Job-Dateien aus `jobs\abnahme-m1\`
   (im Repository bzw. im Artefakt `sbench-windows-*`) nach `C:\Tools\sbench\jobs\` kopieren.

**Datenverzeichnis:** sbench legt Ergebnisse, Logs und Fortschrittsdateien in
`C:\Tools\sbench\data\` ab (neben der EXE, falls beschreibbar), sonst unter
`%ProgramData%\sbench\`. Der tatsächliche Pfad steht in `sbench info --json` unter
`meta.data_dir`.

## 3. Schnellcheck

```bat
cd /d C:\Tools\sbench
sbench version
sbench info
sbench validate --job jobs\01-4k-random-read-qd1.json
```

- `version` listet `sb-job/1`, `sb-result/1`, `sb-progress/1`, `sb-info/1`, `sb-validate/1`, `sb-runs/1`.
- `info` zeigt OS, CPU, RAM und die Hypervisor-Rolle (`host` auf dem Hyper-V-Host, `guest` in der VM).
- `validate` muss `"ok": true` melden. Warnungen (`warnings`) sind erlaubt, z. B.
  `W_LOW_DISK_SPACE`, wenn das Laufwerk knapp wird.

## 4. Job-Dateien anpassen

Alle Jobs testen `D:\sbtest.dat` (8 GiB). Liegt dein Testlaufwerk woanders, ersetze in
allen Dateien `D:\\` durch deinen Laufwerksbuchstaben (in JSON doppelter Backslash).
Job 07 nutzt zusätzlich `E:\sbtest2.dat`. Hast du nur ein Laufwerk, setze beide Pfade
auf dasselbe Laufwerk mit unterschiedlichen Dateinamen.

Jeder Job hat 5 s Warmup und 30 s Messung, Cache-Modus `unbuffered`
(entspricht `FILE_FLAG_NO_BUFFERING | FILE_FLAG_WRITE_THROUGH`, bei DiskSpd `-Sh`).
Die Queue Depth gilt **pro Worker**, genau wie `-o` pro Thread bei DiskSpd.

| Nr. | Job-Datei | Inhalt |
|---|---|---|
| 01 | `01-4k-random-read-qd1.json` | 4K random read, 1 Worker, QD1 (reine Latenz) |
| 02 | `02-4k-random-read-qd32.json` | 4K random read, 4 Worker × QD32 |
| 03 | `03-4k-random-write-qd32.json` | 4K random write, 4 Worker × QD32 |
| 04 | `04-64k-seq-read.json` | 64K sequentiell lesen, 1 Worker × QD8 |
| 05 | `05-64k-seq-write.json` | 64K sequentiell schreiben, 1 Worker × QD8 |
| 06 | `06-iometer-mix-8k-70-30.json` | Iometer-Mix: 8K random, 70 % lesen / 30 % schreiben, 4 Worker × QD16 |
| 07 | `07-multi-worker-2-targets.json` | 4K random read, 4 Worker auf 2 Targets (Worker 0/2 → Target 0, 1/3 → Target 1), QD16 |

## 5. Messung durchführen

Für jeden Job: **erst sbench, dann DiskSpd, dann (optional) Iometer**, jeweils auf
dieselbe Testdatei. Zwischen den Läufen ca. 30 s warten. Jeden Vergleich am besten
**zweimal** laufen lassen: Weichen schon zwei sbench-Läufe untereinander um mehr als 5 %
ab, ist das System zu unruhig für einen sinnvollen Vergleich (siehe Abschnitt 10).

### 5.1 sbench

```bat
sbench run --job jobs\01-4k-random-read-qd1.json --progress stdout --keep-files
```

- Ohne `--out` landet das Ergebnis in `data\results\<run_id>.json`. Die `run_id`
  steht im ersten Event (`"ev":"start"`).
- Beim ersten Lauf wird die Testdatei angelegt (mit Adminrechten in Sekunden per
  `SetFileValidData`, sonst wird sie vollständig beschrieben).
- `--keep-files` lässt die Testdatei nach dem Lauf liegen, damit DiskSpd und die
  weiteren Jobs dieselbe Datei nutzen. Ohne die Option löscht sbench Testdateien, die es
  selbst angelegt hat, nach dem Lauf wieder. Am Ende der Abnahme `D:\sbtest.dat` (und
  `E:\sbtest2.dat`) von Hand löschen.
- Abbrechen: `Strg+C` oder in einer zweiten Konsole `sbench stop --id <run_id>`.
- Kennzahlen als CSV: `sbench export data\results\<run_id>.json --csv 01.csv --style flat`.
  Relevant sind in der Zeile `scope = total` die Spalten `iops`, `mbps`, `lat_mean_us`, `lat_p99_us`.

### 5.2 DiskSpd

Ausgabe jeweils in eine Datei umleiten, z. B. `> C:\Abnahme\diskspd\01.txt`. Die Option
`-Z1M` sorgt für zufällige (nicht komprimierbare) Schreibdaten wie bei sbench.

| Nr. | DiskSpd-Befehl |
|---|---|
| 01 | `diskspd -b4K -r -w0 -o1 -t1 -d30 -W5 -Sh -L -c8G D:\sbtest.dat` |
| 02 | `diskspd -b4K -r -w0 -o32 -t4 -d30 -W5 -Sh -L -c8G D:\sbtest.dat` |
| 03 | `diskspd -b4K -r -w100 -o32 -t4 -d30 -W5 -Sh -L -Z1M -c8G D:\sbtest.dat` |
| 04 | `diskspd -b64K -w0 -o8 -t1 -d30 -W5 -Sh -L -c8G D:\sbtest.dat` |
| 05 | `diskspd -b64K -w100 -o8 -t1 -d30 -W5 -Sh -L -Z1M -c8G D:\sbtest.dat` |
| 06 | `diskspd -b8K -r -w30 -o16 -t4 -d30 -W5 -Sh -L -Z1M -c8G D:\sbtest.dat` |
| 07 | `diskspd -b4K -r -w0 -o16 -t2 -d30 -W5 -Sh -L -c8G D:\sbtest1.dat E:\sbtest2.dat` |

Hinweise:
- Bei DiskSpd ist `-t` die Anzahl Threads **pro Ziel**. Job 07 hat deshalb `-t2` bei
  zwei Zielen, zusammen 4 Threads wie die 4 sbench-Worker.
- `-c8G` legt die Datei an, falls sie fehlt. sbench und DiskSpd können dieselbe Datei nutzen.
- DiskSpd zeigt **MiB/s** (1 MiB = 1 048 576 Bytes), sbench zeigt **MB/s** (10⁶ Bytes).
  Zum Vergleich: **MB/s = MiB/s × 1,048576**. IOPS sind direkt vergleichbar.
- Werte aus DiskSpd: Abschnitt „Total IO“, Zeile `total:` (I/O per s, MiB/s, AvgLat in ms)
  und in der Latenztabelle (`-L`) die Zeile `99th` in ms (× 1000 = µs).

### 5.3 Iometer (optional)

Iometer **als Administrator** starten. Pro Job:

| Einstellung | Wert |
|---|---|
| Disk Targets | Laufwerk `D:` auswählen. Iometer legt dort `iobw.tst` an (eigene Testdatei). |
| Maximum Disk Size | `16777216` Sektoren (= 8 GiB bei 512 Bytes) |
| # of Outstanding I/Os | QD aus der Tabelle in Abschnitt 4 (pro Worker und Target) |
| Workers | Anzahl Worker aus Abschnitt 4 (Job 07: je 2 Worker auf `D:` und `E:`) |
| Access Specification | Transfer Request Size = Blockgröße, Percent Read = Leseanteil, Percent Random = 100 % (random) oder 0 % (sequentiell), Align I/Os on = Request Size Boundaries |
| Test Setup | Ramp Up Time 5 s, Run Time 30 s |
| Results Display | Update-Frequenz 1 s; am Ende das CSV speichern (`File → Save Results`) |

Iometer meldet MB/s als „MBps (Decimal)“ (10⁶ Bytes, wie sbench) und „MBps (Binary)“.

## 6. Auswertung und Toleranz

| Kennzahl | Toleranz | Bemerkung |
|---|---|---|
| IOPS | **± 5 %** | Hauptkriterium |
| MB/s | **± 5 %** | MiB/s aus DiskSpd vorher umrechnen (× 1,048576) |
| mittlere Latenz | ± 10 % (Richtwert) | ergibt sich bei fester QD aus den IOPS |
| p99-Latenz | **nur mit Vorsicht** | Perzentile reagieren stark auf einzelne Ausreißer. sbench rechnet mit einem Histogramm (Klassenbreite ≤ 0,78 %), DiskSpd mit allen Einzelwerten. Abweichungen bis ca. 20 % sind ohne weitere Hinweise kein Fehler. |

Trage pro Job die Werte von sbench, DiskSpd und (falls gemessen) Iometer in eine Tabelle
ein und notiere die Abweichung in Prozent: `(sbench − DiskSpd) / DiskSpd × 100`.

## 7. Reihenfolge: erst Host, dann VM

1. **Hyper-V-Host:** alle 7 Jobs wie oben auf dem physischen Server. Danach
   `sbench info --json > info-host.json`. Erwartet: `meta.hypervisor.role = "host"`.
2. **VM auf derselben Disk:** eine VM (Generation 2) mit einer **VHDX fester Größe** auf
   dem gleichen physischen Laufwerk anlegen, in der VM ein Testlaufwerk mit mindestens
   20 GiB einrichten. Andere VMs während der Messung ausschalten. Dann alle 7 Jobs erneut,
   jeweils sbench und DiskSpd **in der VM**. Erwartet: `meta.hypervisor.role = "guest"`.
3. Ein Vergleich Host ↔ VM ist kein Abnahmekriterium, zeigt aber den
   Virtualisierungsaufwand. Die ±5 % gelten immer **sbench ↔ DiskSpd auf demselben System**.

## 8. Zusatzprüfungen

Diese Punkte prüft die CI nicht oder nicht vollständig. Ergebnis jeweils in deiner
Vergleichstabelle bzw. im Ticket notieren.

### 8.1 Testdateien aufräumen (Issue #5)

```bat
del D:\sbtest.dat
sbench run --job jobs\04-64k-seq-read.json --out C:\Abnahme\cleanup.json
dir D:\sbtest.dat
```

- Erwartet: `dir` findet die Datei **nicht** mehr. In `cleanup.json` steht unter
  `tests[0].targets[0]` `"created": true, "deleted": true`.
- Gegenprobe mit `--keep-files`: Datei bleibt, `"created": true, "deleted": false`.
  Ein weiterer Lauf ohne Option auf die nun vorhandene Datei lässt sie stehen
  (`"created": false, "deleted": false`).
- Abbruch: Lauf ohne `--keep-files` starten und nach einigen Sekunden mit `Strg+C`
  beenden (Exit 3). Auch dann ist die selbst angelegte Datei weg.

### 8.2 Ohne Adminrechte: Exit 4 (Issue #6)

In einer **nicht erhöhten** Konsole (normale Eingabeaufforderung, nicht „Als
Administrator ausführen“), auf einem Konto mit Adminrechten also mit UAC-Filter:

```bat
sbench info --json | findstr elevated
sbench validate --job jobs\abnahme-volume-read.json
sbench run --job jobs\abnahme-volume-read.json --progress stdout
echo Exit-Code: %ERRORLEVEL%
```

`jobs\abnahme-volume-read.json` legst du dafür einmalig an (nur Lesen, es wird nichts
geschrieben):

```json
{"schema": "sb-job/1", "name": "volume lesen", "tests": [{"type": "storage",
  "targets": [{"path": "\\\\.\\C:", "kind": "volume"}], "duration_s": 5, "warmup_s": 0,
  "access_specs": [{"weight": 100, "block_size": "4KiB", "read_pct": 100, "random_pct": 100}]}]}
```

Erwartet:

| Schritt | Ergebnis |
|---|---|
| `info --json` | `"elevated": false` |
| `validate` | Exit 0, `"ok": true`, in `warnings` ein Eintrag `W_NEEDS_ADMIN` für `/tests/0/targets/0` |
| `run` | **Exit 4**, `error`-Event mit `"code": "E_ACCESS_DENIED"` und Text „erfordert Administratorrechte“ auf stderr, kein Ergebnis unter `data\results\` |

Danach denselben `run` in einer Konsole **als Administrator**: Exit 0 mit Messwerten.

Hinweis: Der Fehlercode bei Exit 4 heißt laut Vertrag (`docs/cli.md`, Fehlercodes)
`E_ACCESS_DENIED`; `W_NEEDS_ADMIN` ist die zugehörige Warnung von `validate`. Die CI
prüft dasselbe automatisch mit einem eingeschränkten Token (Smoke-Test, Abschnitt
„#6: ohne Adminrechte“). Die Prüfung von Hand bestätigt es unter echter UAC.

### 8.3 Windows Server 2016 (Issue #7)

Für die CI gibt es keinen Server-2016-Runner. Bitte auf **Server 2016 mit Desktop** und
auf **Server 2016 Core** (Build 14393, beides als Administrator):

```bat
sbench version
sbench info --json > info-2016.json
sbench validate --job jobs\01-4k-random-read-qd1.json
sbench run --job jobs\01-4k-random-read-qd1.json --progress stdout
sbench bundle --all --out abnahme-m1-2016.zip
```

Erwartet: alle Befehle mit Exit 0; in `info-2016.json` `meta.os.build` beginnend mit
`14393.` und plausible CPU-, RAM- und NUMA-Angaben; der Lauf liefert
IOPS > 0 ohne `errors`. Auf Server Core gibt es keine GUI; die Befehle laufen in
`cmd` bzw. PowerShell. Das Ergebnis (OS-Build, ok/Fehler) trägt der PM in
`docs/kompatibilitaet.md` ein; dafür `info-2016.json` und das Bundle mitschicken.

## 9. Bundle erstellen und zurückschicken

Auf **jedem** getesteten System (Host und VM) zum Schluss:

```bat
sbench bundle --all --include C:\Abnahme --out abnahme-m1.zip
```

- `--all` nimmt alle beendeten Läufe aus `data\runs\index.jsonl` mit: Ergebnisse,
  Fortschrittsdateien, Logs und Job-Kopien. Ohne `--all` enthält das Bundle nur
  `info.json` und das Manifest.
- `--include C:\Abnahme` packt deinen Ordner mit DiskSpd-Ausgaben, Iometer-CSVs und
  deiner Vergleichstabelle unter `extra\Abnahme\` dazu.
- Wenn Rechnernamen, Benutzernamen und IP-Adressen nicht weitergegeben werden sollen,
  zusätzlich `--anonymize` angeben.
- Für die VM die Datei z. B. `abnahme-m1-vm.zip` nennen.

Die ZIP-Dateien bitte an den PM schicken (oder im Ticket anhängen).

## 10. Was tun bei Fehlern

| Symptom | Ursache / Abhilfe |
|---|---|
| Exit-Code **4**, „erfordert Administratorrechte“ | Konsole als Administrator starten. Gilt für `volume`/`physical`-Ziele (siehe 8.2). |
| Testdatei nach dem Lauf weg | Gewollt: sbench löscht selbst angelegte Testdateien. `--keep-files` angeben (Abschnitt 5.1). |
| Exit-Code **2** und `errors` mit `path` | Job ungültig. `sbench validate --job <datei>` zeigt Fehler mit JSON-Pointer (z. B. `/tests/0/access_specs/0/block_size`). |
| `E_DESTRUCTIVE_NOT_ALLOWED` | Schreibtest auf ein Volume oder eine ganze Disk. Für M1-Abnahme nicht nötig, alle Abnahme-Jobs nutzen Testdateien. |
| `E_TARGET_NOT_FOUND` | Laufwerk oder Ordner existiert nicht: Pfad in der Job-Datei anpassen (Abschnitt 4). |
| `E_BLOCK_ALIGN` | Blockgröße passt nicht zur Sektorgröße (4Kn-Disk mit 512-Byte-Blöcken). Abnahme-Jobs sind 4K-ausgerichtet. |
| Anlegen der Testdatei dauert Minuten | Ohne Adminrechte bzw. ohne `SeManageVolumePrivilege` wird die Datei vollständig beschrieben. Einmalig abwarten (Fortschritt im Event `progress`). Mit `--keep-files` bleibt sie liegen und wird beim nächsten Lauf wiederverwendet. |
| `W_LOW_DISK_SPACE` bei `validate` | Weniger als 1 GiB bzw. 5 % frei nach dem Anlegen. Anderes Laufwerk wählen oder Dateigröße verkleinern (dann auch bei DiskSpd `-c` anpassen). |
| Windows SmartScreen / Defender blockiert `sbench.exe` | „Weitere Informationen → Trotzdem ausführen“ bzw. Datei-Eigenschaften → „Zulassen“. Die EXE ist (noch) nicht signiert. |
| Lauf hängt / reagiert nicht | `sbench list --running` zeigt laufende Läufe, `sbench stop --id <run_id>` beendet sie sauber (Exit 3, Teilergebnis mit `aborted: true`). |
| Abweichung > 5 % zu DiskSpd | Beide noch einmal laufen lassen. Prüfen: Energieplan, Virenscanner-Ausnahme, Hintergrundlast (Task-Manager), andere VMs, gleiche Testdatei und gleiche QD/Threads. Bleibt die Abweichung, Bundle trotzdem schicken und im Ticket notieren. |
| Wo finde ich Details? | Log jedes Laufs: `data\logs\<run_id>.log` (UTC-Zeitstempel, Windows-Fehlertexte). Fortschritt: `data\runs\<run_id>.jsonl`. Beides ist im Bundle enthalten. |

Nach der Abnahme die Testdateien löschen: `del D:\sbtest.dat D:\sbtest1.dat E:\sbtest2.dat`
und bei Iometer `D:\iobw.tst` (sbench löscht Testdateien nicht automatisch).
