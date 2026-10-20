#!/usr/bin/env python3
"""Prüft von container_smoke.ps1 erzeugte Dateien gegen die Schemas (auf dem Host)."""
import hashlib
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import smoke  # noqa: E402

out = Path(sys.argv[1])


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8-sig"))


smoke.validate(load(out / "info.json"), "sb-info-1.schema.json", "info.json")
smoke.validate(load(out / "validate.json"), "sb-validate-1.schema.json", "validate.json")
if (out / "runs.json").exists():
    smoke.validate(load(out / "runs.json"), "sb-runs-1.schema.json", "runs.json (list --running --json)")
smoke.check_result(out / "result.json", "result.json", expect_aborted=False)
smoke.check_progress(smoke.read_jsonl(out / "progress.jsonl"), "progress.jsonl", expect_aborted=False)
if (out / "result-m2.json").exists():
    smoke.check_result(out / "result-m2.json", "result-m2.json (cpu, memory)", expect_aborted=False)
    smoke.check_progress(smoke.read_jsonl(out / "progress-m2.jsonl"), "progress-m2.jsonl", expect_aborted=False)
else:
    smoke.check(False, "result-m2.json fehlt")
with zipfile.ZipFile(out / "bundle.zip") as z:
    man = json.loads(z.read("manifest.json"))
    smoke.validate(man, "sb-bundle-1.schema.json", "manifest.json")
    smoke.check(all(hashlib.sha256(z.read(f["path"])).hexdigest() == f["sha256"] for f in man["files"]), "sha256")
print(f"{len(smoke.FAILS)} Fehler")
sys.exit(1 if smoke.FAILS else 0)
