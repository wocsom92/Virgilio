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
    "telegram_bot_status",
    "wifi_status",
    "vpn_status",
    "prometheus_query",
]

PrometheusThresholdDirection = Literal["higher", "lower"]
QuickStatusDisplayVariant = Literal["tile", "graph", "temperature", "humidity", "self_monitoring"]

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
    "telegram_bot_status",
    "wifi_status",
    "vpn_status",
    "prometheus_query",
}

_REQUIRES_PING_ENDPOINT = {"ping_result", "ping_delay_ms"}
_LOWER_IS_WORSE = {"last_restart", "memory_available_gb", "mount_available_gb", "ssh_last_unsuccessful_attempt"}
_NO_THRESHOLD_METRICS = {
    "ping_result",
    "ssh_last_successful_login",
    "ssh_status",
    "telegram_bot_status",
    "wifi_status",
    "vpn_status",
    "swap_used_percent",
}
_PROMETHEUS_METRICS = {"prometheus_query"}
_BAND_THRESHOLD_METRICS = {"cpu_temperature_c", "prometheus_query"}


def is_supported_quick_status_metric(metric_key: str | None) -> bool:
    if not metric_key:
        return False
    return metric_key in SUPPORTED_QUICK_STATUS_METRIC_KEYS


class QuickStatusItemBase(BaseModel):
    backend_id: int | None = None
    project_id: int | None = None
    label: str = Field(..., min_length=1, max_length=120)
    metric_key: QuickStatusMetricKey
    mount_path: str | None = Field(default=None, max_length=255)
    lower_warning_threshold: float | None = Field(default=None, ge=0)
    lower_critical_threshold: float | None = Field(default=None, ge=0)
    warning_threshold: float = Field(..., ge=0)
    critical_threshold: float = Field(..., ge=0)
    ping_endpoint: str | None = Field(default=None, max_length=255)
    ping_interval_seconds: int = Field(default=60, ge=5, le=86_400)
    prometheus_url: str | None = Field(default=None, max_length=500)
    prometheus_query: str | None = Field(default=None, min_length=1)
    prometheus_interval_seconds: int = Field(default=60, ge=5, le=86_400)
    prometheus_threshold_direction: PrometheusThresholdDirection = "higher"
    display_variant: QuickStatusDisplayVariant = "tile"
    display_unit: str | None = Field(default=None, max_length=16)
    show_updated_age: bool = False
    display_order: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_item(self) -> "QuickStatusItemBase":
        if self.backend_id is None:
            if self.project_id is None:
                raise ValueError("project_id is required when backend_id is not set")
            if self.metric_key not in _PROMETHEUS_METRICS:
                raise ValueError("a backend is required for non-Prometheus tiles")
        if self.metric_key in _REQUIRES_PING_ENDPOINT:
            if not (self.ping_endpoint or "").strip():
                raise ValueError("ping_endpoint is required for ping tiles")
        else:
            self.ping_endpoint = None
            self.ping_interval_seconds = 60

        if self.metric_key in _PROMETHEUS_METRICS:
            if not (self.prometheus_url or "").strip():
                raise ValueError("prometheus_url is required for Prometheus tiles")
            if not (self.prometheus_query or "").strip():
                raise ValueError("prometheus_query is required for Prometheus tiles")
            self.prometheus_url = self.prometheus_url.strip()
            self.prometheus_query = self.prometheus_query.strip()
        else:
            self.prometheus_url = None
            self.prometheus_query = None
            self.prometheus_interval_seconds = 60
            self.prometheus_threshold_direction = "higher"

        if self.display_variant != "tile" and self.metric_key not in _PROMETHEUS_METRICS:
            raise ValueError("graph, temperature, humidity, and self-monitoring items are supported only for Prometheus metrics")
        if self.display_unit is not None:
            self.display_unit = self.display_unit.strip() or None
        if self.display_variant == "humidity" and not self.display_unit:
            self.display_unit = "%"

        if self.metric_key in _BAND_THRESHOLD_METRICS:
            has_lower_thresholds = self.lower_warning_threshold is not None or self.lower_critical_threshold is not None
            if has_lower_thresholds:
                if self.lower_warning_threshold is None or self.lower_critical_threshold is None:
                    raise ValueError("both lower_warning_threshold and lower_critical_threshold are required")
                if self.lower_warning_threshold <= self.lower_critical_threshold:
                    raise ValueError("lower_warning_threshold must be greater than lower_critical_threshold")
        else:
            self.lower_warning_threshold = None
            self.lower_critical_threshold = None

        if self.metric_key not in _NO_THRESHOLD_METRICS:
            if self.metric_key == "docker_container_count":
                if self.warning_threshold > self.critical_threshold:
                    # Backward compatibility: legacy rows used warning/critical semantics,
                    # so normalize to [min..max] interval instead of rejecting reads.
                    self.warning_threshold, self.critical_threshold = (
                        self.critical_threshold,
                        self.warning_threshold,
                    )
            elif self.metric_key in _BAND_THRESHOLD_METRICS and self.lower_warning_threshold is not None:
                if self.warning_threshold >= self.critical_threshold:
                    raise ValueError("warning_threshold must be less than critical_threshold")
            elif self.metric_key in _PROMETHEUS_METRICS and self.prometheus_threshold_direction == "lower":
                if self.warning_threshold <= self.critical_threshold:
                    raise ValueError("warning_threshold must be greater than critical_threshold")
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


class QuickStatusDetailLine(BaseModel):
    text: str = Field(..., min_length=1)
    severity: Literal["ok", "warn", "critical"]


class QuickStatusSeriesPoint(BaseModel):
    recorded_at: datetime
    value: float | None = None


class QuickStatusSeriesSummary(BaseModel):
    min_value: float | None = None
    max_value: float | None = None
    average_value: float | None = None
    delta_value: float | None = None


class QuickStatusCardDatum(BaseModel):
    label: str = Field(..., min_length=1, max_length=120)
    value: str = Field(..., min_length=1, max_length=120)
    severity: Literal["ok", "info", "warn", "critical"] = "ok"


class QuickStatusTileRead(BaseModel):
    id: int
    backend_id: int | None = None
    project_id: int | None = None
    backend_display_order: int
    backend_name: str
    label: str
    metric_key: QuickStatusMetricKey
    display_variant: QuickStatusDisplayVariant = "tile"
    display_unit: str | None = None
    value: float | None
    display_value: str
    status: Literal["ok", "info", "warn", "critical", "unknown"]
    stale: bool = False
    show_updated_age: bool = False
    history_loaded: bool = False
    has_details: bool = False
    history: list[Literal["ok", "info", "warn", "critical", "unknown"]] = Field(default_factory=list)
    series: list[QuickStatusSeriesPoint] = Field(default_factory=list)
    series_summary: QuickStatusSeriesSummary | None = None
    highlights: list[QuickStatusCardDatum] = Field(default_factory=list)
    breakdown: list[QuickStatusCardDatum] = Field(default_factory=list)
    reported_at: datetime | None = None
    details: list[QuickStatusDetailLine] | None = None
