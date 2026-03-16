from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.monitors import NotificationEvent


async def record_notification_event(
    session: AsyncSession,
    *,
    channel: str = "telegram",
    category: str,
    severity: str,
    title: str,
    body: str,
    backend_id: int | None = None,
    backend_name: str | None = None,
    delivery_status: str = "sent",
    target: str | None = None,
    error_message: str | None = None,
) -> NotificationEvent:
    event = NotificationEvent(
        channel=channel,
        category=category,
        severity=severity,
        title=title,
        body=body,
        backend_id=backend_id,
        backend_name=backend_name,
        delivery_status=delivery_status,
        target=target,
        error_message=error_message,
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event


async def list_notification_events(session: AsyncSession, *, limit: int = 100) -> list[NotificationEvent]:
    result = await session.execute(
        select(NotificationEvent)
        .order_by(NotificationEvent.created_at.desc(), NotificationEvent.id.desc())
        .limit(max(1, min(limit, 500)))
    )
    return list(result.scalars())


async def count_unread_notification_events(session: AsyncSession) -> int:
    result = await session.execute(
        select(func.count(NotificationEvent.id)).where(NotificationEvent.read_at.is_(None))
    )
    return int(result.scalar() or 0)


async def mark_all_notification_events_read(session: AsyncSession) -> int:
    now = datetime.now(tz=timezone.utc)
    result = await session.execute(
        select(NotificationEvent).where(NotificationEvent.read_at.is_(None))
    )
    items = list(result.scalars())
    for item in items:
        item.read_at = now
        session.add(item)
    await session.commit()
    return len(items)
