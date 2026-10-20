# Release-Prozess

Releases baut und veröffentlicht `.github/workflows/release.yml`. Der Workflow baut
Kern und GUI aus **einem** Commit, prüft die Versionen und legt ein GitHub-Release mit
Prüfsummen an. `build-windows.yml` veröffentlicht nichts mehr selbst.

## Ablauf im Workflow

| Job | Inhalt |
|---|---|
| `meta` | Modus bestimmen: **veröffentlichen** bei Push eines Tags `v*` oder bei manuellem Start mit `dry_run = false` (nur von einem Tag aus), sonst **Vorschau**. |
| `core` | `uses: ./.github/workflows/build-windows.yml` (Build x64/ARM64, Unit-Tests, Smoke-Tests auf allen Runnern, Server-Core-Container). Bei einem Tag wird die Version `<tag ohne v>` als Input übergeben. Outputs: `version`, `commit`, `cli_artifact_x64`, `cli_artifact_arm64`. |
| `gui` | `needs: core`, `uses: ./.github/workflows/build-gui.yml` (von der GUI, Devfornt) mit `version: needs.core.outputs.version` und `core_artifact: needs.core.outputs.cli_artifact_x64` (= `sbench-cli-x64`). Erzeugt das Artifact `sbench-gui-x64` mit `sbench-gui.exe`. |
| `verify` | Auf `windows-latest` (x64) und `windows-11-arm` (ARM64): `sbench version --json` ausführen. `version` muss dem Tag ohne `v` und dem Output von `core` entsprechen, `commit` dem gebauten Commit, `arch` der Architektur, `sha256` dem Hash der Datei. |
| `publish` | `needs: [core, gui, verify]`. Assets umbenennen, `SHA256SUMS.txt` erzeugen, Release-Notes bestimmen. Vorschau: alles als Artifact `release-preview`. Veröffentlichen: `gh release create`. |

### Assets

| Datei | Inhalt |
|---|---|
| `sbench-<tag>-windows-x64.zip` | Artifact `sbench-windows-x64`: `sbench.exe`, `sbcore.dll`, `lib\sbcore.lib`, `include\sb\sb.h`, Doku, Abnahme-Jobs, Lizenzen, `VERSION.txt` |
| `sbench-<tag>-windows-arm64.zip` | dasselbe für ARM64 |
| `sbench-<tag>-cli-x64.zip` | nur `sbench.exe` (x64) |
| `sbench-<tag>-cli-arm64.zip` | nur `sbench.exe` (ARM64) |
| `sbench-gui-<tag>-x64.zip` | Inhalt des Artifacts `sbench-gui-x64` (`sbench-gui.exe`) |
| `SHA256SUMS.txt` | SHA-256 aller ZIP-Dateien im Format von `sha256sum` (prüfen mit `sha256sum -c SHA256SUMS.txt` oder `Get-FileHash`) |

Den Hash der entpackten `sbench.exe` liefert zusätzlich `sbench version --json` (`sha256`).

### Release-Notes und Pre-release

- Gibt es `docs/release-notes/<tag>.md` (z. B. `docs/release-notes/v0.1.0-beta.1.md`), ist
  sie der Release-Text. Sonst erzeugt GitHub die Notizen (`--generate-notes`, Liste der PRs
  seit dem letzten Release).
- Enthält der Tag `-alpha`, `-beta` oder `-rc`, wird das Release als **Pre-release** markiert.
- Titel: `sbench <tag>`.
- Existiert das Release zum Tag schon, bricht `publish` ab und überschreibt nichts.

## Release erstellen (Checkliste)

1. Alle PRs für das Release sind in `main` gemergt (Merge macht der PM), die CI auf `main`
   ist grün.
2. `docs/release-notes/v<version>.md` anlegen bzw. aktualisieren (per PR nach `main`).
   Vorlage: die vorherige Datei; Abschnitte *Neu*, *Geändert*, *Bekannte Einschränkungen*,
   *Prüfsummen* (Verweis auf `SHA256SUMS.txt`).
3. **Probelauf ohne Tag:** Actions → **Release** → *Run workflow* auf `main`,
   `dry_run` angehakt. Ergebnis im Artifact `release-preview`: `assets/` (alle ZIPs und
   `SHA256SUMS.txt`), `notes.md`, `release.json` (Tag, Version, Pre-release, Quelle der
   Notizen). Ohne Tag heißt der Vorschau-Tag `v<version aus git describe>`, und die
   Notizen kommen aus `--generate-notes`.
4. Tag setzen und pushen (SemVer, mit `v`):

   ```sh
   git checkout main && git pull
   git tag -a v0.1.0-beta.1 -m "sbench v0.1.0-beta.1"
   git push origin v0.1.0-beta.1
   ```

   Der Push startet `release.yml` und veröffentlicht automatisch. Alternativ: Tag pushen,
   dann *Run workflow* **auf dem Tag** mit `dry_run` angehakt (Vorschau mit der echten
   Version) und anschließend noch einmal mit `dry_run` ausgeschaltet. Der Push-Lauf
   veröffentlicht dann bereits; der zweite Lauf bricht ab, weil das Release existiert.
5. Release-Seite prüfen: Assets vollständig, `SHA256SUMS.txt` stimmt
   (`sha256sum -c SHA256SUMS.txt`), Pre-release-Markierung, Notizen.

### Fehlerfälle

| Meldung | Ursache / Abhilfe |
|---|---|
| `version … passt nicht zum Tag` | Die eingebaute Version weicht vom Tag ab. Tag prüfen (Format `v<semver>`), ggf. löschen und neu setzen. |
| `Veröffentlichen nur von einem Tag v* aus` | Manueller Start mit `dry_run = false` auf einem Branch. Nur Vorschau ist dort möglich. |
| `Release … existiert bereits` | Das Release gibt es schon. Löschen (Release und Tag) oder eine neue Version taggen. |
| Job `gui` schlägt mit „workflow not found“ fehl | `build-gui.yml` liegt noch nicht in diesem Commit. Er kommt mit dem GUI-Integrations-PR, vorher ist kein Release möglich. |
| `sbench-gui-x64 enthält keine sbench-gui.exe` | `build-gui.yml` hat ein anderes Artifact geliefert. Vertrag: Artifact `sbench-gui-x64` mit `sbench-gui.exe`. |

## Versionen

- Die Kern-Version kommt aus dem Tag (`v0.1.0-beta.1` → `0.1.0-beta.1`). Ohne Tag nutzt
  `build-windows.yml` `git describe` (z. B. `0.1.0-beta.1-3-gabc1234`), ohne Tags im
  Repository `0.1.0-ci.<lauf>+<sha>`.
- `sbench version` (erste Zeile) und `sbench version --json` (`version`) geben diese Version aus,
  ebenso `kern_version` in Ergebnissen und Bundles.
- Die GUI bekommt dieselbe Version über den Input `version` von `build-gui.yml`.
