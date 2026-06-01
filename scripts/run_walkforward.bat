@echo off
cd /d C:\Users\Administrator\pypoc
call .venv\Scripts\activate.bat
python cli.py walk-forward --years 3 --end-date 2026-05-29
python cli.py check-gate
