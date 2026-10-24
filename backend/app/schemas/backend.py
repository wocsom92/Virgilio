from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, HttpUrl

from backend.app.schemas.common import MetricSnapshotRead, MetricSnapshotSummaryRead


class MonitoredBackendBase(BaseModel):
    name: str = Field(..., max_length=100)
    base_url: HttpUrl
    api_token: str = Field(..., min_length=10)
    is_active: bool = True
    display_order: int = 0
    poll_interval_seconds: int = Field(60, ge=30, description="Polling cadence in seconds (minimum 30)")
    notes: str | None = None
    selected_metrics: dict[str, Any] | None = None


class MonitoredBackendCreate(MonitoredBackendBase):
    pass


class MonitoredBackendUpdate(BaseModel):
    name: str | None = None
    base_url: HttpUrl | None = None
    api_token: str | None = None
    is_active: bool | None = None
    display_order: int | None = None
    poll_interval_seconds: int | None = Field(None, ge=30)
    notes: str | None = None
    selected_metrics: dict[str, Any] | None = None


class MonitoredBackendRead(MonitoredBackendBase):
    id: int
    api_token: str | None = None
    last_seen_at: datetime | None = None
    last_warning: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BackendWithLatestSnapshot(MonitoredBackendRead):
    latest_snapshot: MetricSnapshotRead | None = None

    class Config:
        from_attributes = True


class BackendDashboardRead(MonitoredBackendRead):
    """Dashboard list entry: the newest snapshot without its raw payload."""

    latest_snapshot: MetricSnapshotSummaryRead | None = None

    class Config:
        from_attributes = True


class MonitorConnectionTestRequest(BaseModel):
    """Payload for the admin "Test connection" button.

    `api_token` may be omitted when `backend_id` points at an existing server so
    the stored token is reused (the admin form never shows it).
    """

    base_url: HttpUrl
    api_token: str | None = None
    backend_id: int | None = None


class MonitorConnectionTestResult(BaseModel):
    ok: bool
    base_url: str
    latency_ms: int | None = None
    hostname: str | None = None
    monitor_version: str | None = None
    os_version: str | None = None
    cpu_temperature_c: float | None = None
    ram_used_percent: float | None = None
    disk_usage_percent: float | None = None
    uptime_seconds: int | None = None
    reported_at: datetime | None = None
    error_kind: str | None = None
    error: str | None = None
    hint: str | None = None
