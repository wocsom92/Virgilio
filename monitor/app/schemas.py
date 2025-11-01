from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class MountedVolume(BaseModel):
    mount_point: str
    total_gb: float | None = Field(None, ge=0)
    used_percent: float | None = Field(None, ge=0, le=100)


class CPULoad(BaseModel):
    one: float | None = None
    five: float | None = None
    fifteen: float | None = None


class MetricPayload(BaseModel):
    reported_at: datetime
    hostname: str | None = None
    backend_version: str | None = None
    cpu_temperature_c: float | None = None
    ram_used_percent: float | None = None
    total_ram_gb: float | None = None
    disk_usage_percent: float | None = None
    mounted_usage: list[MountedVolume] | None = None
    cpu_load: CPULoad | None = None
    network_counters: list[dict] | None = None
    disk_temperatures: list[dict] | None = None
    os_version: str | None = None
    uptime_seconds: int | None = None
    warnings: list[str] | None = None
    configured_mounts: list[str] | None = None
    raw_payload: dict[str, Any] | None = None


class MetricResponse(BaseModel):
    metrics: MetricPayload
