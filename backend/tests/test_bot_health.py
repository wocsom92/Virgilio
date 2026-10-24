from datetime import datetime, timedelta, timezone

import pytest

from backend.app.bot import health as bot_health
from backend.app.models.monitors import MetricSnapshot, QuickStatusItem
from backend.app.services import quick_status


@pytest.fixture(autouse=True)
def _reset_health():
    bot_health.reset_bot_health()
    yield
    bot_health.reset_bot_health()


def _health(**overrides) -> bot_health.BotHealth:
    return bot_health.BotHealth(**overrides)


NOW = datetime(2026, 10, 24, 20, 0, tzinfo=timezone.utc)


def test_not_enabled_means_not_evaluated():
    assert bot_health.evaluate_bot_health(_health(), now=NOW) == (None, [])


def test_running_bot_with_recent_contact_is_ok():
    health = _health(enabled=True, running=True, started_at=NOW - timedelta(hours=1), last_success_at=NOW - timedelta(seconds=8), last_route="direct")
    level, lines = bot_health.evaluate_bot_health(health, now=NOW)
    assert level == 0
    assert lines == [("Bot: polling Telegram, last contact 8s ago via direct", "ok")]


def test_bot_still_starting_is_a_warning():
    health = _health(enabled=True, running=True, started_at=NOW - timedelta(seconds=30))
    level, lines = bot_health.evaluate_bot_health(health, now=NOW)
    assert level == 1
    assert lines[0][1] == "warn"


def test_bot_without_contact_is_critical_and_shows_last_error():
    health = _health(
        enabled=True,
        running=True,
        started_at=NOW - timedelta(hours=2),
        last_success_at=NOW - timedelta(minutes=12),
        last_error="direct: timed out",
        last_error_at=NOW - timedelta(seconds=5),
    )
    level, lines = bot_health.evaluate_bot_health(health, now=NOW)
    assert level == 2
    assert lines == [("Bot: no Telegram contact for 12m", "critical"), ("Last error: direct: timed out", "warn")]


def test_stopped_bot_is_critical():
    health = _health(enabled=True, running=False, last_error="TELEGRAM_BOT_TOKEN is not configured")
    level, lines = bot_health.evaluate_bot_health(health, now=NOW)
    assert level == 2
    assert lines == [("Bot: not running (TELEGRAM_BOT_TOKEN is not configured)", "critical")]


def test_stopped_egress_proxy_degrades_to_warning_only_when_configured():
    health = _health(enabled=True, running=True, started_at=NOW - timedelta(hours=1), last_success_at=NOW)
    level, lines = bot_health.evaluate_bot_health(health, now=NOW, proxy_configured=True, proxy_running=False)
    assert level == 1
    assert lines[-1] == ("Egress proxy: stopped", "warn")

    level, lines = bot_health.evaluate_bot_health(health, now=NOW, proxy_configured=False, proxy_running=False)
    assert level == 0
    assert all("proxy" not in text for text, _ in lines)


def test_record_request_tracks_success_and_errors():
    bot_health.mark_enabled(True)
    bot_health.mark_started()
    bot_health.record_request(True, route="direct")
    state = bot_health.get_bot_health()
    assert state.enabled and state.running and state.last_success_at is not None and state.last_route == "direct"

    bot_health.record_request(False, route="xray", error="HTTP 502")
    state = bot_health.get_bot_health()
    assert state.last_error == "xray: HTTP 502"
    assert state.last_error_at is not None

    bot_health.mark_stopped(error="boom")
    state = bot_health.get_bot_health()
    assert state.running is False and state.last_error == "boom"


def _snapshot(payload: dict) -> MetricSnapshot:
    return MetricSnapshot(backend_id=1, reported_at=NOW, raw_payload=payload)


def test_tile_uses_backend_health_instead_of_agent_container_guess(monkeypatch):
    monkeypatch.setattr(quick_status.settings, "telegram_proxy_url", "http://host.docker.internal:18080", raising=False)
    # Agent of a 9.0.0 host: cannot see a bot container, reports critical and proxy running.
    snapshot = _snapshot({"telegram_bot_status_level": 2, "telegram_bot_running": False, "telegram_egress_proxy_running": True})

    # Bot not run by this backend: the agent's view is all we have.
    assert quick_status._metric_value(snapshot, "telegram_bot_status", None) == 2

    bot_health.mark_enabled(True)
    bot_health.mark_started()
    bot_health.record_request(True, route="direct")
    assert quick_status._metric_value(snapshot, "telegram_bot_status", None) == 0

    item = QuickStatusItem(label="Telegram", metric_key="telegram_bot_status", warning_threshold=0, critical_threshold=0)
    lines = quick_status._build_detail_lines(item, snapshot, status="ok")
    assert lines is not None
    assert lines[0].text.startswith("Bot: polling Telegram, last contact")
    assert [line.text for line in lines][-1] == "Egress proxy: running"


def test_docker_container_count_is_informational_only():
    assert quick_status._resolve_status(0, 1, 5, "docker_container_count") == "info"
    assert quick_status._resolve_status(99, 1, 5, "docker_container_count") == "info"
    assert quick_status._resolve_status(None, 1, 5, "docker_container_count") == "unknown"
