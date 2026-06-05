@echo off
echo ========================================
echo   pypoc NSE Paper Trading Quick Start
echo ========================================
echo.
echo Step 1: Running preflight check...
call .venv\Scripts\activate.bat
python cli.py preflight
echo.
echo Step 2: Checking gate status...
python cli.py check-gate
echo.
echo Step 3: Showing current status...
python cli.py status
echo.
echo Dashboard: http://localhost:8502
echo To start agent: scripts\service_manager.bat start-agent
pause
