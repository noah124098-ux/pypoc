# Check agent is running and snapshot is fresh
$snapshot = Get-Content "data\snapshot.json" | ConvertFrom-Json
$ts = [DateTime]::Parse($snapshot.ts)
$age = (Get-Date) - $ts
if ($age.TotalMinutes -gt 5) {
    Write-Host "WARNING: Snapshot is $($age.TotalMinutes.ToString("F0")) minutes old"
    exit 1
}
Write-Host "OK: Agent running, last tick $($age.TotalSeconds.ToString("F0"))s ago"
exit 0
