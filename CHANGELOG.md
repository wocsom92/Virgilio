# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Removed
- Project tabs and the auto-seeded Self-monitoring project (quick tiles now always belong to a backend).
- The bundled Prometheus container; Prometheus quick tiles still query any external Prometheus URL.

### Fixed
- Frontend build is now type-checked (`tsc --noEmit && vite build`) and the test runner provides an in-memory `localStorage` under Node 22+.

## [8.0.0] - 2026-04-20

### Added
- **Quick-status alert confirmation:** configurable minimum time (default **5 minutes**, `0` = immediate) that a warning or error must persist before the tile switches to warn/critical styling and before automatic **Telegram** and **notification center** alerts are emitted. Configure under **Admin → Telegram → Alert confirmation (minutes)**.

### Fixed
- Quick-status **24-hour history strips** under tiles could fall out of sync with the live tile after an issue cleared (summary updates refreshed status but reused cached strip data, which looked like reversed or stale colors). The dashboard now reloads history when tile **status**, **display value**, or **stale** state changes from polling.

### Changed
- Project version updated from `7.1.0` to `8.0.0` across backend, frontend, monitor, README, package metadata, changelog, and displayed UI version constants.

## [7.1.0] - 2026-04-08

### Added
- Added built-in Prometheus scraping for the local `backend` and `monitor` services, including exported HTTP request counters by service, route, status code, and status class.
- Added an auto-seeded `Self-monitoring` project tab with graph cards for backend/monitor API call volume and last-hour `200`, `4xx`, and `5xx` response totals.

### Changed
- Notification Center entries now display as compact summary rows showing severity, delivery status, date, and title. Clicking a row expands it to reveal the full body and delivery error details.
- Quick tiles now show a loading indicator during the initial fetch instead of briefly flashing "No quick tiles configured".
- Ping and Prometheus quick-status checks now run concurrently on the backend, reducing dashboard load time when many network-checked tiles are configured.
- Prometheus dashboard cards now support explicit display units such as `%`, including a dedicated humidity-card style and visible low/high/current value labels on the embedded 24-hour graphs.
- Project version updated from `7.0.1` to `7.1.0` across backend, frontend, monitor, README, changelog, and displayed UI version constants.

## [7.0.1] - 2026-04-05

### Fixed
- Temperature and humidity quick-status graph cards now stay visible when the 24-hour series is flat or contains only isolated samples, which fixes Raspberry Pi sensor cards that looked empty despite valid Prometheus data.

### Changed
- Project version updated from `7.0.0` to `7.0.1` across backend, frontend, monitor, README, package metadata, changelog, and displayed UI version constants.

## [7.0.0] - 2026-04-03

### Added
- Monitoring and project dashboards now support mixed overview items in one grid, combining `1x1` quick tiles with wide `4x2` Prometheus graph cards in the same backend or project section.
- Prometheus overview items now expose persisted 24-hour series data, summary statistics, and a dedicated temperature-card variant tuned for room/apartment climate tracking.

### Changed
- Admin quick-status configuration now lets Prometheus items choose between quick-tile, graph-card, and temperature-card display styles instead of treating all overview items as square tiles.
- Prometheus cache keys now include endpoint/query identity, and heartbeat-history aggregation now models freshness windows correctly instead of overstating stale time.
- Project version updated from `6.0.3` to `7.0.0` across backend, frontend, monitor, deployment env, README, package metadata, changelog, and displayed UI version constants.

### Added
- Quick status tiles can now query Prometheus instant metrics through the Prometheus HTTP API, with configurable thresholds, threshold direction, refresh interval, and persisted 24-hour tile history.
- Projects can now be created as separate sidebar tabs, and each project can have its own configured quick-status tile set independent from the main Monitoring tab.

## [6.0.3] - 2026-03-29

### Changed
- Telegram delivery paths can now be routed through a dedicated proxy URL for both backend notifications and the polling bot, which fixes Raspberry Pi deployments where Telegram is blocked on the direct ISP path but reachable through local Xray/VPN egress.
- Site Monitoring HTTP targets entered without a scheme are now normalized to `https://...`, and malformed targets now record a normal failed check result instead of crashing the backend polling loop.
- Backend site-monitor polling now logs per-site check failures with monitor id/name/type/target context, so stalled history gaps are visible immediately in backend logs instead of looking silent.
- Raspberry Pi deployment defaults now advertise the current monitor version and Telegram proxy wiring.
- Project version updated from `6.0.2` to `6.0.3` across backend, frontend, monitor, deployment env, README, changelog, and displayed UI version constants.

## [6.0.2] - 2026-03-28

