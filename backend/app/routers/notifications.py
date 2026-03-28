from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.security import get_current_user
from backend.app.db.session import get_session
from backend.app.schemas.notifications import NotificationCenterResponse, NotificationEventRead
from backend.app.services.notification_center import (
    count_unread_notification_events,
    list_notification_events,
    mark_all_notification_events_read,
)


router = APIRouter(prefix="/notifications", tags=["notifications"], dependencies=[Depends(get_current_user)])


@router.get("/", response_model=NotificationCenterResponse)
async def list_notifications(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> NotificationCenterResponse:
    notification_page = await list_notification_events(session, page=page, page_size=page_size)
    unread_count = await count_unread_notification_events(session)
    total_pages = max(1, (notification_page.total_items + notification_page.page_size - 1) // notification_page.page_size)
    return NotificationCenterResponse(
        unread_count=unread_count,
        page=notification_page.page,
        page_size=notification_page.page_size,
        total_items=notification_page.total_items,
        total_pages=total_pages,
        items=[NotificationEventRead.model_validate(item) for item in notification_page.items],
    )


@router.post("/read-all")
async def mark_all_notifications_read(
    session: AsyncSession = Depends(get_session),
) -> dict[str, int | str]:
    cleared = await mark_all_notification_events_read(session)
    return {"status": "ok", "cleared": cleared}
