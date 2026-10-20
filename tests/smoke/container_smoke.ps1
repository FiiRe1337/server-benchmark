# CLI-Smoke in einem Windows-Container (Server Core). Kein Python im Image:
# Prüfung von Exit-Codes und JSON-Lesbarkeit hier, Schema-Prüfung danach auf dem Host
# (tests/smoke/check_files.py).
param([Parameter(Mandatory)] [string] $Exe, [Parameter(Mandatory)] [string] $Out)
$ErrorActionPreference = "Stop"
$fail = 0
function Check($cond, $msg) { if ($cond) { Write-Host "  [OK]   $msg" } else { Write-Host "  [FAIL] $msg"; $script:fail++ } }

New-Item -ItemType Directory -Force $Out | Out-Null
$bin = "C:\sbtest\bin"
New-Item -ItemType Directory -Force $bin | Out-Null
Copy-Item $Exe "$bin\sbench.exe"
$sb = "$bin\sbench.exe"

Write-Host "== Container: $((Get-CimInstance Win32_OperatingSystem).Caption) $([Environment]::OSVersion.Version)"
& $sb version; Check ($LASTEXITCODE -eq 0) "version"
& $sb info --json | Out-File -Encoding utf8 "$Out\info.json"; Check ($LASTEXITCODE -eq 0) "info --json"
$info = Get-Content -Raw "$Out\info.json" | ConvertFrom-Json
Write-Host "  OS: $($info.meta.os.name) $($info.meta.os.build); Hypervisor: role=$($info.meta.hypervisor.role) Quelle=$($info.meta.hypervisor.partition_info.role_source)"

$job = @{
  schema = "sb-job/1"; name = "Container-Smoke"
  tests = @(@{ type = "storage"; targets = @(@{ path = "C:\sbtest\sbtest.dat"; kind = "file"; file_size = "64MiB" })
              workers = 2; queue_depth = 8; warmup_s = 1; duration_s = 3; cache = "unbuffered"
              access_specs = @(@{ weight = 100; block_size = "4KiB"; read_pct = 70; random_pct = 100 }) })
}
$job | ConvertTo-Json -Depth 8 | Out-File -Encoding utf8 C:\sbtest\job.json
& $sb validate --job C:\sbtest\job.json | Out-File -Encoding utf8 "$Out\validate.json"; Check ($LASTEXITCODE -eq 0) "validate"
& $sb run --job C:\sbtest\job.json --out "$Out\result.json" --progress "file:$Out\progress.jsonl" --id container-run
Check ($LASTEXITCODE -eq 0) "run Exit 0 (ist $LASTEXITCODE)"
$r = Get-Content -Raw "$Out\result.json" | ConvertFrom-Json
$s = $r.tests[0].summary
Write-Host ("  Container-Ergebnis: IOPS {0:N0}, MB/s {1:N2}, p99 {2:N1} us" -f $s.iops, $s.mbps, $s.lat_us.p99)
Check ($s.io_count -gt 0) "I/Os gemessen"
# Meilenstein 2: cpu + memory im Container (Topologie, VirtualAllocExNuma, Pinning)
$job2 = @{
  schema = "sb-job/1"; name = "Container-M2"
  tests = @(@{ type = "cpu"; workload = "mixed"; warmup_s = 0.5; duration_s = 2 },
            @{ type = "memory"; mode = "bandwidth"; op = "copy"; buffer_size = "64MiB"; warmup_s = 0.5; duration_s = 2 },
            @{ type = "memory"; mode = "latency"; buffer_size = "256MiB"; warmup_s = 0.5; duration_s = 2 })
}
$job2 | ConvertTo-Json -Depth 8 | Out-File -Encoding utf8 C:\sbtest\job-m2.json
& $sb run --job C:\sbtest\job-m2.json --out "$Out\result-m2.json" --progress "file:$Out\progress-m2.jsonl"
Check ($LASTEXITCODE -eq 0) "run M2 (cpu, memory) Exit 0 (ist $LASTEXITCODE)"
if (Test-Path "$Out\result-m2.json") {
  $r2 = Get-Content -Raw "$Out\result-m2.json" | ConvertFrom-Json
  Write-Host ("  Container-Ergebnis M2: cpu mixed {0:N0} ops/s (Score {1:N0}, {2} Threads), copy {3:N2} GB/s, Latenz {4:N1} ns" -f `
    $r2.tests[0].summary.ops_per_s, $r2.tests[0].summary.score, $r2.tests[0].summary.threads, $r2.tests[1].summary.gbps, $r2.tests[2].summary.latency_ns)
  Check ($r2.tests.Count -eq 3 -and $r2.tests[0].summary.ops_per_s -gt 0 -and $r2.tests[1].summary.gbps -gt 0 -and $r2.tests[2].summary.latency_ns -gt 0) "M2-Messwerte > 0"
  Write-Host ("  NUMA im Container: " + (($info.meta.numa_nodes | ForEach-Object { "Knoten $($_.id): $($_.cpus.Count) CPUs, ram_method=$($_.ram_method)" }) -join "; "))
}
& $sb export "$Out\result.json" --csv "$Out\result.csv" --style iometer; Check ($LASTEXITCODE -eq 0) "export"
& $sb list --running --json | Out-File -Encoding utf8 "$Out\runs.json"; Check ($LASTEXITCODE -eq 0) "list --running --json"
& $sb bundle --all --anonymize --out "$Out\bundle.zip"; Check ($LASTEXITCODE -eq 0) "bundle --all --anonymize"
Check (Test-Path "$bin\data\logs\container-run.log") "Log im portablen Datenverzeichnis (data\ neben sbench.exe)"
Check (Test-Path "$bin\data\runs\container-run.jsonl") "Fortschrittsdatei runs\container-run.jsonl"
Check (-not (Test-Path "$bin\data\runs\container-run.lock.json")) "Lock-Datei nach Laufende entfernt"
Check ((Get-Content "$bin\data\runs\index.jsonl" | Select-String '"container-run"').Count -eq 1) "Eintrag in runs\index.jsonl"
Write-Host "$fail Fehler"
exit $fail
