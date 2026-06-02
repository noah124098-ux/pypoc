# Production Dockerfile for the NSE pypoc trading agent
# Stage: single-stage build on python:3.12-slim
FROM python:3.12-slim

# System-level build dependencies (gcc required by some pip packages, e.g. ta-lib extensions)
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

# Lightweight health check: validates config loads + core imports (exit 0 = healthy)
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python cli.py health-check

# Default: run the Streamlit dashboard in headless/server mode
CMD ["streamlit", "run", "dashboard.py", \
     "--server.headless=true", \
     "--server.enableCORS=false", \
     "--server.enableXsrfProtection=false", \
     "--server.port=8501", \
     "--server.address=0.0.0.0"]
