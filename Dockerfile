# Production Dockerfile for the NSE pypoc trading agent
#
# TARGET PLATFORMS:
#   Linux (EC2 Amazon Linux, Ubuntu) or WSL2:
#     docker build -t pypoc .          <- uses python:3.12-slim (default)
#
#   Windows Server (current EC2, no WSL2/Hyper-V):
#     Use NSSM Windows Services instead (see scripts/service_manager.bat)
#     Windows containers work but add 4GB base + 512MB overhead per service.
#
FROM python:3.12-slim

# System-level build dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- Dependency layer (cached as long as requirements.txt is unchanged) ---
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# --- Application code ---
COPY . .

# --- Non-root user for security hardening ---
RUN groupadd --system trader \
    && useradd --system --gid trader --no-create-home trader \
    && chown -R trader:trader /app

USER trader

# Streamlit dashboard port
EXPOSE 8501

# Lightweight health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python cli.py health-check

# Default: run the Streamlit dashboard
CMD ["python", "-m", "streamlit", "run", "dashboard.py", \
     "--server.headless=true", \
     "--server.enableCORS=false", \
     "--server.enableXsrfProtection=false", \
     "--server.port=8501", \
     "--server.address=0.0.0.0"]
