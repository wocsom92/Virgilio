from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.app.models.monitors import SiteMonitor, SiteMonitorSample
from backend.app.services import site_monitoring


@pytest.mark.asyncio
async def test_run_ping_check_falls_back_when_ping_binary_is_missing(monkeypatch):
    now = datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc)
    item = SiteMonitor(
        name="alpha",
        check_type="ping",
        target="alpha.example",
        expected_status_codes=[],
        timeout_ms=1000,
        warning_consecutive_failures=3,
        critical_consecutive_failures=5,
        check_interval_seconds=1800,
        display_order=1,
        is_active=True,
    )

    async def fake_create_subprocess_exec(*args, **kwargs):
        raise FileNotFoundError

    async def fake_tcp_fallback(site_monitor, checked_at):
        assert site_monitor.target == "alpha.example"
        assert checked_at == now
        return site_monitoring.SiteCheckResult(
            checked_at=checked_at,
            success=True,
            latency_ms=42,
            status_code=None,
            detail=None,
        )

    monkeypatch.setattr(site_monitoring.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(site_monitoring, "_run_tcp_ping_fallback", fake_tcp_fallback)

    result = await site_monitoring._run_ping_check(item, now)

    assert result.success is True
    assert result.latency_ms == 42
    assert result.detail is None


@pytest.mark.asyncio
async def test_build_site_monitor_statuses_orders_items_and_uses_latest_samples(db_session):
    now = datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc)
    beta = SiteMonitor(
        name="beta",
        check_type="http",
        target="https://beta.example/healthz",
        expected_status_codes=[200],
        timeout_ms=800,
        warning_consecutive_failures=3,
        critical_consecutive_failures=5,
        check_interval_seconds=1800,
        display_order=2,
        is_active=True,
    )
    alpha = SiteMonitor(
        name="alpha",
        check_type="ping",
        target="alpha.example",
        expected_status_codes=[],
        timeout_ms=1000,
        warning_consecutive_failures=3,
        critical_consecutive_failures=5,
        check_interval_seconds=1800,
        display_order=1,
        is_active=True,
    )
    db_session.add_all([beta, alpha])
    await db_session.flush()
    db_session.add_all(
        [
            SiteMonitorSample(
                site_monitor_id=alpha.id,
                checked_at=now - timedelta(minutes=2),
                success=True,
                latency_ms=20,
                status_code=None,
                detail=None,
                consecutive_failures=0,
            ),
            SiteMonitorSample(
                site_monitor_id=beta.id,
                checked_at=now - timedelta(minutes=1),
                success=False,
                latency_ms=150,
                status_code=503,
                detail="unexpected status 503",
                consecutive_failures=3,
            ),
        ]
    )
    await db_session.commit()

    statuses = await site_monitoring.build_site_monitor_statuses(db_session, [beta, alpha], now=now)

    assert [status.name for status in statuses] == ["alpha", "beta"]
    assert statuses[0].status == "ok"
    assert statuses[0].display_value == "20 ms"
    assert statuses[1].status == "warn"
    assert statuses[1].display_value == "3 failures"


@pytest.mark.asyncio
async def test_build_site_monitor_status_history_uses_latest_result_per_half_hour_slot(db_session):
    now = datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc)
    item = SiteMonitor(
        name="alpha",
        check_type="http",
        target="https://alpha.example/healthz",
        expected_status_codes=[200],
        timeout_ms=1000,
        warning_consecutive_failures=3,
        critical_consecutive_failures=5,
        check_interval_seconds=1800,
        display_order=1,
        is_active=True,
    )
    db_session.add(item)
    await db_session.flush()
    db_session.add_all(
        [
            SiteMonitorSample(
                site_monitor_id=item.id,
                checked_at=now - timedelta(hours=1, minutes=55),
                success=True,
                latency_ms=40,
                status_code=200,
                detail=None,
                consecutive_failures=0,
            ),
            SiteMonitorSample(
                site_monitor_id=item.id,
                checked_at=now - timedelta(hours=1, minutes=40),
                success=False,
                latency_ms=110,
                status_code=503,
                detail="unexpected status 503",
                consecutive_failures=1,
            ),
            SiteMonitorSample(
                site_monitor_id=item.id,
                checked_at=now - timedelta(minutes=20),
                success=True,
                latency_ms=30,
                status_code=200,
                detail=None,
                consecutive_failures=0,
            ),
        ]
    )
    await db_session.commit()

    statuses = await site_monitoring.build_site_monitor_statuses(db_session, [item], now=now)

    assert len(statuses[0].history) == 48
    assert statuses[0].history[-4] == "critical"
    assert statuses[0].history[-1] == "ok"
    assert statuses[0].history[-2] == "unknown"


@pytest.mark.asyncio
async def test_refresh_due_site_monitor_samples_persists_new_results(db_session, monkeypatch):
    now = datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc)
    item = SiteMonitor(
        name="homepage",
        check_type="http",
        target="https://example.com/healthz",
        expected_status_codes=[200],
        timeout_ms=3000,
        warning_consecutive_failures=3,
        critical_consecutive_failures=5,
        check_interval_seconds=1800,
        display_order=0,
        is_active=True,
    )
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)
    db_session.add(
        SiteMonitorSample(
            site_monitor_id=item.id,
            checked_at=now - timedelta(minutes=2),
            success=False,
            latency_ms=None,
            status_code=504,
            detail="request failed",
            consecutive_failures=2,
        )
    )
    await db_session.commit()

    async def fake_run_site_check(site_monitor, checked_at):
        assert site_monitor.id == item.id
        return site_monitoring.SiteCheckResult(
            checked_at=checked_at,
            success=False,
            latency_ms=None,
            status_code=504,
            detail="request failed",
        )

    monkeypatch.setattr(site_monitoring, "_run_site_check", fake_run_site_check)

    latest_samples = await site_monitoring.refresh_due_site_monitor_samples(db_session, [item], now=now)
    await db_session.commit()

    stored_samples = (await db_session.execute(select(SiteMonitorSample))).scalars().all()
    assert len(stored_samples) == 2
    assert latest_samples[item.id].status_code == 504

    statuses = await site_monitoring.build_site_monitor_statuses(
        db_session,
        [item],
        now=now,
        latest_samples=latest_samples,
    )

    assert statuses[0].status == "warn"
    assert statuses[0].display_value == "3 failures"
    assert statuses[0].consecutive_failures == 3
    assert len(statuses[0].history) == 48
