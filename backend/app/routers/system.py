import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from backend.app.core.security import require_admin_user
from backend.app.db.session import get_session
from backend.app.services.reboot_service import request_reboot
from backend.app.schemas.system import AuthSessionSettings, RetentionSettings
from backend.app.models.monitors import MonitoredBackend, QuickStatusItem
from backend.app.schemas.quick_status import QuickStatusItemCreate, QuickStatusItemRead
from backend.app.services.quick_status import create_quick_status_item, update_quick_status_item
from backend.app.services.system_settings import (
    get_auth_session_minutes,
    get_metric_retention_days,
    update_auth_session_minutes,
    update_metric_retention_days,
)


router = APIRouter(prefix="/system", tags=["system"], dependencies=[Depends(require_admin_user)])
logger = logging.getLogger(__name__)


class RebootRequest(BaseModel):
    reason: str | None = None
    chat_id: str | None = None


@router.post("/reboot")
async def reboot_host(
    payload: RebootRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    event = await request_reboot(
        session,
        requested_by="admin-ui",
        chat_id=payload.chat_id,
        reason=payload.reason,
    )
    return {"status": "rebooting", "requested_at": event.created_at}


@router.get("/db-size")
async def get_db_size(session: AsyncSession = Depends(get_session)) -> dict:
    # Returns database size in bytes for the current schema
    try:
        result = await session.execute(
            text(
                "SELECT SUM(data_length + index_length) AS size_bytes "
                "FROM information_schema.tables WHERE table_schema = DATABASE()"
            )
        )
        size = result.scalar() or 0
    except Exception as exc:  # pragma: no cover - defensive guard for DB permission issues
        logger.warning("Failed to compute database size: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to compute database size",
        ) from exc
    return {"size_bytes": int(size)}


@router.get("/retention", response_model=RetentionSettings)
async def get_retention_settings(session: AsyncSession = Depends(get_session)) -> RetentionSettings:
    days = await get_metric_retention_days(session)
    return RetentionSettings(retention_days=days)


@router.put("/retention", response_model=RetentionSettings)
async def update_retention_settings(
    payload: RetentionSettings,
    session: AsyncSession = Depends(get_session),
) -> RetentionSettings:
    settings = await update_metric_retention_days(session, payload.retention_days)
    return RetentionSettings(retention_days=settings.metric_retention_days)


@router.get("/auth-session", response_model=AuthSessionSettings)
async def get_auth_session_settings(session: AsyncSession = Depends(get_session)) -> AuthSessionSettings:
    minutes = await get_auth_session_minutes(session)
    return AuthSessionSettings(auth_session_minutes=minutes)


@router.put("/auth-session", response_model=AuthSessionSettings)
async def update_auth_session_settings(
    payload: AuthSessionSettings,
    session: AsyncSession = Depends(get_session),
) -> AuthSessionSettings:
    settings = await update_auth_session_minutes(session, payload.auth_session_minutes)
    return AuthSessionSettings(auth_session_minutes=settings.auth_session_minutes)


@router.get("/quick-status", response_model=list[QuickStatusItemRead])
async def list_quick_status_items(session: AsyncSession = Depends(get_session)) -> list[QuickStatusItemRead]:
    result = await session.execute(
        select(QuickStatusItem).order_by(QuickStatusItem.display_order, QuickStatusItem.id)
    )
    return [QuickStatusItemRead.model_validate(item) for item in result.scalars()]


@router.post("/quick-status", response_model=QuickStatusItemRead, status_code=status.HTTP_201_CREATED)
async def create_quick_status(
    payload: QuickStatusItemCreate,
    session: AsyncSession = Depends(get_session),
) -> QuickStatusItemRead:
    backend = await session.get(MonitoredBackend, payload.backend_id)
    if not backend:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backend not found")
    item = await create_quick_status_item(session, payload)
    return QuickStatusItemRead.model_validate(item)


@router.put("/quick-status/{item_id}", response_model=QuickStatusItemRead)
async def update_quick_status(
    item_id: int,
    payload: QuickStatusItemCreate,
    session: AsyncSession = Depends(get_session),
) -> QuickStatusItemRead:
    item = await session.get(QuickStatusItem, item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quick status item not found")
    backend = await session.get(MonitoredBackend, payload.backend_id)
    if not backend:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backend not found")
    item = await update_quick_status_item(session, item, payload)
    return QuickStatusItemRead.model_validate(item)


@router.delete("/quick-status/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_quick_status(item_id: int, session: AsyncSession = Depends(get_session)) -> None:
    item = await session.get(QuickStatusItem, item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quick status item not found")
    await session.delete(item)
    await session.commit()
