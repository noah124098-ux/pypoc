@echo off
echo === pypoc Windows Services ===
if "%1"=="start" (
    nssm start pypoc-dashboard
    nssm start pypoc-mcp
    echo Services started.
) else if "%1"=="stop" (
    nssm stop pypoc-dashboard
    nssm stop pypoc-mcp
    echo Services stopped.
) else if "%1"=="status" (
    nssm status pypoc-dashboard
    nssm status pypoc-mcp
    nssm status pypoc-agent
) else if "%1"=="start-agent" (
    nssm start pypoc-agent
    echo Agent started.
) else if "%1"=="stop-agent" (
    nssm stop pypoc-agent
    echo Agent stopped.
) else (
    echo Usage: service_manager.bat [start^|stop^|status^|start-agent^|stop-agent]
)
