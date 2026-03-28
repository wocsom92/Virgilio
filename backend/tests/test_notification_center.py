from datetime import datetime, timedelta, timezone

import pytest

from backend.app.models.monitors import NotificationEvent, SystemSettings
from backend.app.services.notification_center import count_unread_notification_events, list_notification_events


async def _create_system_settings(session, retention_days: int) -> SystemSettings:
    settings = SystemSettings(metric_retention_days=retention_days, auth_session_minutes=1440)
    session.add(settings)
    await session.commit()
    await session.refresh(settings)
    return settings


async def _create_notification(
    session,
    *,
    title: str,
    created_at: datetime,
    read_at: datetime | None = None,
) -> NotificationEvent:
    event = NotificationEvent(
        channel="telegram",
        category="test",
        severity="info",
        title=title,
        body="body",
        delivery_status="sent",
        created_at=created_at,
        updated_at=created_at,
        read_at=read_at,
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event


@pytest.mark.asyncio
async def test_list_notification_events_returns_paginated_results(db_session):
    await _create_system_settings(db_session, retention_days=7)
    now = datetime.now(tz=timezone.utc)
    for index in range(5):
        await _create_notification(
            db_session,
            title=f"event-{index}",
            created_at=now - timedelta(minutes=index),
        )

    page = await list_notification_events(db_session, page=2, page_size=2)

    assert page.page == 2
    assert page.page_size == 2
    assert page.total_items == 5
    assert [item.title for item in page.items] == ["event-2", "event-3"]


@pytest.mark.asyncio
async def test_notification_center_prunes_events_older_than_metric_retention(db_session):
    await _create_system_settings(db_session, retention_days=3)
    now = datetime.now(tz=timezone.utc)
    await _create_notification(
        db_session,
        title="stale-event",
        created_at=now - timedelta(days=4),
    )
    await _create_notification(
        db_session,
        title="fresh-event",
        created_at=now - timedelta(days=1),
    )

    page = await list_notification_events(db_session, page=1, page_size=20)
    unread_count = await count_unread_notification_events(db_session)

    assert page.total_items == 1
    assert [item.title for item in page.items] == ["fresh-event"]
    assert unread_count == 1
