"""Regression: deleting a backend must succeed when quick-status items reference it."""

import pytest
from sqlalchemy import select

from backend.app.models.monitors import (
    MetricSnapshot,
    MonitoredBackend,
    NotificationEvent,
    QuickStatusItem,
    QuickStatusPingSample,
    QuickStatusPrometheusSample,
)
from backend.app.services.backend_queries import delete_monitored_backend


@pytest.mark.asyncio
async def test_delete_monitored_backend_removes_quick_status_items(db_session):
    backend = MonitoredBackend(
        name="to-delete",
        base_url="http://127.0.0.1:9",
        api_token="secret-token",
        display_order=0,
    )
    db_session.add(backend)
    await db_session.flush()
    item = QuickStatusItem(
        backend_id=backend.id,
        label="Disk",
        metric_key="disk_usage_percent",
        warning_threshold=80,
        critical_threshold=90,
        display_order=0,
    )
    db_session.add(item)
    await db_session.commit()

    ok = await delete_monitored_backend(db_session, backend.id)
    assert ok is True

    remaining_backend = await db_session.get(MonitoredBackend, backend.id)
    assert remaining_backend is None
    items = (await db_session.execute(select(QuickStatusItem).where(QuickStatusItem.backend_id == backend.id))).scalars().all()
    assert items == []


@pytest.mark.asyncio
async def test_delete_monitored_backend_returns_false_when_missing(db_session):
    assert await delete_monitored_backend(db_session, 999_999) is False


@pytest.mark.asyncio
async def test_delete_monitored_backend_removes_linked_history_and_notifications(db_session):
    backend = MonitoredBackend(
        name="with-history",
        base_url="http://127.0.0.1:10",
        api_token="secret-token",
        display_order=0,
    )
    db_session.add(backend)
    await db_session.flush()

    item = QuickStatusItem(
        backend_id=backend.id,
        label="Latency",
        metric_key="ping_delay_ms",
        warning_threshold=100,
        critical_threshold=200,
        ping_endpoint="google.com",
        display_order=0,
    )
    db_session.add(item)
    await db_session.flush()

    db_session.add_all(
        [
            QuickStatusPingSample(
                quick_status_item_id=item.id,
                checked_at=item.created_at,
                success=True,
                latency_ms=42,
            ),
            QuickStatusPrometheusSample(
                quick_status_item_id=item.id,
                checked_at=item.created_at,
                sampled_at=item.created_at,
                value=12,
            ),
            MetricSnapshot(
                backend_id=backend.id,
                reported_at=item.created_at,
                raw_payload={"sample": True},
            ),
            NotificationEvent(
                backend_id=backend.id,
                backend_name=backend.name,
                channel="telegram",
                category="quick_status",
                severity="warn",
                title="warn",
                body="body",
                delivery_status="sent",
            ),
        ]
    )
    await db_session.commit()

    assert await delete_monitored_backend(db_session, backend.id) is True

    ping_rows = (await db_session.execute(select(QuickStatusPingSample))).scalars().all()
    prom_rows = (await db_session.execute(select(QuickStatusPrometheusSample))).scalars().all()
    snapshot_rows = (await db_session.execute(select(MetricSnapshot))).scalars().all()
    notification_rows = (await db_session.execute(select(NotificationEvent))).scalars().all()

    assert ping_rows == []
    assert prom_rows == []
    assert snapshot_rows == []
    assert notification_rows == []
