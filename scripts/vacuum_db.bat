@echo off
REM Monthly SQLite maintenance. Schedule 02:00 on the 1st via Task Scheduler.
REM WAL mode leaves freed pages in the file; without periodic VACUUM the DB only
REM grows. wal_checkpoint(TRUNCATE) first flushes + shrinks the -wal sidecar.
setlocal
cd /d C:\Users\Administrator\pypoc

if not exist .venv\Scripts\python.exe (
    echo VACUUM SKIPPED: venv python not found
    exit /b 1
)

.venv\Scripts\python.exe -c "import sqlite3, os; p='data/agent.db'; before=os.path.getsize(p); c=sqlite3.connect(p); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.execute('VACUUM'); c.close(); after=os.path.getsize(p); print(f'VACUUM ok: {before/1e6:.1f}MB -> {after/1e6:.1f}MB')"

echo Vacuum complete.
endlocal
