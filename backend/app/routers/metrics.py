from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.session import get_session
from backend.app.models.monitors import MetricSnapshot, MonitoredBackend
from backend.app.schemas.metrics import MetricSnapshotCreate, MetricsIngestResponse
from backend.app.services.metrics_service import build_snapshot_model


router = APIRouter(prefix="/metrics", tags=["metrics"])


async def ensure_backend_and_token(
    backend_id: int,
    authorization: str | None = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
) -> MonitoredBackend:
    backend = await session.get(MonitoredBackend, backend_id)
    if not backend or not backend.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backend unavailable")

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    token = authorization.split(" ", 1)[1]
    if token != backend.api_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    return backend


@router.post("/{backend_id}", response_model=MetricsIngestResponse, status_code=status.HTTP_201_CREATED)
async def ingest_metrics(
    backend_id: int,
    payload: MetricSnapshotCreate,
    backend: MonitoredBackend = Depends(ensure_backend_and_token),
    session: AsyncSession = Depends(get_session),
) -> MetricsIngestResponse:
    snapshot = build_snapshot_model(backend.id, payload)
    backend.last_seen_at = datetime.now(tz=timezone.utc)
    backend.last_warning = "; ".join(payload.warnings) if payload.warnings else None

    session.add(snapshot)
    session.add(backend)
    await session.commit()
    await session.refresh(snapshot)

    return MetricsIngestResponse(snapshot=snapshot)


@router.get("/{backend_id}/latest", response_model=MetricsIngestResponse)
async def get_latest_snapshot(
    backend_id: int,
    session: AsyncSession = Depends(get_session),
) -> MetricsIngestResponse:
    backend = await session.get(MonitoredBackend, backend_id)
    if not backend:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backend not found")

    result = await session.execute(
        select(MetricSnapshot)
        .where(MetricSnapshot.backend_id == backend_id)
        .order_by(MetricSnapshot.reported_at.desc())
        .limit(1)
    )
    snapshot = result.scalars().first()
    if not snapshot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No snapshots found")

    return MetricsIngestResponse(snapshot=snapshot)
