# refresh_gate.ps1 — Weekly gate refresh script for NSE trading agent
# Run directly or via Windows Task Scheduler (refresh_gate.bat wrapper).

param(
    [string]$EndDate = "2026-05-29",
    [int]$Years = 3
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot   = "C:\Users\Administrator\pypoc"
$LogDir     = Join-Path $RepoRoot "logs"
$RefreshLog = Join-Path $LogDir "gate_refresh.log"
$FailureLog = Join-Path $LogDir "gate_failures.log"
$Python     = Join-Path $RepoRoot ".venv\Scripts\python.exe"

# Ensure logs directory exists
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $ts = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    $line = "[$ts] [$Level] $Message"
    Write-Host $line
    Add-Content -Path $RefreshLog -Value $line
}

Write-Log "=== Gate refresh started (end-date=$EndDate, years=$Years) ==="

# ---------------------------------------------------------------------------
# 1. Run walk-forward
# ---------------------------------------------------------------------------
Write-Log "Running walk-forward..."
$wfArgs = @("cli.py", "walk-forward", "--years", $Years, "--end-date", $EndDate)
$wfOutput = & $Python @wfArgs 2>&1
$wfOutput | ForEach-Object { Add-Content -Path $RefreshLog -Value $_ }
$wfExit = $LASTEXITCODE

if ($wfExit -ne 0) {
    Write-Log "walk-forward process exited with code $wfExit" "ERROR"
    $failureTs = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    $failureEntry = @"
[$failureTs] GATE REFRESH FAILED
  Exit code : $wfExit
  End date  : $EndDate
  Years     : $Years
  See full output in: $RefreshLog
"@
    Add-Content -Path $FailureLog -Value $failureEntry
    Write-Log "Failure details written to $FailureLog" "ERROR"
    exit 1
}

Write-Log "Walk-forward completed."

# ---------------------------------------------------------------------------
# 2. Parse key metrics from walk-forward output
# ---------------------------------------------------------------------------
$sharpe  = $null
$maxdd   = $null
$winRate = $null
$pf      = $null

foreach ($line in $wfOutput) {
    if ($line -match "Sharpe\s*:\s*([-\d.]+)")       { $sharpe  = [double]$Matches[1] }
    if ($line -match "Max DD\s*:\s*([-\d.]+)")        { $maxdd   = [double]$Matches[1] }
    if ($line -match "Win rate\s*:\s*([-\d.]+)")      { $winRate = [double]$Matches[1] }
    if ($line -match "Profit factor\s*:\s*([-\d.]+)") { $pf      = [double]$Matches[1] }
}

$metricSummary = "Sharpe=$sharpe  MaxDD=$maxdd%  WinRate=$winRate%  PF=$pf"
Write-Log "Aggregate metrics: $metricSummary"

# ---------------------------------------------------------------------------
# 3. Read gate result (passed / failed)
# ---------------------------------------------------------------------------
$gateArgs   = @("cli.py", "check-gate")
$gateOutput = & $Python @gateArgs 2>&1
$gateOutput | ForEach-Object { Add-Content -Path $RefreshLog -Value $_ }

$gatePassed = ($gateOutput | Where-Object { $_ -match "Passed\s*:\s*True" }) -ne $null

# ---------------------------------------------------------------------------
# 4. Branch on pass / fail
# ---------------------------------------------------------------------------
if ($gatePassed) {
    Write-Log "GATE PASSED — $metricSummary" "INFO"
} else {
    # Collect failure reasons from check-gate output
    $failureLines = $gateOutput | Where-Object { $_ -match "^  X  " }
    $failureTs    = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    $failureEntry = @"
[$failureTs] GATE FAILED
  Metrics   : $metricSummary
  End date  : $EndDate
  Years     : $Years
  Failures:
"@
    foreach ($fl in $failureLines) {
        $failureEntry += "`n    $fl"
    }
    $failureEntry += "`n  See full walk-forward output in: $RefreshLog`n"

    Add-Content -Path $FailureLog -Value $failureEntry
    Write-Log "Gate FAILED. Failure details written to $FailureLog" "WARN"
}

# ---------------------------------------------------------------------------
# 5. Send Telegram notification with gate result
# ---------------------------------------------------------------------------
if ($null -ne $sharpe) {
    $sharpeFmt  = [string]$sharpe
    $passedFmt  = if ($gatePassed) { "True" } else { "False" }
    $notifyArgs = @("-c", "
import sys
sys.path.insert(0, r'$RepoRoot')
try:
    from core.config import Secrets
    from core.notifications.telegram import TelegramNotifier
    sec = Secrets.from_env()
    n = TelegramNotifier(sec.telegram_bot_token, sec.telegram_chat_id)
    n.send_gate_refresh(sharpe=$sharpeFmt, passed=$passedFmt)
except Exception as e:
    print(f'Telegram gate notify failed: {e}')
")
    & $Python @notifyArgs 2>&1 | ForEach-Object { Add-Content -Path $RefreshLog -Value $_ }
    Write-Log "Telegram gate notification sent."
}

# ---------------------------------------------------------------------------
# 6. Exit with appropriate code
# ---------------------------------------------------------------------------
if ($gatePassed) {
    Write-Log "=== Gate refresh finished SUCCESSFULLY ===" "INFO"
    exit 0
} else {
    Write-Log "=== Gate refresh finished with FAILURES ===" "WARN"
    exit 1
}
