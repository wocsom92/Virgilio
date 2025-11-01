from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.models.base import Base, TimestampMixin


class MonitoredBackend(TimestampMixin, Base):
    __tablename__ = "monitored_backends"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    base_url: Mapped[str] = mapped_column(String(255), nullable=False)
    api_token: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, default=60)
    notes: Mapped[str | None] = mapped_column(Text)
    selected_metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_warning: Mapped[str | None] = mapped_column(Text)

    snapshots: Mapped[list["MetricSnapshot"]] = relationship(
        "MetricSnapshot",
        back_populates="backend",
        cascade="all, delete-orphan",
        order_by="MetricSnapshot.reported_at",
    )


class MetricSnapshot(TimestampMixin, Base):
    __tablename__ = "metric_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    backend_id: Mapped[int] = mapped_column(ForeignKey("monitored_backends.id"), nullable=False, index=True)
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    cpu_temperature_c: Mapped[float | None] = mapped_column(Float)
    ram_used_percent: Mapped[float | None] = mapped_column(Float)
    total_ram_gb: Mapped[float | None] = mapped_column(Float)
    disk_usage_percent: Mapped[float | None] = mapped_column(Float)
    mounted_usage: Mapped[dict | None] = mapped_column(JSON)
    cpu_load: Mapped[dict | None] = mapped_column(JSON)
    backend_version: Mapped[str | None] = mapped_column(String(40))
    network_counters: Mapped[dict | None] = mapped_column(JSON)
    disk_temperatures: Mapped[dict | None] = mapped_column(JSON)
    os_version: Mapped[str | None] = mapped_column(String(120))
    uptime_seconds: Mapped[int | None] = mapped_column(Integer)
    warnings: Mapped[list[str] | None] = mapped_column(JSON)
    raw_payload: Mapped[dict] = mapped_column(JSON)

    backend: Mapped[MonitoredBackend] = relationship("MonitoredBackend", back_populates="snapshots")


class TelegramSettings(TimestampMixin, Base):
    __tablename__ = "telegram_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_token: Mapped[str | None] = mapped_column(String(120), nullable=True)
    default_chat_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    warn_thresholds: Mapped[dict | None] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)


class RebootEvent(TimestampMixin, Base):
    __tablename__ = "reboot_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    requested_by: Mapped[str] = mapped_column(String(120), nullable=False)
    chat_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    back_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SystemSettings(TimestampMixin, Base):
    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    metric_retention_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
