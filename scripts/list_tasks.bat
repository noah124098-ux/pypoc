@echo off
echo === pypoc Scheduled Tasks ===
schtasks /query /tn "NSE-Gate-Refresh" /fo list 2>nul | findstr "Next Run"
schtasks /query /tn "pypoc-WeeklyReport" /fo list 2>nul | findstr "Next Run"
schtasks /query /tn "pypoc-DailyBackup" /fo list 2>nul | findstr "Next Run"
schtasks /query /tn "pypoc-RotateLogs" /fo list 2>nul | findstr "Next Run"
