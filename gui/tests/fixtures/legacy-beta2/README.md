# Fixture: Benutzerdaten von sbench-gui v0.1.0-beta.2

Stand eines Rechners, auf dem vorher v0.1.0-beta.1/-beta.2 lief (Profile mit den Speicherfunktionen der Betas angelegt,
Ergebnisse vom echten beta.1-/beta.2-Kern aus den Release-Läufen). Anonymisiert mit `tools/anon_fixture.py`:
Host `DEMO-HOST-01`, IPs aus 192.0.2.0/24 bzw. 2001:db8::/32, Benutzer `DEMO-USER` – keine echten Namen/Adressen.

| Pfad | entspricht auf Windows |
|---|---|
| `LocalAppData/sbench-gui/profiles/*.json` | `%LOCALAPPDATA%\sbench-gui\profiles\` (gespeicherte sb-job/1-Profile, u. a. eigene Kopie von »Mix 60/40«, die beim Start das mitgelieferte Profil ersetzt) |
| `LocalAppData/sbench-gui/results/*.json` | `%LOCALAPPDATA%\sbench-gui\results\` (sb-result/1, Kern 0.1.0-beta.1/-beta.2, teils mit UTF-8-BOM, ein abgebrochener Lauf) |
| `LocalAppData/sbench-gui/logs/` | `%LOCALAPPDATA%\sbench-gui\logs\gui-YYYYMMDD.log` |
| `data/runs/index.jsonl`, `data/jobs/` | `data\` neben `sbench-gui.exe` (bzw. `%ProgramData%\sbench`) |

`../legacy-broken/` enthält zusätzlich unbrauchbare Dateien (leer, abgeschnitten, BOM + Liste, fremdes Schema), wie sie ein
während eines Laufs beendeter Rechner hinterlassen kann. Beide Sätze nutzen pytest (`tests/test_legacy_config.py`) und
der CI-Schritt »Start-Test der EXE mit Altdaten« (`.github/workflows/build-gui.yml`).
