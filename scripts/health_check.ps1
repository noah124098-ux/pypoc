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

# ── 5. pypoc-dashboard service ───────────────────────────────────────────────
try {
    $dashSvc = sc.exe query pypoc-dashboard 2>&1 | Out-String
    $dashRunning = $dashSvc -match "RUNNING"
    $result.checks.service_dashboard = [ordered]@{
        ok      = $dashRunning
        message = if ($dashRunning) { "pypoc-dashboard is RUNNING" } else { "pypoc-dashboard is NOT running" }
    }
    if (-not $dashRunning) {
        $result.warnings += "pypoc-dashboard not running"
    }
} catch {
    $result.checks.service_dashboard = [ordered]@{ ok = $false; message = "Could not query pypoc-dashboard: $_" }
    $result.warnings += "pypoc-dashboard query failed"
}

# ── 6. pypoc-mcp service ─────────────────────────────────────────────────────
try {
    $mcpSvc = sc.exe query pypoc-mcp 2>&1 | Out-String
    $mcpRunning = $mcpSvc -match "RUNNING"
    $result.checks.service_mcp = [ordered]@{
        ok      = $mcpRunning
        message = if ($mcpRunning) { "pypoc-mcp is RUNNING" } else { "pypoc-mcp is NOT running" }
    }
    if (-not $mcpRunning) {
        $result.warnings += "pypoc-mcp not running"
    }
} catch {
    $result.checks.service_mcp = [ordered]@{ ok = $false; message = "Could not query pypoc-mcp: $_" }
    $result.warnings += "pypoc-mcp query failed"
}

# ── 7. Last Telegram notification (within last 60 min) ───────────────────────
$logPath2 = Join-Path $RepoRoot "logs\agent.log"
if (Test-Path $logPath2) {
    try {
        # Read the last 500 lines to find a recent Telegram entry
        $lines       = Get-Content $logPath2 -Tail 500
        $telegramLine = $lines | Where-Object { $_ -match "Telegram" } | Select-Object -Last 1
        if ($telegramLine) {
            # Try to extract an ISO timestamp from the log line (format: 2026-06-02T...)
            $tsMatch = [regex]::Match($telegramLine, '\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}')
            if ($tsMatch.Success) {
                $telegramTs  = [DateTime]::Parse($tsMatch.Value)
                $telegramAge = [int]((Get-Date) - $telegramTs).TotalMinutes
                $telegramOk  = $telegramAge -lt 60
                $result.checks.telegram = [ordered]@{
                    ok          = $telegramOk
                    age_minutes = $telegramAge
                    message     = if ($telegramOk) { "Last Telegram msg $telegramAge min ago" } else { "Last Telegram msg $telegramAge min ago (>60 min)" }
                }
                if (-not $telegramOk) {
                    $result.warnings += "Telegram silent for $telegramAge min"
                }
            } else {
                $result.checks.telegram = [ordered]@{ ok = $true; message = "Telegram entries found (timestamp unparseable)" }
            }
        } else {
            $result.checks.telegram = [ordered]@{ ok = $false; message = "No Telegram entries found in last 500 log lines" }
            $result.warnings += "No recent Telegram activity"
        }
    } catch {
        $result.checks.telegram = [ordered]@{ ok = $false; message = "Error reading agent.log: $_" }
    }
} else {
    $result.checks.telegram = [ordered]@{ ok = $true; message = "agent.log not found — skipping Telegram check" }
}

# ── 8. Gate validity (backtest_gate.json <= 30 days old) ─────────────────────
$gatePath = Join-Path $RepoRoot "data\backtest_gate.json"
$gateValid = $false
$gateAgeDays = $null
if (Test-Path $gatePath) {
    try {
        $gateContent = Get-Content $gatePath -Raw | ConvertFrom-Json
        # Accept either .run_date or .timestamp field
        $gateDateStr = if ($gateContent.run_date) { $gateContent.run_date } elseif ($gateContent.timestamp) { $gateContent.timestamp } else { $null }
        if ($gateDateStr) {
            $gateDate    = [DateTime]::Parse($gateDateStr)
            $gateAgeDays = [int]((Get-Date) - $gateDate).TotalDays
            $gateValid   = $gateAgeDays -le 30
        }
        $result.checks.gate = [ordered]@{
            ok       = $gateValid
            age_days = $gateAgeDays
            message  = if ($gateValid) { "Gate is $gateAgeDays days old (valid)" } elseif ($null -eq $gateAgeDays) { "Could not parse gate date" } else { "Gate is $gateAgeDays days old (>30 — stale)" }
        }
        if (-not $gateValid) {
            $result.warnings += if ($null -eq $gateAgeDays) { "gate date unparseable" } else { "Gate stale: $gateAgeDays days" }
        }
    } catch {
        $result.checks.gate = [ordered]@{ ok = $false; message = "Error reading backtest_gate.json: $_" }
        $result.warnings += "gate file parse error"
    }
} else {
    $result.checks.gate = [ordered]@{ ok = $false; message = "backtest_gate.json not found" }
    $result.warnings += "gate file missing"
}

# ── 9. Backup freshness (most recent backup dir < 25 hours old) ──────────────
$backupRoot = Join-Path $RepoRoot "backups"
if (Test-Path $backupRoot) {
    try {
        $latestBackup = Get-ChildItem -Path $backupRoot -Directory |
                        Sort-Object LastWriteTime -Descending |
                        Select-Object -First 1
        if ($latestBackup) {
            $backupAgeHours = [math]::Round(((Get-Date) - $latestBackup.LastWriteTime).TotalHours, 1)
            $backupFresh    = $backupAgeHours -lt 25
            $result.checks.backup_freshness = [ordered]@{
                ok         = $backupFresh
                age_hours  = $backupAgeHours
                latest_dir = $latestBackup.Name
                message    = if ($backupFresh) { "Latest backup '$($latestBackup.Name)' is ${backupAgeHours}h old" } else { "Latest backup '$($latestBackup.Name)' is ${backupAgeHours}h old (>25h — backup may have missed)" }
            }
            if (-not $backupFresh) {
                $result.healthy = $false
                $result.warnings += "Backup stale: ${backupAgeHours}h (latest: $($latestBackup.Name))"
            }
        } else {
            $result.checks.backup_freshness = [ordered]@{ ok = $false; message = "Backup directory exists but is empty — no backups found" }
            $result.healthy = $false
            $result.warnings += "No backup directories found under backups\"
        }
    } catch {
        $result.checks.backup_freshness = [ordered]@{ ok = $false; message = "Error scanning backup directory: $_" }
        $result.warnings += "backup scan error"
    }
} else {
    $result.checks.backup_freshness = [ordered]@{ ok = $false; message = "Backup root '$backupRoot' not found — backup_data.bat has not run yet" }
    $result.healthy = $false
    $result.warnings += "Backup root missing: $backupRoot"
}

# ── Summarised service map (for quick top-level access) ──────────────────────
$result["services"] = [ordered]@{
    dashboard = $result.checks.service_dashboard.ok
    mcp       = $result.checks.service_mcp.ok
}
$result["snapshot_age"]  = if ($result.checks.snapshot.age_sec) { $result.checks.snapshot.age_sec } else { $null }
$result["gate_valid"]    = $gateValid
$result["backup_fresh"]  = if ($result.checks.backup_freshness) { $result.checks.backup_freshness.ok } else { $false }

# ── Output ───────────────────────────────────────────────────────────────────
$json = $result | ConvertTo-Json -Depth 5
Write-Output $json

if (-not $result.healthy) {
    exit 1
}
exit 0
