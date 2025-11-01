import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from backend.app.core.security import require_admin_user
from backend.app.db.session import get_session
from backend.app.services.reboot_service import request_reboot
from backend.app.schemas.system import RetentionSettings
from backend.app.services.system_settings import get_metric_retention_days, update_metric_retention_days


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
