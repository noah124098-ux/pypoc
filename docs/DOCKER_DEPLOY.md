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

## Backup

SQLite database and snapshots live in `data/`. Back up with:

```bash
tar -czf pypoc-data-$(date +%F).tar.gz /opt/pypoc/data/
```
