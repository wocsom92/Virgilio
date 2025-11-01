from pydantic import BaseModel, Field


class RetentionSettings(BaseModel):
    retention_days: int = Field(..., ge=1, le=90, description="Number of days to retain metric history")
