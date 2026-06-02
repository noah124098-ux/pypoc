@echo off
REM Run this after reboot to start Docker and deploy pypoc containers
echo Starting Docker Engine...
C:\ProgramData\chocolatey\bin\dockerd.exe --register-service 2>/dev/null
net start docker
timeout /t 10

echo Building and starting pypoc containers...
cd /d C:\Users\Administrator\pypoc
C:\ProgramData\chocolatey\bin\docker.exe compose up -d --build

echo Done! Dashboard at http://localhost:8501
C:\ProgramData\chocolatey\bin\docker.exe compose ps
