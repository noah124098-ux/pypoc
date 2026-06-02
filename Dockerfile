# Production Dockerfile for the NSE pypoc trading agent
# Windows Server Core with Python 3.12 — runs natively on Windows Docker Engine
# (Linux containers require Hyper-V or WSL2 which are unavailable on EC2 bare metal)
FROM python:3.12-windowsservercore-ltsc2022

WORKDIR /app

# --- Dependency layer (cached as long as requirements.txt is unchanged) ---
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip && \
    python -m pip install --no-cache-dir -r requirements.txt

# --- Application code ---
COPY . .

# Streamlit dashboard port
EXPOSE 8501

# Lightweight health check
HEALTHCHECK --interval=30s --timeout=15s --start-period=30s --retries=3 \
    CMD ["python", "cli.py", "health-check"]

# Default: run the Streamlit dashboard in headless/server mode
CMD ["python", "-m", "streamlit", "run", "dashboard.py", \
     "--server.headless=true", \
     "--server.enableCORS=false", \
     "--server.enableXsrfProtection=false", \
     "--server.port=8501", \
     "--server.address=0.0.0.0"]
