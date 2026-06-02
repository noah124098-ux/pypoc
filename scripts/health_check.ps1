# health_check.ps1 — structured health check for pypoc on Windows EC2
# Returns a JSON object suitable for monitoring tools and exits with:
#   0 = healthy
#   1 = one or more warnings/errors

param(
    [string]$RepoRoot = "C:\Users\Administrator\pypoc"
)

$result = [ordered]@{
    ts          = (Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ")
    healthy     = $true
    warnings    = @()
    checks      = [ordered]@{}
}

# ── 1. Agent snapshot freshness ─────────────────────────────────────────────
$snapshotPath = Join-Path $RepoRoot "data\snapshot.json"
if (Test-Path $snapshotPath) {
    try {
        $snapshot  = Get-Content $snapshotPath -Raw | ConvertFrom-Json
        $ts        = [DateTime]::Parse($snapshot.ts)
        $ageSec    = [int]((Get-Date) - $ts).TotalSeconds
        $stale     = $ageSec -gt 300
        $result.checks.snapshot = [ordered]@{
            ok      = -not $stale
            age_sec = $ageSec
            message = if ($stale) { "Snapshot is $ageSec seconds old (>300)" } else { "Fresh ($ageSec s)" }
        }
        if ($stale) {
            $result.healthy = $false
            $result.warnings += "Snapshot stale: $ageSec s"
        }
    } catch {
        $result.checks.snapshot = [ordered]@{ ok = $false; message = "Parse error: $_" }
        $result.healthy = $false
        $result.warnings += "snapshot parse error"
    }
} else {
    $result.checks.snapshot = [ordered]@{ ok = $false; message = "snapshot.json not found — agent not running?" }
    $result.warnings += "snapshot.json missing"
    # Not fatal for the other checks; healthy flag left untouched here
}

# ── 2. Disk space (warn if < 1 GB free on C:) ───────────────────────────────
try {
    $disk      = Get-PSDrive -Name C
    $freeGB    = [math]::Round($disk.Free / 1GB, 2)
    $lowDisk   = $freeGB -lt 1
    $result.checks.disk_space = [ordered]@{
        ok        = -not $lowDisk
        free_gb   = $freeGB
        message   = if ($lowDisk) { "Only ${freeGB} GB free on C: — BELOW 1 GB threshold" } else { "${freeGB} GB free" }
    }
    if ($lowDisk) {
        $result.healthy = $false
        $result.warnings += "Low disk: ${freeGB} GB"
    }
} catch {
    $result.checks.disk_space = [ordered]@{ ok = $false; message = "Could not query disk: $_" }
}

# ── 3. Memory usage (warn if > 80 % used) ───────────────────────────────────
try {
    $os        = Get-CimInstance Win32_OperatingSystem
    $totalMB   = [math]::Round($os.TotalVisibleMemorySize / 1024, 0)
    $freeMB    = [math]::Round($os.FreePhysicalMemory      / 1024, 0)
    $usedPct   = [math]::Round(100 * ($totalMB - $freeMB) / $totalMB, 1)
    $highMem   = $usedPct -gt 80
    $result.checks.memory = [ordered]@{
        ok       = -not $highMem
        used_pct = $usedPct
        free_mb  = $freeMB
        total_mb = $totalMB
        message  = if ($highMem) { "Memory ${usedPct}% used — ABOVE 80% threshold" } else { "Memory ${usedPct}% used" }
    }
    if ($highMem) {
        $result.healthy = $false
        $result.warnings += "High memory: ${usedPct}%"
    }
} catch {
    $result.checks.memory = [ordered]@{ ok = $false; message = "Could not query memory: $_" }
}

# ── 4. Log file size (warn if agent.log > 100 MB) ────────────────────────────
$logPath = Join-Path $RepoRoot "logs\agent.log"
if (Test-Path $logPath) {
    $sizeMB  = [math]::Round((Get-Item $logPath).Length / 1MB, 2)
    $bigLog  = $sizeMB -gt 100
    $result.checks.log_size = [ordered]@{
        ok       = -not $bigLog
        size_mb  = $sizeMB
        path     = $logPath
        message  = if ($bigLog) { "agent.log is ${sizeMB} MB — rotation recommended (run rotate_logs.bat)" } else { "agent.log is ${sizeMB} MB" }
    }
    if ($bigLog) {
        $result.healthy = $false
        $result.warnings += "Log too large: ${sizeMB} MB"
    }
} else {
    $result.checks.log_size = [ordered]@{ ok = $true; message = "logs\agent.log not found (agent not started yet)" }
}

# ── Output ───────────────────────────────────────────────────────────────────
$json = $result | ConvertTo-Json -Depth 5
Write-Output $json

if (-not $result.healthy) {
    exit 1
}
exit 0
