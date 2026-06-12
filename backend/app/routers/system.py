import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from backend.app.core.config import settings
from backend.app.core.security import require_admin_user
from backend.app.db.session import get_session
from backend.app.services.reboot_service import request_reboot
from backend.app.schemas.project import ProjectCreate, ProjectRead, ProjectUpdate
from backend.app.schemas.system import AuthSessionSettings, RetentionSettings
from backend.app.models.monitors import MonitoredBackend, Project, QuickStatusItem, SiteMonitor
from backend.app.schemas.quick_status import QuickStatusItemCreate, QuickStatusItemRead, is_supported_quick_status_metric
from backend.app.schemas.site_monitoring import SiteMonitorCreate, SiteMonitorRead
from backend.app.services.backend_queries import delete_project as delete_project_with_dependents
from backend.app.services.quick_status import create_quick_status_item, update_quick_status_item
from backend.app.services.site_monitoring import create_site_monitor, list_site_monitors, update_site_monitor
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
    # Returns combined size in bytes for the configured config + history schemas.
    try:
        result = await session.execute(
            text(
                "SELECT SUM(data_length + index_length) AS size_bytes "
                "FROM information_schema.tables "
                "WHERE table_schema IN (:config_schema, :history_schema)"
            ),
            {
                "config_schema": settings.db_name,
                "history_schema": settings.history_db_name,
            },
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


@router.get("/projects", response_model=list[ProjectRead])
async def list_projects(session: AsyncSession = Depends(get_session)) -> list[ProjectRead]:
    result = await session.execute(select(Project).order_by(Project.display_order, Project.name, Project.id))
    return [ProjectRead.model_validate(item) for item in result.scalars()]


@router.post("/projects", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    session: AsyncSession = Depends(get_session),
) -> ProjectRead:
    existing = await session.execute(select(Project).where(Project.name == payload.name))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A project with this name already exists")
    project = Project(**payload.model_dump())
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return ProjectRead.model_validate(project)


@router.put("/projects/{project_id}", response_model=ProjectRead)
async def update_project(
    project_id: int,
    payload: ProjectUpdate,
    session: AsyncSession = Depends(get_session),
) -> ProjectRead:
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    updates = payload.model_dump(exclude_unset=True)
    next_name = updates.get("name")
    if next_name:
        existing = await session.execute(select(Project).where(Project.name == next_name, Project.id != project_id))
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A project with this name already exists")
    for key, value in updates.items():
        setattr(project, key, value)
    await session.commit()
    await session.refresh(project)
    return ProjectRead.model_validate(project)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: int, session: AsyncSession = Depends(get_session)) -> None:
    deleted = await delete_project_with_dependents(session, project_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")


@router.get("/quick-status", response_model=list[QuickStatusItemRead])
async def list_quick_status_items(session: AsyncSession = Depends(get_session)) -> list[QuickStatusItemRead]:
    result = await session.execute(
        select(QuickStatusItem).order_by(QuickStatusItem.project_id, QuickStatusItem.backend_id, QuickStatusItem.display_order, QuickStatusItem.id)
    )
    items = []
    for item in result.scalars():
        if not is_supported_quick_status_metric(getattr(item, "metric_key", None)):
            continue
        items.append(QuickStatusItemRead.model_validate(item))
    return items


@router.post("/quick-status", response_model=QuickStatusItemRead, status_code=status.HTTP_201_CREATED)
async def create_quick_status(
    payload: QuickStatusItemCreate,
    session: AsyncSession = Depends(get_session),
) -> QuickStatusItemRead:
    if payload.backend_id is not None:
        backend = await session.get(MonitoredBackend, payload.backend_id)
        if not backend:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backend not found")
    if payload.project_id is not None:
        project = await session.get(Project, payload.project_id)
        if not project:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
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
    if payload.backend_id is not None:
        backend = await session.get(MonitoredBackend, payload.backend_id)
        if not backend:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backend not found")
    if payload.project_id is not None:
        project = await session.get(Project, payload.project_id)
        if not project:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    item = await update_quick_status_item(session, item, payload)
    return QuickStatusItemRead.model_validate(item)


@router.delete("/quick-status/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_quick_status(item_id: int, session: AsyncSession = Depends(get_session)) -> None:
    item = await session.get(QuickStatusItem, item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quick status item not found")
    await session.delete(item)
    await session.commit()


@router.get("/site-monitors", response_model=list[SiteMonitorRead])
async def list_site_monitor_items(session: AsyncSession = Depends(get_session)) -> list[SiteMonitorRead]:
    return [SiteMonitorRead.model_validate(item) for item in await list_site_monitors(session)]


@router.post("/site-monitors", response_model=SiteMonitorRead, status_code=status.HTTP_201_CREATED)
async def create_site_monitor_item(
    payload: SiteMonitorCreate,
    session: AsyncSession = Depends(get_session),
) -> SiteMonitorRead:
    existing = await session.execute(select(SiteMonitor).where(SiteMonitor.name == payload.name))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A site monitor with this name already exists")
    item = await create_site_monitor(session, payload)
    return SiteMonitorRead.model_validate(item)


@router.put("/site-monitors/{item_id}", response_model=SiteMonitorRead)
async def update_site_monitor_item(
    item_id: int,
    payload: SiteMonitorCreate,
    session: AsyncSession = Depends(get_session),
) -> SiteMonitorRead:
    item = await session.get(SiteMonitor, item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Site monitor not found")
    existing = await session.execute(
        select(SiteMonitor).where(SiteMonitor.name == payload.name, SiteMonitor.id != item_id)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A site monitor with this name already exists")
    item = await update_site_monitor(session, item, payload)
    return SiteMonitorRead.model_validate(item)


@router.delete("/site-monitors/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_site_monitor_item(item_id: int, session: AsyncSession = Depends(get_session)) -> None:
    item = await session.get(SiteMonitor, item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Site monitor not found")
    await session.delete(item)
    await session.commit()
