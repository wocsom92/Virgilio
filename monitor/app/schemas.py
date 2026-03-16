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
    monitor_version: str | None = None
    cpu_temperature_c: float | None = None
    ram_used_percent: float | None = None
    total_ram_gb: float | None = None
    memory_available_gb: float | None = None
    swap_used_percent: float | None = None
    docker_container_count: int | None = None
    docker_running_containers: list[str] | None = None
    disk_usage_percent: float | None = None
    disk_total_gb: float | None = None
    disk_available_gb: float | None = None
    mounted_usage: list[MountedVolume] | None = None
    cpu_load: CPULoad | None = None
    network_counters: list[dict] | None = None
    disk_temperatures: list[dict] | None = None
    os_version: str | None = None
    uptime_seconds: int | None = None
    ssh_last_successful_login_seconds: int | None = None
    ssh_last_successful_auth_method: str | None = None
    ssh_last_successful_username: str | None = None
    ssh_last_successful_source_ip: str | None = None
    ssh_last_successful_port: int | None = None
    ssh_last_successful_line: str | None = None
    ssh_last_unsuccessful_attempt_seconds: int | None = None
    ssh_last_failure_auth_method: str | None = None
    ssh_last_failure_username: str | None = None
    ssh_last_failure_source_ip: str | None = None
    ssh_last_failure_port: int | None = None
    ssh_last_failure_line: str | None = None
    ssh_pubkey_auth_enabled: bool | None = None
    ssh_root_password_login_disabled: bool | None = None
    ssh_password_auth_disabled: bool | None = None
    ssh_kbd_interactive_auth_disabled: bool | None = None
    ssh_permit_root_login_mode: str | None = None
    ssh_pubkey_auth_line: str | None = None
    ssh_password_auth_line: str | None = None
    ssh_kbd_interactive_auth_line: str | None = None
    ssh_permit_root_login_line: str | None = None
    ssh_status_level: int | None = None
    ssh_status: str | None = None
    warnings: list[str] | None = None
    configured_mounts: list[str] | None = None
    raw_payload: dict[str, Any] | None = None


class MetricResponse(BaseModel):
    metrics: MetricPayload


class PingResponse(BaseModel):
    target: str
    success: bool
    latency_ms: float | None = None
    checked_at: datetime
