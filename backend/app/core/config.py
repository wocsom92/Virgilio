import json
from functools import lru_cache
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.app.models.base import CONFIG_SCHEMA, HISTORY_SCHEMA


class Settings(BaseSettings):
    """Application configuration loaded from environment variables or .env files."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SERVER_MONITOR_",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Server Monitor API"
    debug: bool = False
    log_level: str = "INFO"
    log_service: str = "backend"
    # MySQL connection options
    db_user: str = "root"
    db_password: str = "password"
    db_host: str = "localhost"
    db_port: int = 3306
    db_name: str = "server_monitor"
    history_db_name: str = "backend_monitor"

    # Authentication options
    admin_api_token: str = "change-me"  # Legacy token, superseded by username/password auth
    auth_secret_key: str = "change-me-secret"
    auth_access_token_exp_minutes: int = 48 * 60
    auth_algorithm: str = "HS256"

    # Telegram related configuration
    telegram_bot_token: str | None = None
    telegram_default_chat_id: str | None = None
    telegram_proxy_url: str | None = None
    telegram_bind_interface: str | None = None
    telegram_local_address: str | None = None
    telegram_allowed_users: List[str] = Field(default_factory=list)

    # Host control
    allow_host_reboot: bool = False
    reboot_command: str = "sudo /sbin/shutdown -r now"

    # Backend monitor HTTP timeouts
    monitor_request_timeout_seconds: int = 10
    cors_allow_origins: List[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _parse_origins(cls, value: List[str] | str) -> List[str]:
        if isinstance(value, list):
            return [origin.strip() for origin in value if isinstance(origin, str) and origin.strip()]
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
                            return [
                                str(origin).strip()
                                for origin in parsed
                                if isinstance(origin, (str, int, float)) and str(origin).strip()
                            ]
                return [
                    origin.strip().strip('"').strip("'")
                    for origin in value.split(",")
                    if origin and origin.strip().strip('"').strip("'")
                ]
        return []

    @field_validator(
        "telegram_proxy_url",
        "telegram_bind_interface",
        "telegram_local_address",
        mode="before",
    )
    @classmethod
    def _normalize_optional_string(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("telegram_allowed_users", mode="before")
    @classmethod
    def _parse_allowed_users(cls, value: List[str] | str | None) -> List[str]:
        if value is None:
            return []
        items: List[str] = []
        if isinstance(value, list):
            items = [str(item) for item in value if isinstance(item, (str, int, float))]
        elif isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, list):
                    items = [str(item) for item in parsed if isinstance(item, (str, int, float))]
            if not items:
                items = [part.strip() for part in stripped.split(",")]
        cleaned: List[str] = []
        for item in items:
            token = item.strip()
            if not token:
                continue
            if token.startswith("@"):
                token = token[1:]
            cleaned.append(token)
        return cleaned

    def sqlalchemy_database_uri(self) -> str:
        """Build a SQLAlchemy connection string."""
        return (
            f"mysql+asyncmy://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    def sqlalchemy_schema_translate_map(self) -> dict[str, str]:
        """Map logical SQLAlchemy schemas to physical database names."""
        return {
            CONFIG_SCHEMA: self.db_name,
            HISTORY_SCHEMA: self.history_db_name,
        }

    def database_names(self) -> list[str]:
        """Return distinct physical database names used by the backend."""
        names = [self.db_name, self.history_db_name]
        return list(dict.fromkeys(name for name in names if name))


@lru_cache
def get_settings() -> Settings:
    """Return a cached settings instance."""
    return Settings()


settings = get_settings()
