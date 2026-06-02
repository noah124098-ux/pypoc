@echo off
REM weekly_report.bat — email weekly performance + strategy report
REM Registered in Task Scheduler as "pypoc-WeeklyReport" (Sun 09:00)

cd /d C:\Users\Administrator\pypoc

REM Ensure logs directory exists
if not exist logs mkdir logs

echo === Weekly Report: %DATE% %TIME% === >> logs\weekly_report.log 2>&1

call .venv\Scripts\activate.bat >> logs\weekly_report.log 2>&1

echo --- 7-day performance --- >> logs\weekly_report.log 2>&1
python cli.py performance --days 7 >> logs\weekly_report.log 2>&1

echo --- 30-day strategy report --- >> logs\weekly_report.log 2>&1
python cli.py strategy-report --days 30 >> logs\weekly_report.log 2>&1

echo === Done === >> logs\weekly_report.log 2>&1
