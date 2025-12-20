from pydantic import BaseModel, Field


class RetentionSettings(BaseModel):
    retention_days: int = Field(..., ge=1, le=90, description="Number of days to retain metric history")


class AuthSessionSettings(BaseModel):
    auth_session_minutes: int = Field(
        ...,
        ge=15,
        le=60 * 24 * 30,
        description="Number of minutes before access tokens expire",
    )
