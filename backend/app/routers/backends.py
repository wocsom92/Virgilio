from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.security import get_current_user, require_admin_user
from backend.app.db.session import get_session
from backend.app.models.monitors import MonitoredBackend
from backend.app.schemas.backend import (
    BackendWithLatestSnapshot,
    MonitoredBackendCreate,
    MonitoredBackendRead,
    MonitoredBackendUpdate,
)
from backend.app.schemas.metrics import MetricsIngestResponse
from backend.app.services.backend_ingest import MetricsPayloadError, ingest_backend_metrics
from backend.app.services.backend_queries import fetch_backends_with_latest_snapshots
from backend.app.services.monitor_client import MonitorClientError, fetch_metrics, request_monitor_reboot


router = APIRouter(prefix="/backends", tags=["backends"])


def _mask_backend_secret(backend: MonitoredBackendRead) -> MonitoredBackendRead:
    payload = backend.model_dump()
    payload["api_token"] = None
    return MonitoredBackendRead.model_validate(payload)


@router.get("/", response_model=list[MonitoredBackendRead])
async def list_backends(
    _: object = Depends(require_admin_user),
    session: AsyncSession = Depends(get_session),
) -> list[MonitoredBackendRead]:
    result = await session.execute(select(MonitoredBackend).order_by(MonitoredBackend.display_order))
    return [_mask_backend_secret(MonitoredBackendRead.model_validate(item)) for item in result.scalars()]


@router.get(
    "/with-latest",
    response_model=list[BackendWithLatestSnapshot],
    dependencies=[Depends(require_admin_user)],
)
async def list_backends_with_latest(
    session: AsyncSession = Depends(get_session),
) -> list[BackendWithLatestSnapshot]:
    return await fetch_backends_with_latest_snapshots(
        session,
        require_admin_ordering=True,
    )


@router.post(
    "/",
    response_model=MonitoredBackendRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin_user)],
)
async def create_backend(
    payload: MonitoredBackendCreate,
    session: AsyncSession = Depends(get_session),
) -> MonitoredBackendRead:
    backend = MonitoredBackend(**payload.model_dump(mode="json"))
    session.add(backend)
    await session.commit()
    await session.refresh(backend)
    return _mask_backend_secret(MonitoredBackendRead.model_validate(backend))


@router.get(
    "/{backend_id}",
    response_model=MonitoredBackendRead,
)
async def get_backend(
    backend_id: int,
    _: object = Depends(require_admin_user),
    session: AsyncSession = Depends(get_session),
) -> MonitoredBackendRead:
    backend = await session.get(MonitoredBackend, backend_id)
    if not backend:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backend not found")
    return _mask_backend_secret(MonitoredBackendRead.model_validate(backend))


@router.put(
    "/{backend_id}",
    response_model=MonitoredBackendRead,
    dependencies=[Depends(require_admin_user)],
)
async def update_backend(
    backend_id: int,
    payload: MonitoredBackendUpdate,
    session: AsyncSession = Depends(get_session),
) -> MonitoredBackendRead:
    backend = await session.get(MonitoredBackend, backend_id)
    if not backend:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backend not found")
    updates = payload.model_dump(exclude_unset=True, mode="json")
    if updates.get("api_token") in (None, ""):
        updates.pop("api_token", None)
    for key, value in updates.items():
        setattr(backend, key, value)
    await session.commit()
    await session.refresh(backend)
    return _mask_backend_secret(MonitoredBackendRead.model_validate(backend))


@router.post(
    "/{backend_id}/refresh",
    response_model=MetricsIngestResponse,
    dependencies=[Depends(require_admin_user)],
)
async def refresh_backend_metrics(
    backend_id: int,
    session: AsyncSession = Depends(get_session),
) -> MetricsIngestResponse:
    backend = await session.get(MonitoredBackend, backend_id)
    if not backend:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backend not found")

    try:
        snapshot = await ingest_backend_metrics(session, backend)
    except MonitorClientError as exc:
        mapped_status = exc.status_code if isinstance(getattr(exc, "status_code", None), int) and 400 <= exc.status_code < 600 else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=mapped_status, detail=str(exc)) from exc
    except MetricsPayloadError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return MetricsIngestResponse(snapshot=snapshot)


@router.post(
    "/{backend_id}/reboot",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_admin_user)],
)
async def reboot_monitor_host(
    backend_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    backend = await session.get(MonitoredBackend, backend_id)
    if not backend or not backend.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backend not found")
    try:
        await request_monitor_reboot(backend.base_url, backend.api_token)
        return {"status": "rebooting"}
    except MonitorClientError as exc:
        mapped_status = exc.status_code if isinstance(getattr(exc, "status_code", None), int) and 400 <= exc.status_code < 600 else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=mapped_status, detail=str(exc)) from exc


@router.get(
    "/{backend_id}/mounts",
    response_model=list[str],
    dependencies=[Depends(require_admin_user)],
)
async def list_backend_mounts(
    backend_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[str]:
    backend = await session.get(MonitoredBackend, backend_id)
    if not backend:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backend not found")

    mounts: list[str] = []
    monitor_error: str | None = None

    try:
        data = await fetch_metrics(backend.base_url, backend.api_token)
    except MonitorClientError as exc:
        monitor_error = str(exc)
    else:
        metrics_payload = data.get("metrics") if isinstance(data, dict) else None
        if isinstance(metrics_payload, dict):
            raw_mounts = metrics_payload.get("mounted_usage") or []
            mounts = [
                volume.get("mount_point")
                for volume in raw_mounts
                if isinstance(volume, dict) and isinstance(volume.get("mount_point"), str)
            ]
            configured = metrics_payload.get("configured_mounts")
            if isinstance(configured, list):
                mounts.extend(str(item).strip() for item in configured if str(item).strip())
            mounts = sorted(set(mounts))
            if mounts:
                return mounts

    result = await session.execute(
        select(MetricSnapshot.mounted_usage)
        .where(MetricSnapshot.backend_id == backend_id)
        .order_by(MetricSnapshot.reported_at.desc())
        .limit(1)
    )
    latest = result.scalar_one_or_none()
    if isinstance(latest, list):
        fallback_mounts = sorted(
            {
                volume.get("mount_point")
                for volume in latest
                if isinstance(volume, dict) and isinstance(volume.get("mount_point"), str)
            }
        )
        if fallback_mounts:
            return list(fallback_mounts)

    if monitor_error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Unable to fetch mount points: {monitor_error}",
        )
    return []

@router.delete(
    "/{backend_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin_user)],
)
async def delete_backend(
    backend_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    backend = await session.get(MonitoredBackend, backend_id)
    if not backend:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backend not found")
    await session.delete(backend)
    await session.commit()
