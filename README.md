# Virgilio - System Monitoring

Current version: `4.3.1`

Virgilio is a full-stack monitoring suite built with FastAPI, React, MySQL, and Docker Compose.

It has three runtime parts:

- `frontend/`: React dashboard + admin console.
- `backend/`: FastAPI API (auth, backend registry, metric snapshots, quick status, system settings, Telegram integration).
- `monitor/`: lightweight FastAPI agent that collects host metrics via `psutil`.

## Features

- Multi-backend monitoring from one dashboard.
- Per-backend metric selection, including:
  - CPU temperature
  - RAM used %
  - Memory available (GiB)
  - Swap used %
  - Disk used %
  - Mount usage (selected mount points)
  - CPU load averages (1/5/15)
  - Network throughput (derived from interface counters)
- Time-range charts (`hourly`, `daily`, `weekly`) with previous/next window navigation.
- Reboot markers on charts (detected from uptime resets).
- Quick status tiles with thresholds and statuses (`ok`, `info`, `warn`, `critical`, `unknown`).
- Quick status tile types include:
  - Mounted volume usage (%)
  - Mounted volume free space (`MiB` / `GiB` / `TiB`)
  - SSH login age and SSH posture
- SSH quick status support:
  - Last successful SSH login (compact elapsed time such as `5d 23h`, always `ok`)
  - Last failed SSH attempt (elapsed age with configurable warn/critical thresholds)
  - SSH posture (`PubkeyAuthentication` and `PermitRootLogin`) mapped to `ok`/`warn`/`critical`
- Admin quick status management supports search, a separate Overview-style preview section, and a paginated management list.
- Quick status preview reordering is server-scoped and includes touch-friendly arrow controls for iPhone Safari.
- Role-based auth (`admin`, `viewer`) with bootstrap flow for first admin user.
- Admin controls for retention days and auth session duration.
- Telegram bot support (`/stats`, `/warn`, reboot actions) with consistent warning output across `/stats` and `/warn`.
- Optional host reboot support (requires explicit enablement + container privileges).

## Prerequisites

- Docker with Compose plugin.
- For deployment script usage: `ssh`, `sftp`, `rsync`, and `sshpass` (when using password auth).

## Configuration

### Full stack (`docker-compose.yml`)

Create a root `.env` (or use host-specific `.env.*`) with at least:

- `MYSQL_ROOT_PASSWORD`
- `SERVER_MONITOR_DB_USER`
- `SERVER_MONITOR_DB_PASSWORD`
- `SERVER_MONITOR_DB_HOST`
- `SERVER_MONITOR_DB_PORT`
- `SERVER_MONITOR_DB_NAME`
- `SERVER_MONITOR_AUTH_SECRET_KEY`
- `SERVER_MONITOR_AUTH_ACCESS_TOKEN_EXP_MINUTES`
- `SERVER_MONITOR_CORS_ALLOW_ORIGINS`
- `MONITOR_API_TOKEN`

Common optional values:

- `SERVER_MONITOR_ALLOW_HOST_REBOOT`
- `SERVER_MONITOR_REBOOT_COMMAND`
- `MONITOR_MOUNTED_POINTS`
- `MONITOR_EXPOSE_DOCKER_RUNNING_CONTAINERS`
- `MONITOR_HOST_ROOT_SOURCE`
- `MONITOR_HOST_ROOT_TARGET`
- `MONITOR_HISTORY_RETENTION_SECONDS`
- `MONITOR_HISTORY_MAX_ENTRIES`
- `MONITOR_ALLOW_HOST_REBOOT`
- `MONITOR_REBOOT_COMMAND`
- `VITE_API_BASE_URL`

Notes:

- In production frontend builds, if `VITE_API_BASE_URL` is not set, frontend uses same-origin `/api` (proxied by frontend Nginx to `backend:8000`).
- `docker-compose.yml` currently passes `VITE_API_BASE_URL` build arg with default `http://localhost:28000`. Set it explicitly for your target domain/port, or set it to `/api` for same-origin routing.
- In development, frontend defaults to `http://localhost:8000`.
- SSH quick status checks require host filesystem bind mount (set `MONITOR_HOST_ROOT_SOURCE=/` and keep `MONITOR_HOST_ROOT_TARGET=/hostfs`).

