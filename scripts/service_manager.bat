@echo off
echo === pypoc Windows Services ===
if "%1"=="start" (
    nssm start pypoc-dashboard
    nssm start pypoc-api
    nssm start pypoc-react
    nssm start pypoc-mcp
    echo Services started.
    echo   Streamlit: http://localhost:8501
    echo   React:     http://localhost:8503
    echo   API:       http://localhost:8502
) else if "%1"=="stop" (
    nssm stop pypoc-dashboard
    nssm stop pypoc-api
    nssm stop pypoc-react
    nssm stop pypoc-mcp
    echo Services stopped.
) else if "%1"=="status" (
    echo --- Service Status ---
    nssm status pypoc-dashboard
    nssm status pypoc-api
    nssm status pypoc-react
    nssm status pypoc-mcp
    nssm status pypoc-agent
) else if "%1"=="start-agent" (
    nssm start pypoc-agent
    echo Agent started.
) else if "%1"=="stop-agent" (
    nssm stop pypoc-agent
    echo Agent stopped.
) else if "%1"=="restart" (
    nssm restart pypoc-dashboard
    nssm restart pypoc-api
    nssm restart pypoc-react
    echo Core services restarted.
) else (
    echo Usage: service_manager.bat [start^|stop^|status^|restart^|start-agent^|stop-agent]
    echo.
    echo Services:
    echo   pypoc-dashboard  Streamlit UI   :8501  ^(AUTO^)
    echo   pypoc-react      React UI        :8503  ^(AUTO^)
    echo   pypoc-api        FastAPI backend :8502  ^(AUTO^)
    echo   pypoc-mcp        MCP server      stdio  ^(AUTO^)
    echo   pypoc-agent      Trading agent   bg     ^(MANUAL^)
)
