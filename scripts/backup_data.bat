@echo off
REM Daily backup of agent state. Scheduled 06:00 via Task Scheduler (pypoc-DailyBackup).
REM WAL checkpoint first so the .db file is complete (WAL sidecar holds recent writes).
setlocal
cd /d C:\Users\Administrator\pypoc

set BACKUP_DIR=C:\Users\Administrator\pypoc_backups\%date:~10,4%-%date:~4,2%-%date:~7,2%
mkdir "%BACKUP_DIR%" 2>nul

REM Flush the WAL into the main DB file so the backup captures all committed trades.
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('data/agent.db'); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()" 2>nul
)

REM copy /Y overwrites without prompting; works on single files (xcopy /E does not).
copy /Y data\agent.db "%BACKUP_DIR%\agent.db" >nul
if exist data\agent.db-wal copy /Y data\agent.db-wal "%BACKUP_DIR%\agent.db-wal" >nul
if exist data\backtest_gate.json copy /Y data\backtest_gate.json "%BACKUP_DIR%\backtest_gate.json" >nul
if exist config\default.yaml copy /Y config\default.yaml "%BACKUP_DIR%\default.yaml" >nul

REM Verify the DB actually copied (non-zero size); fail loudly if not.
if not exist "%BACKUP_DIR%\agent.db" (
    echo BACKUP FAILED: agent.db not copied to %BACKUP_DIR%
    exit /b 1
)
for %%F in ("%BACKUP_DIR%\agent.db") do if %%~zF EQU 0 (
    echo BACKUP FAILED: agent.db is zero bytes in %BACKUP_DIR%
    exit /b 1
)

REM Prune backup dirs older than 45 days (keeps a full 30-day proof window + buffer).
forfiles /p C:\Users\Administrator\pypoc_backups /d -45 /c "cmd /c if @isdir==TRUE rmdir /s /q @path" 2>nul

echo Backup complete: %BACKUP_DIR%
endlocal
