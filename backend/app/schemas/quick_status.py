from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


QuickStatusMetricKey = Literal[
    "disk_usage_percent",
    "ram_used_percent",
    "cpu_temperature_c",
    "cpu_load_one",
    "mount_used_percent",
]


class QuickStatusItemBase(BaseModel):
    backend_id: int
    label: str = Field(..., min_length=1, max_length=120)
    metric_key: QuickStatusMetricKey
    mount_path: str | None = Field(default=None, max_length=255)
    warning_threshold: float = Field(..., ge=0)
    critical_threshold: float = Field(..., ge=0)
    display_order: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_item(self) -> "QuickStatusItemBase":
        if self.warning_threshold >= self.critical_threshold:
            raise ValueError("warning_threshold must be less than critical_threshold")
        if self.metric_key == "mount_used_percent" and not (self.mount_path or "").strip():
            raise ValueError("mount_path is required for mounted usage tiles")
        if self.metric_key != "mount_used_percent":
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
    backend_name: str
    label: str
    metric_key: QuickStatusMetricKey
    value: float | None
    display_value: str
    status: Literal["ok", "warn", "critical", "unknown"]
    reported_at: datetime | None = None
