# Changelog

All notable changes to this project will be documented in this file.

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
