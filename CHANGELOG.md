# Changelog

All notable changes to this project will be documented in this file.

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
