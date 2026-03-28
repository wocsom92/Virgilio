from datetime import datetime

from pydantic import BaseModel


class NotificationEventRead(BaseModel):
    id: int
    channel: str
    category: str
    severity: str
    title: str
    body: str
    backend_id: int | None = None
    backend_name: str | None = None
    delivery_status: str
    target: str | None = None
    error_message: str | None = None
    read_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class NotificationCenterResponse(BaseModel):
    unread_count: int
    page: int
    page_size: int
    total_items: int
    total_pages: int
    items: list[NotificationEventRead]
