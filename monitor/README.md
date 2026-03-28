# Monitor

Lightweight FastAPI agent that runs on a monitored host, collects local metrics with `psutil`, and exposes them to the backend over a bearer-token protected HTTP API.

## Responsibilities

- Collects host metrics on demand.
- Stores a short in-process history buffer for `latest` responses and chart continuity.
- Exposes network ping checks for quick status tiles.
- Exposes network ping checks that the backend can persist and aggregate into 24-hour quick-tile history bars.
- Collects process-heavy CPU and memory snapshots used in Telegram alert summaries and command responses.
- Collects SSH login and SSH posture details for quick status tiles and Telegram notifications.
- Optionally triggers a local host reboot when explicitly enabled.

## Main entrypoints

- App: `monitor.app.main:app`
- Config: [monitor/app/config.py](/Users/martin/code/server_monitor/monitor/app/config.py)
- Metrics collection: [monitor/app/metrics.py](/Users/martin/code/server_monitor/monitor/app/metrics.py)
- Storage: [monitor/app/storage.py](/Users/martin/code/server_monitor/monitor/app/storage.py)
- Docker image: [monitor/Dockerfile](/Users/martin/code/server_monitor/monitor/Dockerfile)

## API surface

- `GET /healthz`
- `GET /metrics`
- `GET /metrics/latest`
- `GET /ping`
- `POST /reboot`

All endpoints except `/healthz` require `Authorization: Bearer <MONITOR_API_TOKEN>`.

## Environment

The service reads `MONITOR_*` variables.

Required:

- `MONITOR_API_TOKEN`

Common optional variables:

- `MONITOR_DEBUG`
- `MONITOR_MOUNTED_POINTS`
- `MONITOR_EXPOSE_DOCKER_RUNNING_CONTAINERS`
- `MONITOR_HOST_ROOT_TARGET`
- `MONITOR_HISTORY_RETENTION_SECONDS`
- `MONITOR_HISTORY_MAX_ENTRIES`
- `MONITOR_ALLOW_HOST_REBOOT`
- `MONITOR_REBOOT_COMMAND`

Notes:

- `MONITOR_MOUNTED_POINTS` accepts JSON or comma-separated values. Default is `["auto"]`.
- SSH-related quick status checks need the host filesystem mounted into the container, usually at `/hostfs`.
- Detailed SSH parsing and process snapshots depend on host access being available inside the monitor container.

## Run locally

Install dependencies:

```bash
python -m pip install -r monitor/requirements.txt
```

Start the agent from the repository root:

```bash
uvicorn monitor.app.main:app --reload --host 0.0.0.0 --port 9000
```

## Run with Docker

Start the monitor-only compose profile:

```bash
docker compose -f docker-compose.monitor.yml up -d monitor
```

Optional HTTPS reverse proxy:

```bash
docker compose -f docker-compose.monitor.yml -f docker-compose.monitor.nginx.yml up -d
```

Default exposed port in the sample compose file: `29000`.

## Tests

From the repository root:

```bash
python -m pip install -r requirements-dev.txt
pytest -q monitor/tests
```

## Code layout

- [monitor/app/main.py](/Users/martin/code/server_monitor/monitor/app/main.py): routes and token verification
- [monitor/app/metrics.py](/Users/martin/code/server_monitor/monitor/app/metrics.py): host metric collection and reboot handling
- [monitor/app/storage.py](/Users/martin/code/server_monitor/monitor/app/storage.py): in-memory history repository
- [monitor/app/schemas.py](/Users/martin/code/server_monitor/monitor/app/schemas.py): API models
