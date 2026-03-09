from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


QuickStatusMetricKey = Literal[
    "disk_usage_percent",
    "ram_used_percent",
    "memory_available_gb",
    "swap_used_percent",
    "docker_container_count",
    "cpu_temperature_c",
    "cpu_load_one",
    "cpu_load_five",
    "cpu_load_fifteen",
    "mount_used_percent",
    "mount_available_gb",
    "last_restart",
    "ping_result",
    "ping_delay_ms",
    "ssh_last_successful_login",
    "ssh_last_unsuccessful_attempt",
    "ssh_status",
]

SUPPORTED_QUICK_STATUS_METRIC_KEYS: set[str] = {
    "disk_usage_percent",
    "ram_used_percent",
    "memory_available_gb",
    "swap_used_percent",
    "docker_container_count",
    "cpu_temperature_c",
    "cpu_load_one",
    "cpu_load_five",
    "cpu_load_fifteen",
    "mount_used_percent",
    "mount_available_gb",
    "last_restart",
    "ping_result",
    "ping_delay_ms",
    "ssh_last_successful_login",
    "ssh_last_unsuccessful_attempt",
    "ssh_status",
}

_REQUIRES_PING_ENDPOINT = {"ping_result", "ping_delay_ms"}
_LOWER_IS_WORSE = {"last_restart", "memory_available_gb", "mount_available_gb", "ssh_last_unsuccessful_attempt"}
_NO_THRESHOLD_METRICS = {"ping_result", "ssh_last_successful_login", "ssh_status", "swap_used_percent"}


def is_supported_quick_status_metric(metric_key: str | None) -> bool:
    if not metric_key:
        return False
    return metric_key in SUPPORTED_QUICK_STATUS_METRIC_KEYS


class QuickStatusItemBase(BaseModel):
    backend_id: int
    label: str = Field(..., min_length=1, max_length=120)
    metric_key: QuickStatusMetricKey
    mount_path: str | None = Field(default=None, max_length=255)
    warning_threshold: float = Field(..., ge=0)
    critical_threshold: float = Field(..., ge=0)
    ping_endpoint: str | None = Field(default=None, max_length=255)
    ping_interval_seconds: int = Field(default=60, ge=5, le=86_400)
    display_order: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_item(self) -> "QuickStatusItemBase":
        if self.metric_key in _REQUIRES_PING_ENDPOINT:
            if not (self.ping_endpoint or "").strip():
                raise ValueError("ping_endpoint is required for ping tiles")
        else:
            self.ping_endpoint = None
            self.ping_interval_seconds = 60

        if self.metric_key not in _NO_THRESHOLD_METRICS:
            if self.metric_key == "docker_container_count":
                if self.warning_threshold > self.critical_threshold:
                    # Backward compatibility: legacy rows used warning/critical semantics,
                    # so normalize to [min..max] interval instead of rejecting reads.
                    self.warning_threshold, self.critical_threshold = (
                        self.critical_threshold,
                        self.warning_threshold,
                    )
            elif self.metric_key in _LOWER_IS_WORSE:
                if self.warning_threshold <= self.critical_threshold:
                    raise ValueError("warning_threshold must be greater than critical_threshold")
            else:
                if self.warning_threshold >= self.critical_threshold:
                    raise ValueError("warning_threshold must be less than critical_threshold")

        if self.metric_key in {"mount_used_percent", "mount_available_gb"} and not (self.mount_path or "").strip():
            raise ValueError("mount_path is required for mounted volume tiles")
        if self.metric_key not in {"mount_used_percent", "mount_available_gb"}:
            self.mount_path = None
        return self


class QuickStatusItemCreate(QuickStatusItemBase):
    pass


class QuickStatusItemUpdate(QuickStatusItemBase):
    pass


class QuickStatusItemRead(QuickStatusItemBase):
    id: int

    class Config:
        from_attributes = True


class QuickStatusTileRead(BaseModel):
    id: int
    backend_id: int
    backend_display_order: int
    backend_name: str
    label: str
    metric_key: QuickStatusMetricKey
    value: float | None
    display_value: str
    status: Literal["ok", "info", "warn", "critical", "unknown"]
    reported_at: datetime | None = None
    details: list[str] | None = None
