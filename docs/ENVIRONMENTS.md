# Environments

## Overview

| Env     | Purpose                                    | Dashboard Port | Debug | DB                    |
|---------|--------------------------------------------|---------------|-------|-----------------------|
| dev     | Local development, small capital           | :8502         | yes   | data/agent_dev.db     |
| staging | Paper trading, production config           | :8501         | no    | data/agent_staging.db |
| prod    | Live trading — post-gate-pass only         | :8501         | no    | data/agent.db         |

## Usage

```bash
# Dev — interactive, logs to stdout
bash deploy/environments/dev/start.sh

# Staging — detached, full config
bash deploy/environments/staging/start.sh

# Prod — detached; gate check runs automatically before start
bash deploy/environments/prod/start.sh
```

CLI overrides:

```bash
python cli.py --env dev status
python cli.py --env staging walk-forward
APP_ENV=staging python cli.py run
```

## Environment Files

| File                  | Used by  | Notes                        |
|-----------------------|----------|------------------------------|
| `.env.dev.example`    | dev      | copy to `.env.dev`           |
| `.env.staging.example`| staging  | copy to `.env.staging`       |
| `.env.example`        | prod     | copy to `.env` (never commit)|

None of the `.env.*` files (populated) are committed to the repo.

## Database Isolation

- **dev:** `data/agent_dev.db`
- **staging:** `data/agent_staging.db`
- **prod:** `data/agent.db` (default)

Each environment writes its own SQLite file so dev experiments never
corrupt staging or production history.

## Prod Gate Requirement

`deploy/environments/prod/start.sh` calls `python cli.py check-gate` before
starting containers and aborts if the gate is not passing. This enforces the
project rule: no live trading until Sharpe >= 1.2 on a fresh walk-forward.