### Changed
- Site Monitoring now uses a fixed 30-minute check cadence and 48 half-hour history rectangles for each site, so one row always represents a full 24-hour day.
- Site Monitoring rows were compacted for iPhone and other narrow screens by hiding target, type, status text, response value, and last-check details until the row is expanded.
- Failed SSH quick tiles now expose parsed failure details on click, including auth method, username, source IP, port, and the raw log line.
- Notification Center entries for failed SSH quick-status alerts now include the same parsed SSH failure details in the stored local event body.
- Dashboard, auth, site monitoring, and admin surfaces now share one visual system based on the same translucent panel treatment used by the left navigation.
- Frontend Nginx now resolves the backend service dynamically so `/api` proxying survives backend container IP changes after restarts.
- Project version updated from `6.0.1` to `6.0.2` across backend, frontend, package metadata, README, changelog, and displayed UI version constants.

## [6.0.1] - 2026-03-28

### Added
- Dedicated Site Monitoring navigation and admin configuration for ping and HTTP GET checks with timeout-based success criteria, consecutive-failure warning/error thresholds, and a live per-site status list.

### Changed
- Site Monitoring rows are now more compact and use 24 vertical hourly history rectangles per site instead of the original wider history strip.
- Ping site checks now fall back to a backend-native TCP reachability probe when the host does not provide the `ping` binary or ICMP permission.
- Legacy `site_monitors` threshold columns are now normalized during schema compatibility startup so upgraded databases no longer block site-monitor saves.
- Root documentation now reflects the Site Monitoring feature set and hourly 24-hour history display.
- Project version updated from `6.0.0` to `6.0.1` across backend, frontend, monitor, and displayed UI version constants.

## [6.0.0] - 2026-03-28

### Added
- Quick status tiles now show a horizontal 24-hour history strip made of 12 aggregated two-hour segments under each square tile.
- Ping quick-status checks are now persisted in the backend so ping tiles can build correct historical state bars instead of showing only gray history.

### Changed
- Dashboard quick-status history is now aggregated by the longest-duration state inside each 2-hour bucket, with the rightmost segment representing the most recent completed 2-hour window.
- Quick-status heartbeat (`HB`) tiles are now built server-side alongside metric and ping tiles so dashboard and admin previews share one tile contract.
- Quick-status overview polling now refreshes every 5 minutes for the new 24-hour history strip.
- Dashboard quick tiles now stay compact on larger desktop layouts while preserving the existing mobile/iPhone sizing.
- Notification Center severity labels now use orange for `warn` and red for `critical` so alert states match the main tile colors more closely.
- Frontend login sessions are now persisted across browser restarts, and the default auth session lifetime for new logins is now 48 hours.
- Project version updated from `5.0.3` to `6.0.0` across backend, frontend, monitor, and displayed UI version constants.

## [5.0.3] - 2026-03-26

### Added
- Quick status alert transitions are now recorded as local notification-history events so they appear in Notification Center even when Telegram delivery is skipped or unavailable.

### Changed
- Notification Center history now treats local quick-status events as first-class history entries instead of showing current alerts in a separate section.
- Admin quick-status reorder arrows now stack below each tile on narrow iPhone layouts so they no longer block the tile preview.
- Project version updated from `5.0.2` to `5.0.3` across backend, frontend, monitor, and displayed UI version constants.

## [5.0.2] - 2026-03-17

### Added
- In-app notification center with unread badge, notification history, and fallback visibility when Telegram delivery is blocked or fails.
- Successful SSH login notifications and richer SSH failure alert details in Telegram.
- Dedicated Telegram notifications for newly detected unsuccessful SSH logins, including parsed failure details.
- Telegram `/cpu <server>` and `/memory <server>` commands for live usage lookups.
- Telegram CPU and memory alerts now include the top 10 highest-usage processes.
- Notification logging API and UI fallback delivery history.

### Changed
- Project version updated from `5.0.1` to `5.0.2` across backend, frontend, monitor, and displayed UI version constants.
- Notification center history is now paginated, and notification retention now follows the same configurable data-retention window used for metrics.
- SSH failure detection now also treats pre-auth disconnect/reset log lines as unsuccessful login attempts, so key-less attempts on key-only hosts appear in the UI and notifications.
- Dashboard and admin UI received mobile-oriented quick-tile layout refinements, admin-only version visibility, admin-only backend address visibility, and a heartbeat (`HB`) freshness tile in the overview grid.
- Deployment script now runs `sudo docker system prune -f` after a successful remote deployment.
- Successful SSH login notifications now use a concrete timestamp instead of relative age wording, and raw SSH success log lines are no longer included in Telegram messages.
- Hidden backend cards were compacted, and header actions/layout were refined so `Show/Hide`, `Refresh`, and hidden state indicators stay aligned cleanly.
- Root and component documentation were refreshed for the release, including screenshot embedding, contribution guidance, and MIT licensing.

### Security
- Backend registry read routes that expose monitor connection details are now admin-only.
- Stored monitor and Telegram secrets are masked in API read responses and no longer echoed back into admin forms.
- Admin secret fields now preserve existing values when left blank during edits instead of overwriting them.
- Frontend auth tokens now use `sessionStorage` instead of `localStorage`, with one-time migration of legacy stored sessions.
- Backend CORS handling no longer falls back to wildcard origins with credentialed requests enabled.
- Monitor bearer-token verification now uses constant-time comparison.

