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
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> NotificationCenterResponse:
    items = await list_notification_events(session, limit=limit)
    unread_count = await count_unread_notification_events(session)
    return NotificationCenterResponse(
        unread_count=unread_count,
        items=[NotificationEventRead.model_validate(item) for item in items],
    )


@router.post("/read-all")
async def mark_all_notifications_read(
    session: AsyncSession = Depends(get_session),
) -> dict[str, int | str]:
    cleared = await mark_all_notification_events_read(session)
    return {"status": "ok", "cleared": cleared}
