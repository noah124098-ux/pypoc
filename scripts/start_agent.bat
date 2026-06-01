@echo off
cd /d C:\Users\Administrator\pypoc
call .venv\Scripts\activate.bat
echo Starting NSE Trading Agent...
python cli.py run >> logs\agent.log 2>&1
