@echo off
cd /d C:\Users\Administrator\pypoc
:: Keep last 7 days of logs
forfiles /p logs /m *.log /d -7 /c "cmd /c del @path" 2>nul
forfiles /p logs /m *.jsonl /d -7 /c "cmd /c del @path" 2>nul
echo Log rotation complete
