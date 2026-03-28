# Frontend

React + Vite dashboard for the monitoring suite. It handles bootstrap/login, backend administration, charts, quick status tiles, notification history, and system settings.

## Responsibilities

- Auth flow for bootstrap admin, login, and persistent current-session state.
- Dashboard views for backend health, metrics charts, warnings, and quick status tiles.
- Admin UI for monitored backends, users, Telegram settings, notification history, and retention/session settings.
- Notification center with unread counter, Telegram delivery fallback inbox, and severity badges aligned with tile colors.
- Responsive quick-tile layout, including the `HB` heartbeat freshness tile, a 12-segment 24-hour history strip under each overview tile, and tighter sizing on desktop without changing the mobile/iPhone presentation.
- Admin-only visibility for backend addresses and version metadata.
- API access through a single Axios client.

## Main entrypoints

- App bootstrap: [frontend/src/main.tsx](/Users/martin/code/server_monitor/frontend/src/main.tsx)
- Root component: [frontend/src/App.tsx](/Users/martin/code/server_monitor/frontend/src/App.tsx)
- API client: [frontend/src/api/client.ts](/Users/martin/code/server_monitor/frontend/src/api/client.ts)
- Docker image: [frontend/Dockerfile](/Users/martin/code/server_monitor/frontend/Dockerfile)

## Environment

Frontend build/runtime behavior is controlled mainly by `VITE_API_BASE_URL`.

Behavior by default:

- In development: falls back to `http://localhost:8000`
- In production build: falls back to same-origin `/api`

Examples:

- `VITE_API_BASE_URL=http://localhost:28000`
- `VITE_API_BASE_URL=/api`

## Run locally

Install dependencies:

```bash
npm --prefix frontend install
```

Start the dev server:

```bash
npm --prefix frontend run dev
```

Default Vite dev URL is typically `http://localhost:5173`.

## Build

```bash
npm --prefix frontend run build
```

The frontend currently displays:

- Dashboard-only operational views for backend cards, charts, quick tiles, and heartbeat freshness.
- Quick tiles now display a backend-provided 24-hour status history strip, including persisted ping history.
- Notification Center severity pills now render `warn` in orange and `critical` in red.
- Auth tokens are kept in `localStorage`, so login sessions survive tab and browser restarts until the backend session expires.
- Admin-only metadata such as frontend/backend/monitor versions and backend addresses.

Preview the production bundle:

```bash
npm --prefix frontend run preview
```

## Run with Docker

Build and run only the frontend service:

```bash
docker compose up -d --build frontend
```

In the full stack compose file it is served by Nginx on port `5173`.

## Tests

From the repository root:

```bash
npm --prefix frontend test -- --run
```

## Code layout

- [frontend/src/components](/Users/martin/code/server_monitor/frontend/src/components): dashboard and admin UI
- [frontend/src/api](/Users/martin/code/server_monitor/frontend/src/api): typed client and request helpers
- [frontend/src/hooks](/Users/martin/code/server_monitor/frontend/src/hooks): reusable hooks
- [frontend/src/utils](/Users/martin/code/server_monitor/frontend/src/utils): chart/mount formatting helpers
- [frontend/src/styles](/Users/martin/code/server_monitor/frontend/src/styles): theme styles
