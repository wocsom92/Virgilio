from pydantic import BaseModel, Field


class WarnThresholds(BaseModel):
    cpu_temperature_c: float | None = Field(
        default=None,
        ge=0,
        le=150,
        description="Trigger warnings at or above this CPU temperature (°C)",
    )
    ram_used_percent: float | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Trigger warnings at or above this RAM usage percentage",
    )
    disk_usage_percent: float | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Trigger warnings at or above this root disk usage percentage",
    )
    mounted_usage_percent: float | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Trigger warnings at or above this mounted volume usage percentage",
    )


class TelegramSettingsBase(BaseModel):
    bot_token: str | None = Field(None, description="Bot token provided by @BotFather")
    default_chat_id: str | None = Field(None, description="Chat id for default notifications")
    warn_thresholds: WarnThresholds | None = Field(
        default=None,
        description="Warning-level thresholds that control when alerts are emitted",
    )
    is_active: bool = False


class TelegramSettingsUpdate(TelegramSettingsBase):
    pass


class TelegramSettingsRead(TelegramSettingsBase):
    id: int

    class Config:
        from_attributes = True