### Monitor-only (`docker-compose.monitor.yml`)

Use `monitor/.env.example` as baseline for monitor agent variables:

- `MONITOR_API_TOKEN`
- `MONITOR_MOUNTED_POINTS`
- `MONITOR_HOST_ROOT_SOURCE`
- `MONITOR_HOST_ROOT_TARGET`
- `MONITOR_HISTORY_RETENTION_SECONDS`
- `MONITOR_HISTORY_MAX_ENTRIES`
- `MONITOR_ALLOW_HOST_REBOOT`
- `MONITOR_REBOOT_COMMAND`

Optional HTTPS reverse proxy is provided by `docker-compose.monitor.nginx.yml`.

## Run with Docker Compose

### Full stack

```bash
docker compose build
docker compose up -d
```

Default exposed ports:

- Frontend: `http://localhost:5173`
- Backend API: `http://localhost:28000`
- Monitor sample agent: `http://localhost:29000`

First startup flow:

1. Open frontend.
2. Create bootstrap admin account.
3. Add monitored backends in admin panel (for local compose, monitor URL is typically `http://monitor:9000` from backend container perspective).

### Monitor only

```bash
docker compose -f docker-compose.monitor.yml up -d monitor
```

With HTTPS proxy:

```bash
docker compose -f docker-compose.monitor.yml -f docker-compose.monitor.nginx.yml up -d
```

## Remote deployment (`scripts/deploy.sh`)

The deploy helper uploads selected files over SSH/SFTP and runs remote Docker Compose.

1. Create config:

```bash
cp scripts/deploy.targets.env.example scripts/deploy.targets.env
```

2. Fill target values in `scripts/deploy.targets.env`:

- `TARGET_<name>_HOST`
- `TARGET_<name>_PORT`
- `TARGET_<name>_USER`
- `TARGET_<name>_DEPLOY_PATH`
- `TARGET_<name>_PROFILE` (`full` or `monitor-only`)
- `TARGET_<name>_AUTH` (`password` or `key`)
- Optional: `TARGET_<name>_ENV_FILE`
- Optional: `TARGET_<name>_DOCKER_USE_SUDO`
- Optional: `TARGET_<name>_PURGE_MODE` (`managed` or `full`)

3. Deploy:

```bash
bash scripts/deploy.sh --target raspi5 --profile full
bash scripts/deploy.sh --target czech --profile monitor-only
```

Behavior summary:

- `full` profile deploys backend + frontend + monitor + compose files.
- `monitor-only` profile deploys monitor + monitor compose files.
- `managed` purge mode removes only managed files in deploy path.
- `full` purge mode removes entire deploy path before upload.

## API overview

Main backend router prefixes:

- `/auth`
- `/backends`
- `/metrics`
- `/dashboard`
- `/system`
- `/telegram`

Meta endpoints:

- `GET /healthz`
- `GET /version`

## Local tests

```bash
python -m pip install -r requirements-dev.txt
npm --prefix frontend install
```

```bash
pytest -q backend/tests monitor/tests
npm --prefix frontend test -- --run
```

## Project structure

```text
backend/
  app/
    core/        # settings + auth/security utilities
    db/          # SQLAlchemy session and compatibility helpers
    models/      # ORM models
    routers/     # auth, backends, dashboard, metrics, system, telegram
    services/    # ingest, monitor client, reboot, quick status, telegram notifications
    bot/         # telegram polling bot
    main.py
  tests/
monitor/
  app/
    config.py
    metrics.py
    storage.py
    main.py
    schemas.py
  tests/
frontend/
  src/
    api/
    components/
    constants/
    hooks/
    styles/
scripts/
  deploy.sh
  deploy.targets.env.example
```
