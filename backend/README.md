# Backend

FastAPI service for auth, backend registry, metric ingestion, dashboard data, quick status tiles, notification history, system settings, and Telegram integration.

## Responsibilities

- Exposes the main API used by the React frontend.
- Polls registered monitor agents and stores metric snapshots in MySQL.
- Computes dashboard ranges, warnings, reboot markers, and quick status summaries.
- Manages users, auth sessions, Telegram settings, notification event history, and optional reboot actions.
- Sends Telegram commands and alert notifications, including process-heavy CPU/memory incident summaries.

## Main entrypoints

- App: `backend.app.main:app`
- Docker image: [backend/Dockerfile](/Users/martin/code/server_monitor/backend/Dockerfile)
- Tests: [backend/tests](/Users/martin/code/server_monitor/backend/tests)

## API surface

Router prefixes:

- `/auth`
- `/backends`
- `/metrics`
- `/dashboard`
- `/notifications`
- `/system`
- `/telegram`

Meta endpoints:

- `GET /healthz`
- `GET /version`

## Environment

The service reads `SERVER_MONITOR_*` variables.

Required for normal operation:

- `SERVER_MONITOR_DB_USER`
- `SERVER_MONITOR_DB_PASSWORD`
- `SERVER_MONITOR_DB_HOST`
- `SERVER_MONITOR_DB_PORT`
- `SERVER_MONITOR_DB_NAME`
- `SERVER_MONITOR_AUTH_SECRET_KEY`

Common optional variables:

- `SERVER_MONITOR_DEBUG`
- `SERVER_MONITOR_AUTH_ACCESS_TOKEN_EXP_MINUTES`
- `SERVER_MONITOR_CORS_ALLOW_ORIGINS`
- `SERVER_MONITOR_MONITOR_REQUEST_TIMEOUT_SECONDS`
- `SERVER_MONITOR_TELEGRAM_BOT_TOKEN`
- `SERVER_MONITOR_TELEGRAM_DEFAULT_CHAT_ID`
- `SERVER_MONITOR_TELEGRAM_ALLOWED_USERS`
- `SERVER_MONITOR_ALLOW_HOST_REBOOT`
- `SERVER_MONITOR_REBOOT_COMMAND`

Notes:

- CORS defaults to `http://localhost:5173` and `http://127.0.0.1:5173`.
- Database tables are created on startup, and schema compatibility checks run during app lifespan.
- `/backends/with-latest` is the admin-oriented backend listing that includes the latest snapshot payload used for monitor-version display.

## Run locally

Install dependencies:

```bash
python -m pip install -r backend/requirements.txt
```

Start the API from the repository root:

```bash
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

The service expects a reachable MySQL instance matching the configured `SERVER_MONITOR_DB_*` values.

## Run with Docker

Build and run only the backend service:

```bash
docker compose up -d --build backend db
```

Default exposed port in the full stack compose file: `28000`.

## Tests

From the repository root:

```bash
python -m pip install -r requirements-dev.txt
pytest -q backend/tests
```

## Code layout

- [backend/app/main.py](/Users/martin/code/server_monitor/backend/app/main.py): app factory and lifespan
- [backend/app/core](/Users/martin/code/server_monitor/backend/app/core): settings and security
- [backend/app/routers](/Users/martin/code/server_monitor/backend/app/routers): HTTP routes
- [backend/app/services](/Users/martin/code/server_monitor/backend/app/services): polling, ingest, warnings, Telegram, reboot logic
- [backend/app/db](/Users/martin/code/server_monitor/backend/app/db): DB session and schema helpers
- [backend/app/models](/Users/martin/code/server_monitor/backend/app/models): SQLAlchemy models
- [backend/app/bot](/Users/martin/code/server_monitor/backend/app/bot): Telegram polling bot
