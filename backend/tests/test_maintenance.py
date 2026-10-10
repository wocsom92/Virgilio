from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from backend.app.models.monitors import (
    MetricSnapshot,
    MonitoredBackend,
    NotificationEvent,
    QuickStatusItem,
    QuickStatusPingSample,
    SiteMonitor,
    SiteMonitorSample,
)
from backend.app.services import maintenance, system_settings


async def _count(session, model) -> int:
    return int((await session.execute(select(func.count()).select_from(model))).scalar() or 0)


@pytest.mark.asyncio
async def test_run_retention_deletes_only_rows_older_than_retention(db_session):
    now = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
    backend = MonitoredBackend(name="alpha", base_url="http://alpha", api_token="t", display_order=0)
    site = SiteMonitor(
        name="site", check_type="http", target="https://example.com", expected_status_codes=[200],
        timeout_ms=1000, warning_consecutive_failures=3, critical_consecutive_failures=5,
        check_interval_seconds=1800, display_order=0, is_active=True,
    )
    db_session.add_all([backend, site])
    await db_session.flush()
    item = QuickStatusItem(
        backend_id=backend.id, label="Ping", metric_key="ping_result", warning_threshold=0, critical_threshold=0,
        ping_endpoint="https://example.com", display_order=0,
    )
    db_session.add(item)
    await db_session.flush()

    await system_settings.update_metric_retention_days(db_session, 2)
    old = now - timedelta(days=3)
    fresh = now - timedelta(hours=1)
    db_session.add_all(
        [
            MetricSnapshot(backend_id=backend.id, reported_at=old, raw_payload={}),
            MetricSnapshot(backend_id=backend.id, reported_at=fresh, raw_payload={}),
            QuickStatusPingSample(quick_status_item_id=item.id, checked_at=old, success=True),
            QuickStatusPingSample(quick_status_item_id=item.id, checked_at=fresh, success=True),
            SiteMonitorSample(site_monitor_id=site.id, checked_at=old, success=True),
            SiteMonitorSample(site_monitor_id=site.id, checked_at=fresh, success=False),
            NotificationEvent(category="x", severity="info", title="old", body="b", created_at=old),
            NotificationEvent(category="x", severity="info", title="new", body="b", created_at=fresh),
        ]
    )
    await db_session.commit()

    deleted = await maintenance.run_retention(db_session, now=now, batch_size=1)

    assert deleted == {
        "metric_snapshots": 1,
        "quick_status_ping_samples": 1,
        "quick_status_prometheus_samples": 0,
        "site_monitor_samples": 1,
        "notification_events": 1,
    }
    assert await _count(db_session, MetricSnapshot) == 1
    assert await _count(db_session, QuickStatusPingSample) == 1
    assert await _count(db_session, SiteMonitorSample) == 1
    remaining = (await db_session.execute(select(NotificationEvent.title))).scalars().all()
    assert remaining == ["new"]

    # Nothing left to prune on a second pass.
    assert all(count == 0 for count in (await maintenance.run_retention(db_session, now=now)).values())
