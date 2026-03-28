from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.monitors import NotificationEvent
from backend.app.services.system_settings import metric_retention_timedelta


@dataclass(slots=True)
class NotificationEventPage:
    items: list[NotificationEvent]
    total_items: int
    page: int
    page_size: int


async def prune_notification_events(session: AsyncSession) -> int:
    retention_window = await metric_retention_timedelta(session)
    cutoff = datetime.now(tz=timezone.utc) - retention_window
    result = await session.execute(
        delete(NotificationEvent)
        .where(NotificationEvent.created_at < cutoff)
        .execution_options(synchronize_session=False)
    )
    deleted = int(result.rowcount or 0)
    if deleted:
        await session.commit()
    return deleted


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
    await prune_notification_events(session)
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


async def list_notification_events(
    session: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 20,
) -> NotificationEventPage:
    await prune_notification_events(session)
    normalized_page = max(1, int(page))
    normalized_page_size = max(1, min(int(page_size), 100))
    total_result = await session.execute(select(func.count(NotificationEvent.id)))
    total_items = int(total_result.scalar() or 0)
    total_pages = max(1, (total_items + normalized_page_size - 1) // normalized_page_size)
    normalized_page = min(normalized_page, total_pages)
    offset = (normalized_page - 1) * normalized_page_size
    result = await session.execute(
        select(NotificationEvent)
        .order_by(NotificationEvent.created_at.desc(), NotificationEvent.id.desc())
        .offset(offset)
        .limit(normalized_page_size)
    )
    return NotificationEventPage(
        items=list(result.scalars()),
        total_items=total_items,
        page=normalized_page,
        page_size=normalized_page_size,
    )


async def count_unread_notification_events(session: AsyncSession) -> int:
    await prune_notification_events(session)
    result = await session.execute(
        select(func.count(NotificationEvent.id)).where(NotificationEvent.read_at.is_(None))
    )
    return int(result.scalar() or 0)


async def mark_all_notification_events_read(session: AsyncSession) -> int:
    await prune_notification_events(session)
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
