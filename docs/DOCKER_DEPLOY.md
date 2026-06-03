# Docker Deployment Guide

## Quick start (local)

```bash
cp .env.example .env          # fill in credentials
docker compose up -d
```

Dashboard: http://localhost:8501
Nginx proxy: http://localhost:80

---

## Production EC2 (Ubuntu)

**1. Provision** — Ubuntu 22.04 LTS, t3.medium or larger.
   Open inbound ports: 22 (SSH), 80 (HTTP), 443 (HTTPS).

**2. Run one-shot setup** (as root or with sudo):

```bash
curl -fsSL https://raw.githubusercontent.com/noah124098-ux/pypoc/main/deploy/setup_ec2_docker.sh | sudo bash
```

**3. Configure environment:**

```bash
cd /opt/pypoc
cp .env.example .env
nano .env   # fill in all required values
```

**4. Start services:**

```bash
bash deploy/start.sh
```

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `ANGEL_API_KEY` | Yes | Angel One SmartAPI key (data only) |
| `ANGEL_CLIENT_ID` | Yes | Angel One client ID |
| `ANGEL_PASSWORD` | Yes | Angel One password |
| `ANGEL_TOTP_SECRET` | Yes | TOTP secret for 2FA |
| `ANTHROPIC_API_KEY` | Yes | Claude API key for EOD reviewer |
| `TELEGRAM_TOKEN` | No | Telegram bot token for alerts |
| `TELEGRAM_CHAT_ID` | No | Telegram chat/channel ID |
| `EMAIL_SENDER` | No | SMTP sender address |
| `EMAIL_PASSWORD` | No | SMTP password |
| `EMAIL_RECIPIENT` | No | Report recipient address |

---

## Monitoring commands

```bash
bash deploy/logs.sh              # tail all container logs
docker compose ps                # check container status
docker compose exec agent python cli.py check-gate   # gate status
```

---

## Nginx basic auth (EC2 Windows)

The Streamlit dashboard runs on port 8501. nginx sits in front on port 80 and
requires HTTP Basic Authentication so the dashboard is not publicly accessible
without credentials.

### One-time setup

1. **Install nginx** (already done via Chocolatey on the EC2 instance):

   ```powershell
   choco install nginx -y
   ```

2. **Set your password** — run the setup script once:

   ```powershell
   cd C:\Users\Administrator\pypoc
   .\deploy\setup_nginx_auth.ps1 -Password "your_secure_password"
   ```

   This writes `deploy/.htpasswd` (which is git-ignored — never committed).
   The default user is `admin`; pass `-User yourname` to change it.

3. **Start nginx** with the project config:

   ```powershell
   nginx -c C:\Users\Administrator\pypoc\deploy\nginx.conf
   ```

4. **Reload after config changes** (no downtime):

   ```powershell
   nginx -s reload
   ```

5. **Stop nginx**:

   ```powershell
   nginx -s stop
   ```

### nginx.conf highlights

- `auth_basic` enabled on `location /` — all dashboard traffic requires login.
- `/healthz` has `auth_basic off` so monitoring scripts can hit it without creds.
- WebSocket upgrade headers are set so Streamlit's live reload works through the proxy.
- `proxy_read_timeout 86400s` keeps long-lived WebSocket connections alive.

### Security notes

- Change the default password (`changeme`) before opening port 80 inbound.
- For HTTPS, obtain a cert (Let's Encrypt or ACM) and uncomment the redirect in `nginx.conf`.
- The `.htpasswd` file is in `.gitignore` — never commit it to the repo.

---

## SSL/TLS setup

### Option A — Self-signed certificate (development / testing)

1. **Generate the cert** (requires OpenSSL on PATH):

   ```bat
   deploy\gen_self_signed_cert.bat
   ```

   This writes `deploy/pypoc.key` and `deploy/pypoc.crt` (both git-ignored).

2. **Copy certs to the nginx ssl directory:**

   ```powershell
   mkdir C:\tools\nginx-1.31.1\ssl
   copy deploy\pypoc.key C:\tools\nginx-1.31.1\ssl\pypoc.key
   copy deploy\pypoc.crt C:\tools\nginx-1.31.1\ssl\pypoc.crt
   ```

   Adjust the path if your nginx install differs.

3. **Start nginx with the SSL config:**

   ```powershell
   nginx -c C:\Users\Administrator\pypoc\deploy\nginx_ssl.conf
   ```

   The HTTP server now redirects all traffic to HTTPS.
   The dashboard proxies to port 8502 (FastAPI/Streamlit combined).

### Option B — Let's Encrypt (production Linux)

1. Install certbot:

   ```bash
   sudo apt install certbot python3-certbot-nginx -y
   ```

2. Obtain a cert (replace `your.domain.com`):

   ```bash
   sudo certbot --nginx -d your.domain.com
   ```

3. Update `nginx_ssl.conf`:
   - Set `server_name your.domain.com;` in both server blocks.
   - Replace the `ssl_certificate` paths with the paths certbot reports
     (typically `/etc/letsencrypt/live/your.domain.com/fullchain.pem` and
     `privkey.pem`).
   - Update `auth_basic_user_file` to your `.htpasswd` path.

4. Reload nginx:

   ```bash
   sudo nginx -s reload
   ```

   Certbot installs a cron/systemd timer to auto-renew the cert before expiry.

### nginx_ssl.conf highlights

- Port 80 issues a permanent 301 redirect to HTTPS — no plain-text traffic.
- TLSv1.2 and TLSv1.3 only; weak ciphers (`aNULL`, `MD5`) excluded.
- `auth_basic` on `location /` protects the dashboard behind HTTP Basic Auth.
- WebSocket upgrade headers are forwarded so Streamlit live-reload works over SSL.
- `proxy_read_timeout 86400` keeps long-lived WebSocket connections alive.

---

## Backup

SQLite database and snapshots live in `data/`. Back up with:

```bash
tar -czf pypoc-data-$(date +%F).tar.gz /opt/pypoc/data/
```
