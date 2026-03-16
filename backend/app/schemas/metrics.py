from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, Field

from backend.app.schemas.common import CPULoad, MetricSnapshotRead, MountedVolume, NetworkCounter, DiskTemperature


class MetricSnapshotCreate(BaseModel):
    reported_at: datetime = Field(default_factory=datetime.utcnow)
    cpu_temperature_c: float | None = None
    ram_used_percent: float | None = None
    total_ram_gb: float | None = None
    memory_available_gb: float | None = None
    swap_used_percent: float | None = None
    docker_container_count: int | None = None
    docker_running_containers: list[str] | None = None
    disk_usage_percent: float | None = None
    mounted_usage: list[MountedVolume] | None = None
    cpu_load: CPULoad | None = None
    network_counters: list[NetworkCounter] | None = None
    disk_temperatures: list[DiskTemperature] | None = None
    monitor_version: str | None = Field(
        default=None,
        validation_alias=AliasChoices("monitor_version", "backend_version"),
        serialization_alias="monitor_version",
    )
    os_version: str | None = None
    uptime_seconds: int | None = None
    ssh_pubkey_auth_line: str | None = None
    ssh_password_auth_line: str | None = None
    ssh_kbd_interactive_auth_line: str | None = None
    ssh_permit_root_login_line: str | None = None
    warnings: list[str] | None = None
    raw_payload: dict[str, Any] | None = None


class MetricsIngestResponse(BaseModel):
    snapshot: MetricSnapshotRead


class MetricSeriesPoint(BaseModel):
    reported_at: datetime
    cpu_temperature_c: float | None = None
    ram_used_percent: float | None = None
    memory_available_gb: float | None = None
    swap_used_percent: float | None = None
    disk_usage_percent: float | None = None
    cpu_load: CPULoad | None = None
    mounted_usage: list[MountedVolume] | None = None
    disk_temperatures: list[DiskTemperature] | None = None
    network_bps: list[dict] | None = None  # [{"interface": str, "tx_bps": float|None, "rx_bps": float|None}]


class MetricSeriesResponse(BaseModel):
    backend_id: int
    range: str
    window_offset: int
    window_start: datetime
    window_end: datetime
    previous_offset_with_data: int | None = None
    next_offset_with_data: int | None = None
    points: list[MetricSeriesPoint]
    reboot_markers: list[datetime] | None = None
