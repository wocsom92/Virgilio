# Architecture

This folder documents the current Virgilio/server-monitor architecture from the codebase.

## Contents

- C4 system context diagram below.
- C4 container diagram below.
- [openapi-backend.yaml](openapi-backend.yaml): generated OpenAPI contract for `backend/app/main.py`.
- [openapi-monitor.yaml](openapi-monitor.yaml): generated OpenAPI contract for `monitor/app/main.py`.
- [database.md](database.md): database schemas, tables, relationships, retention notes, and Mermaid ER diagram.

## Runtime Summary

Virgilio is deployed as a Docker Compose stack with:

- React frontend served by Nginx.
- FastAPI backend for auth, dashboard data, admin configuration, metric history, notifications, and Telegram integration.
- FastAPI monitor agent for host metrics, ping checks, and optional host reboot.
- SQLite file (WAL mode) on a named volume for configuration and metric history.
- Optional Elasticsearch, Kibana, and Filebeat profile for container log collection.

The frontend calls the backend API. The backend polls monitor agents, ingests metric snapshots, executes quick-status checks, stores data in SQLite, and sends Telegram notifications when configured.

## C4 System Context

```mermaid
flowchart LR
    admin["Admin / Operator"]
    viewer["Viewer"]
    telegram["Telegram User"]
    telegramApi["Telegram Bot API"]
    monitoredHost["Monitored Host"]
    externalSite["External HTTP/Ping Targets"]

    system["Virgilio Monitoring System"]

    admin -->|"configures backends, quick status, projects, Telegram, retention"| system
    viewer -->|"views dashboards and site status"| system
    telegram -->|"bot commands: stats, warn, cpu, memory, reboot"| system
    system -->|"sends bot messages"| telegramApi
    system -->|"polls metrics, ping, optional reboot"| monitoredHost
    system -->|"checks reachability and HTTP responses"| externalSite

    classDef person fill:#f7f7f7,stroke:#555,color:#111
    classDef system fill:#e8f1ff,stroke:#2b5c9e,color:#111
    classDef external fill:#fff4df,stroke:#9e6b1f,color:#111

    class admin,viewer,telegram person
    class system system
    class telegramApi,monitoredHost,externalSite external
```

## C4 Container Diagram

```mermaid
flowchart TB
    user["Browser User"]
    telegramUser["Telegram User"]
    telegramApi["Telegram Bot API"]
    monitoredTargets["Monitor Agents / External Targets"]

    subgraph compose["Docker Compose Deployment"]
        frontend["Frontend\nReact + Nginx\nport 5173"]
        backend["Backend API + Telegram bot\nFastAPI\nport 8000 / host 28000"]
        monitor["Local Monitor Agent\nFastAPI\nport 9000 / host 29000"]
        db[("SQLite file\n/app/data/virgilio.db")]
        logs["Optional Logs Profile\nFilebeat + Elasticsearch + Kibana"]
    end

    user -->|"HTTPS/HTTP UI"| frontend
    frontend -->|"REST API, bearer JWT"| backend
    backend -->|"SQLAlchemy async"| db
    backend -->|"polls metrics, ping, reboot"| monitor
    backend -->|"polls remote monitor agents and site checks"| monitoredTargets
    backend -->|"Telegram notifications"| telegramApi
    backend -->|"Telegram long polling / replies (in-process bot)"| telegramApi
    telegramUser -->|"commands"| telegramApi
    logs -->|"collects container logs"| frontend
    logs -->|"collects container logs"| backend
    logs -->|"collects container logs"| monitor

    classDef app fill:#e8f1ff,stroke:#2b5c9e,color:#111
    classDef data fill:#e8ffe8,stroke:#2f7d32,color:#111
    classDef external fill:#fff4df,stroke:#9e6b1f,color:#111
    classDef optional fill:#f3edff,stroke:#6b43a8,color:#111

    class frontend,backend,monitor app
    class db data
    class user,telegramUser,telegramApi,monitoredTargets external
    class logs optional
```

## Regenerating OpenAPI

The OpenAPI files are generated directly from the FastAPI app objects:

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
import yaml

from backend.app.main import app as backend_app
from monitor.app.main import app as monitor_app

out = Path("architecture")
for filename, app in (
    ("openapi-backend.yaml", backend_app),
    ("openapi-monitor.yaml", monitor_app),
):
    schema = app.openapi()
    with (out / filename).open("w", encoding="utf-8") as fh:
        yaml.safe_dump(schema, fh, sort_keys=False, allow_unicode=False)
PY
```
