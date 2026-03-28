from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


SiteMonitorCheckType = Literal["ping", "http"]
SiteMonitorStatus = Literal["ok", "warn", "critical", "unknown"]


class SiteMonitorBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    check_type: SiteMonitorCheckType
    target: str = Field(..., min_length=1, max_length=500)
    expected_status_codes: list[int] | None = None
    expected_response_substring: str | None = Field(default=None, max_length=1000)
    timeout_ms: int = Field(default=3000, ge=100, le=600_000)
    warning_consecutive_failures: int = Field(default=3, ge=1, le=50)
    critical_consecutive_failures: int = Field(default=5, ge=1, le=50)
    check_interval_seconds: int = Field(default=1800, ge=5, le=86_400)
    display_order: int = Field(default=0, ge=0)
    is_active: bool = True

    @model_validator(mode="after")
    def validate_site_monitor(self) -> "SiteMonitorBase":
        self.name = self.name.strip()
        self.target = self.target.strip()
        self.expected_response_substring = (
            self.expected_response_substring.strip() if self.expected_response_substring else None
        )
        self.expected_status_codes = self.expected_status_codes or []
        self.expected_status_codes = [int(code) for code in self.expected_status_codes if 100 <= int(code) <= 599]
        self.expected_status_codes = sorted(set(self.expected_status_codes))
        if self.critical_consecutive_failures <= self.warning_consecutive_failures:
            raise ValueError("critical_consecutive_failures must be greater than warning_consecutive_failures")
        if self.check_type == "ping":
            self.expected_status_codes = []
            self.expected_response_substring = None
        else:
            if not self.expected_status_codes:
                raise ValueError("expected_status_codes must contain at least one valid HTTP status code")
        return self


class SiteMonitorCreate(SiteMonitorBase):
    pass


class SiteMonitorRead(SiteMonitorBase):
    id: int

    class Config:
        from_attributes = True


class SiteMonitorStatusRead(BaseModel):
    id: int
    name: str
    check_type: SiteMonitorCheckType
    target: str
    status: SiteMonitorStatus
    display_value: str
    history: list[SiteMonitorStatus] = Field(default_factory=list)
    checked_at: datetime | None = None
    latency_ms: float | None = None
    status_code: int | None = None
    detail: str | None = None
    consecutive_failures: int = 0
