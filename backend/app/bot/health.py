"""Health of the Telegram bot that runs inside the backend process.

The polling loop and the Telegram HTTP layer update this state; the quick-status
``telegram_bot_status`` tile reads it instead of guessing from container names (the separate
bot container disappeared in 9.0.0). Kept free of python-telegram-bot imports so the tile code
can import it cheaply.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

# A healthy bot long-polls getUpdates every ~10 s; allow a few misses before calling it stale.
POLL_STALE_AFTER = timedelta(minutes=2)


@dataclass(slots=True)
class BotHealth:
    enabled: bool = False
    """The bot is configured to run in this backend (enabled flag and token present)."""
    running: bool = False
    """Polling has started and has not stopped."""
    started_at: datetime | None = None
    last_success_at: datetime | None = None
    """Last Telegram API call that returned 2xx (any method, including getUpdates)."""
    last_route: str | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None


_state = BotHealth()
_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def get_bot_health() -> BotHealth:
    with _lock:
        return replace(_state)


def reset_bot_health() -> None:
    """Back to the initial state (tests)."""
    global _state
    with _lock:
        _state = BotHealth()


def mark_enabled(enabled: bool) -> None:
    with _lock:
        _state.enabled = enabled


def mark_started() -> None:
    with _lock:
        _state.running = True
        _state.started_at = _now()
        _state.last_error = None
        _state.last_error_at = None


def mark_stopped(error: str | None = None) -> None:
    with _lock:
        _state.running = False
        if error:
            _state.last_error = error
            _state.last_error_at = _now()


def record_request(ok: bool, *, route: str | None = None, error: str | None = None) -> None:
    with _lock:
        if ok:
            _state.last_success_at = _now()
            _state.last_route = route
        else:
            _state.last_error = f"{route}: {error}" if route and error else (error or route)
            _state.last_error_at = _now()


def _format_age(delta: timedelta) -> str:
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s" if seconds else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def evaluate_bot_health(
    health: BotHealth,
    *,
    now: datetime | None = None,
    proxy_configured: bool = False,
    proxy_running: bool | None = None,
) -> tuple[int | None, list[tuple[str, str]]]:
    """Status level (0 ok, 1 warn, 2 critical, None when the bot is not run here) plus detail lines.

    ``proxy_running`` is what the host's monitor agent reports for the egress proxy container; it
    only matters when the backend is configured to send through that proxy.
    """
    if not health.enabled:
        return None, []
    reference = now or _now()
    lines: list[tuple[str, str]] = []

    if not health.running:
        level = 2
        text = "Bot: not running"
        if health.last_error:
            text += f" ({health.last_error})"
        lines.append((text, "critical"))
    elif health.last_success_at is not None and reference - health.last_success_at <= POLL_STALE_AFTER:
        level = 0
        via = f" via {health.last_route}" if health.last_route else ""
        lines.append((f"Bot: polling Telegram, last contact {_format_age(reference - health.last_success_at)} ago{via}", "ok"))
    elif health.started_at is not None and reference - health.started_at <= POLL_STALE_AFTER:
        level = 1
        lines.append((f"Bot: starting, no Telegram contact yet ({_format_age(reference - health.started_at)})", "warn"))
    else:
        level = 2
        since = (
            f"for {_format_age(reference - health.last_success_at)}"
            if health.last_success_at is not None
            else "since start"
        )
        lines.append((f"Bot: no Telegram contact {since}", "critical"))

    if level != 0 and health.last_error and health.running:
        lines.append((f"Last error: {health.last_error}", "warn"))

    if proxy_configured:
        if proxy_running is True:
            lines.append(("Egress proxy: running", "ok"))
        elif proxy_running is False:
            level = max(level, 1)
            lines.append(("Egress proxy: stopped", "warn"))
        else:
            lines.append(("Egress proxy: state unknown", "warn"))

    return level, lines
