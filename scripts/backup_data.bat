@echo off
set BACKUP_DIR=C:\Users\Administrator\pypoc_backups\%date:~10,4%-%date:~4,2%-%date:~7,2%
mkdir "%BACKUP_DIR%" 2>nul
xcopy /E /I /Q data\agent.db "%BACKUP_DIR%\"
xcopy /E /I /Q data\backtest_gate.json "%BACKUP_DIR%\"
xcopy /E /I /Q config\default.yaml "%BACKUP_DIR%\"
echo Backup complete: %BACKUP_DIR%
