@echo off
REM show_status.bat — one-liner status check for pypoc agent, gate, and services

cd /d C:\Users\Administrator\pypoc
call .venv\Scripts\activate.bat

python cli.py status
echo.
python cli.py check-gate
echo.
sc query pypoc-dashboard | findstr STATE
sc query pypoc-mcp | findstr STATE
