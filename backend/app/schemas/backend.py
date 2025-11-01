from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, HttpUrl

from backend.app.schemas.common import MetricSnapshotRead


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
