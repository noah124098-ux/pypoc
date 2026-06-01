@echo off
cd /d C:\Users\Administrator\pypoc
call .venv\Scripts\activate.bat
python cli.py mcp-server >> logs\mcp.log 2>&1
