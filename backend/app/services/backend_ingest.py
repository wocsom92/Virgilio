from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.monitors import MetricSnapshot, MonitoredBackend
from backend.app.schemas.metrics import MetricSnapshotCreate
from backend.app.services.metrics_service import build_snapshot_model
from backend.app.db.schema_compat import ensure_schema_compat_async
from backend.app.services.monitor_client import MonitorClientError, fetch_metrics
from backend.app.services.telegram_notifications import try_send_warning_notification
from backend.app.services.telegram_settings import get_warn_thresholds
from backend.app.services.warnings import detect_warnings
from backend.app.services.system_settings import metric_retention_timedelta


logger = logging.getLogger(__name__)


class MetricsPayloadError(RuntimeError):
    """Raised when the monitor returns an unexpected payload."""


async def ingest_backend_metrics(session: AsyncSession, backend: MonitoredBackend) -> MetricSnapshot:
    """Fetch metrics from a monitor and persist them for the provided backend.

    The session is committed on success and the refreshed snapshot instance is returned.
    """

    try:
        data: dict[str, Any] = await fetch_metrics(backend.base_url, backend.api_token)
    except MonitorClientError:
        raise

    metrics_payload = data.get("metrics") if isinstance(data, dict) else None
    if not metrics_payload:
        raise MetricsPayloadError("Monitor payload missing 'metrics'")

    payload = MetricSnapshotCreate.model_validate(metrics_payload)

    warn_thresholds = await get_warn_thresholds(session)
    if warn_thresholds is not None:
        warnings = detect_warnings(payload, warn_thresholds)
        payload.warnings = warnings or None

    snapshot = build_snapshot_model(backend.id, payload)

    previous_warning_active = bool(backend.last_warning)
    current_warning_active = bool(payload.warnings)

    backend.last_seen_at = datetime.now(tz=timezone.utc)
    backend.last_warning = "; ".join(payload.warnings) if payload.warnings else None

    retention_window = await metric_retention_timedelta(session)
    cutoff = datetime.now(tz=timezone.utc) - retention_window
    await session.execute(
        delete(MetricSnapshot).where(
            MetricSnapshot.backend_id == backend.id,
            MetricSnapshot.reported_at < cutoff,
        )
    )
    session.add(snapshot)
    session.add(backend)
    await session.commit()
    await session.refresh(snapshot)

    if current_warning_active and not previous_warning_active:
        await try_send_warning_notification(session)

    return snapshot


async def safe_ingest_backend_metrics(session: AsyncSession, backend: MonitoredBackend) -> MetricSnapshot | None:
    """Ingest metrics, logging recoverable errors instead of raising them."""
    attempted_schema_fix = False
    try:
        snapshot = await ingest_backend_metrics(session, backend)
        return snapshot
    except OperationalError as exc:
        message = str(exc.orig).lower() if getattr(exc, "orig", None) else str(exc).lower()
        if ("backend_version" in message or "unknown column" in message) and not attempted_schema_fix:
            attempted_schema_fix = True
            try:
                engine = session.get_bind()
                if engine:
                    await ensure_schema_compat_async(engine)
                    await session.rollback()
                    snapshot = await ingest_backend_metrics(session, backend)
                    return snapshot
            except Exception:  # pragma: no cover - defensive logging
                logger.exception("Schema compatibility fix failed")
        logger.warning("Operational error while ingesting metrics for backend %s: %s", backend.name, exc)
    except MonitorClientError as exc:  # pragma: no cover - simple logging pathway
        logger.warning("Monitor request failed for backend %s: %s", backend.name, exc)
    except MetricsPayloadError as exc:  # pragma: no cover - simple logging pathway
        logger.warning("Monitor payload invalid for backend %s: %s", backend.name, exc)
    except Exception:  # pragma: no cover - defensive logging
        logger.exception("Unexpected error while ingesting metrics for backend %s", backend.name)
    return None
