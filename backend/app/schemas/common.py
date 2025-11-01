from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class MountedVolume(BaseModel):
    mount_point: str = Field(..., examples=["/mnt/storage"])
    total_gb: float | None = Field(None, ge=0)
    used_percent: float | None = Field(None, ge=0, le=100)


class CPULoad(BaseModel):
    one: float | None = Field(None, description="1 minute load average")
    five: float | None = Field(None, description="5 minute load average")
    fifteen: float | None = Field(None, description="15 minute load average")


class NetworkCounter(BaseModel):
    interface: str
    bytes_sent: float | None = None
    bytes_recv: float | None = None


class DiskTemperature(BaseModel):
    device: str
    temperature_c: float | None = None


class MetricSnapshotBase(BaseModel):
    reported_at: datetime
    cpu_temperature_c: float | None = None
    ram_used_percent: float | None = None
    total_ram_gb: float | None = None
    disk_usage_percent: float | None = None
    mounted_usage: list[MountedVolume] | None = None
    cpu_load: CPULoad | None = None
    network_counters: list[NetworkCounter] | None = None
    disk_temperatures: list[DiskTemperature] | None = None
    backend_version: str | None = None
    os_version: str | None = None
    uptime_seconds: int | None = None
    warnings: list[str] | None = None
    raw_payload: dict[str, Any] | None = None


class MetricSnapshotRead(MetricSnapshotBase):
    id: int
    backend_id: int

    class Config:
        from_attributes = True
