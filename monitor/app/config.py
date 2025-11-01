from functools import lru_cache
import json
from datetime import timedelta
from typing import Any, List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _safe_json_loads(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode()
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return stripped
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return value
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="MONITOR_",
        case_sensitive=False,
        extra="ignore",
        json_loads=lambda value: _safe_json_loads(value),
    )

    app_name: str = "Backend Monitor"
    version: str = "2.0.1"
    debug: bool = False

    api_token: str = "monitor-token"
    allow_host_reboot: bool = False
    reboot_command: str = "/sbin/shutdown -r now"
    mounted_points: List[str] = Field(default_factory=lambda: ["auto"])
    host_root_target: str = "/hostfs"
    history_retention_seconds: int = 86_400  # 24 hours
    history_max_entries: int = 1_440        # 1 day of 1-minute samples

    @field_validator("mounted_points", mode="before")
    @classmethod
    def _parse_mounts(cls, value: List[str] | str) -> List[str]:
        def _normalize_list(items: List[str]) -> List[str]:
            normalized: List[str] = []
            for item in items:
                token = str(item).strip()
                if not token:
                    continue
                if token.lower() == "auto" or token == "*":
                    normalized.append("auto")
                else:
                    normalized.append(token)
            return normalized

        if isinstance(value, list):
            mounts = [
                mount
                for mount in value
                if isinstance(mount, (str, int, float)) and str(mount).strip()
            ]
            normalized = _normalize_list([str(mount) for mount in mounts])
            return normalized or ["auto"]
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                if stripped.startswith("["):
                    try:
                        parsed = json.loads(stripped)
                    except json.JSONDecodeError:
                        parsed = None
                    else:
                        if isinstance(parsed, list):
                            normalized = _normalize_list(parsed)
                            return normalized or ["auto"]
                mounts = [
                    item.strip().strip('"').strip("'")
                    for item in value.split(",")
                    if item and item.strip().strip('"').strip("'")
                ]
                normalized = _normalize_list(mounts)
                return normalized or ["auto"]
        return ["auto"]

    @field_validator("host_root_target", mode="before")
    @classmethod
    def _normalize_host_root_target(cls, value: str | None) -> str:
        if value is None:
            return "/hostfs"
        target = str(value).strip()
        if not target:
            return "/hostfs"
        if not target.startswith("/"):
            target = f"/{target}"
        if target != "/":
            target = target.rstrip("/")
        return target or "/hostfs"

    @field_validator("history_retention_seconds", mode="before")
    @classmethod
    def _validate_retention(cls, value: int | str | None) -> int:
        if value in (None, ""):
            return 86_400
        try:
            numeric = int(value)
        except (TypeError, ValueError):
            return 86_400
        return max(60, numeric)

    @field_validator("history_max_entries", mode="before")
    @classmethod
    def _validate_max_entries(cls, value: int | str | None) -> int:
        if value in (None, ""):
            return 1_440
        try:
            numeric = int(value)
        except (TypeError, ValueError):
            return 1_440
        return max(10, numeric)

    def history_retention(self) -> timedelta:
        return timedelta(seconds=self.history_retention_seconds)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