### Performance
- Backend monitor and Telegram outbound HTTP calls now reuse pooled async clients instead of creating a new client per request.
- Dashboard/admin latest-snapshot queries now fetch only the newest snapshot per backend instead of loading full snapshot histories.
- Monitor metric collection now caches top-process and SSH snapshot data briefly to reduce repeated expensive work during rapid polling.

## [4.3.5] - 2026-03-16

### Added
- Telegram SSH failure alerts now include parsed details such as auth method, username, source IP, port, and the related log line when available.

### Changed
- Project version updated from `4.3.4` to `4.3.5` across backend, frontend, monitor, and displayed UI version constants.

## [4.3.4] - 2026-03-13

### Added
- Telegram now supports `/cpu <server_name>` and `/memory <server_name>` commands that return live usage for the selected server.

### Changed
- Project version updated from `4.3.3` to `4.3.4` across backend, frontend, monitor, and displayed UI version constants.

## [4.3.3] - 2026-03-13

### Added
- Telegram CPU and memory alerts now include the top 10 processes by usage for the affected backend.

### Changed
- Project version updated from `4.3.2` to `4.3.3` across backend, frontend, monitor, and displayed UI version constants.

## [4.3.2] - 2026-03-09

### Changed
- Quick status admin search now lives inside the paginated Existing Tiles section instead of the separate Overview Preview block.
- Project version updated from `4.3.1` to `4.3.2` across backend, frontend, monitor, and displayed UI version constants.

## [4.3.1] - 2026-03-09

### Changed
- Admin quick status now has a separate Overview-style preview section in addition to the existing paginated management list.
- Quick status reorder mode is now server-scoped with a single edit button per server section.
- Mobile quick status reordering now includes compact arrow controls that work reliably on iPhone Safari.
- Clickable SSH quick status tiles now open their fix guidance more reliably on iPhone Safari by using native button interactions.

## [4.3.0] - 2026-03-09

### Added
- Clickable SSH quick status tiles now expose actionable fix guidance instead of only showing `WARN` or `CRIT`.
- Telegram settings now include a dedicated alert batch window so multiple issues raised close together can be grouped into one notification.

### Changed
- `/stats` now shows the same warning and error entries as `/warn`, keeping both Telegram commands consistent.
- Automatic quick-status alerts now wait for the configured batch window before the first send, while cooldown starts only after a message is delivered.
- Quick status free-space values now use rounded IEC units such as `223 GiB` instead of fractional decimal `GB`.
- Temperature formatting now uses `°C` consistently.
- Dashboard, admin quick-status preview, and other storage displays now use `KiB` / `MiB` / `GiB` / `TiB`.
- Admin quick-status ordering now uses the real tile preview with drag-and-drop on the tiles themselves.

## [4.2.0] - 2026-03-07

### Added
- Quick status tile for mounted volume free space with dynamic `MiB` / `GiB` / `TiB` display.
- Quick status admin search for existing tiles by server, label, metric, mount path, or ping target.
- Per-server quick status pagination in the admin panel with 5 tiles per page.

### Changed
- Quick status server ordering is now stable across dashboard refreshes.
- Informational quick status tiles now use the same green styling as `ok` tiles.
- SSH last successful and failed login tiles now use compact uptime-style formatting such as `5d 23h`.
- Quick status admin sections are now collapsible per server.
- Root (`/`) mounted free-space quick tiles now fall back to root disk metrics when mounted volume data is unavailable.
- Monitor Docker build now uses a multi-stage image so the final runtime stays smaller while still compiling `psutil` on ARM.

### Improved
- SSH login age collection is more resilient on systemd-based hosts by using host-root journal and host command fallbacks.
- Quick status free-space tiles now work more reliably on Raspberry Pi and similar hosts where root disk data is not listed in mounted volume metrics.

## [4.1.0] - 2026-03-07

### Added
- Configurable Telegram notification cooldown in the admin panel.
- Automatic Telegram recovery notifications when a quick status warning or error clears.
- Deferred quick status alert delivery after cooldown expiry when the issue is still active.
- Informational-only quick status state for swap tiles.
- Targeted backend test coverage for quick status notification queueing and recovery flows.

### Changed
- Swap quick status tiles no longer produce warning or error states and are now informational only.
- Telegram quick status notifications are now rate-limited by a configurable cooldown window.
- Quick status alerts that occur during cooldown are queued and delivered later instead of being dropped.
- Queued alerts are automatically canceled if the warning or error clears before cooldown expires.
- Dashboard styling now includes a dedicated informational quick tile appearance.
- Project version updated from `4.0.1` to `4.1.0` across backend, frontend, monitor, and displayed UI version constants.

### Improved
- Reduced Telegram alert noise for unstable or rapidly changing quick status conditions.
- Better operational visibility by notifying both incident start and incident recovery.
