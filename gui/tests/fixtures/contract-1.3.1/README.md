Vertrag 1.3.1 (PR #17, Commit a4287fa, noch nicht auf main) – nur als Test-Fixtures/Referenz, nicht gemergt:
`schema/` = Schemas aus #17 (inkl. optionalem `done.exit_code` 0–4), Beispiele aus `schema/examples/`:
`result-fatal.json` + `progress-fatal.jsonl` (Laufzeitfehler, `done.exit_code` 1),
`result-partial.json` + `progress-partial.jsonl` (Stopp, `done.exit_code` 3).
Nach dem Merge von #17 in main: Ordner löschen und die Tests auf `../schema` umstellen.
