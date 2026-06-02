@echo off
cd /d C:\Users\Administrator\pypoc
echo Running weekly gate refresh at %date% %time%...
call .venv\Scripts\activate.bat
python cli.py walk-forward --years 3 --end-date 2026-05-29 >> logs\gate_refresh.log 2>&1
if %errorlevel% == 0 (
    echo Gate refresh completed successfully
    python cli.py check-gate >> logs\gate_refresh.log 2>&1
) else (
    echo Gate refresh FAILED - check logs/gate_refresh.log
)
