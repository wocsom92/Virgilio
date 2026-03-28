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
    memory_available_gb: Mapped[float | None] = mapped_column(Float)
    swap_used_percent: Mapped[float | None] = mapped_column(Float)
    docker_container_count: Mapped[int | None] = mapped_column(Integer)
    docker_running_containers: Mapped[list[str] | None] = mapped_column(JSON)
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
    notification_batch_window_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    notification_cooldown_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    quick_status_last_notification_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
    auth_session_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=2880)


class NotificationEvent(TimestampMixin, Base):
    __tablename__ = "notification_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False, default="telegram")
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    backend_id: Mapped[int | None] = mapped_column(ForeignKey("monitored_backends.id"), nullable=True, index=True)
    backend_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    delivery_status: Mapped[str] = mapped_column(String(16), nullable=False, default="sent")
    target: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    backend: Mapped[MonitoredBackend | None] = relationship("MonitoredBackend")


class QuickStatusItem(TimestampMixin, Base):
    __tablename__ = "quick_status_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    backend_id: Mapped[int] = mapped_column(ForeignKey("monitored_backends.id"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    metric_key: Mapped[str] = mapped_column(String(64), nullable=False)
    mount_path: Mapped[str | None] = mapped_column(String(255))
    warning_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    critical_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    ping_endpoint: Mapped[str | None] = mapped_column(String(255))
    ping_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_notified_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    pending_notification_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    pending_notification_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    backend: Mapped[MonitoredBackend] = relationship("MonitoredBackend")
    ping_samples: Mapped[list["QuickStatusPingSample"]] = relationship(
        "QuickStatusPingSample",
        back_populates="item",
        cascade="all, delete-orphan",
        order_by="QuickStatusPingSample.checked_at",
    )


class QuickStatusPingSample(TimestampMixin, Base):
    __tablename__ = "quick_status_ping_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    quick_status_item_id: Mapped[int] = mapped_column(ForeignKey("quick_status_items.id"), nullable=False, index=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    latency_ms: Mapped[float | None] = mapped_column(Float)

    item: Mapped[QuickStatusItem] = relationship("QuickStatusItem", back_populates="ping_samples")


class SiteMonitor(TimestampMixin, Base):
    __tablename__ = "site_monitors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    check_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target: Mapped[str] = mapped_column(String(500), nullable=False)
    expected_status_codes: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    expected_response_substring: Mapped[str | None] = mapped_column(Text, nullable=True)
    timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=3000)
    warning_consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    critical_consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    check_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=1800)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    samples: Mapped[list["SiteMonitorSample"]] = relationship(
        "SiteMonitorSample",
        back_populates="site_monitor",
        cascade="all, delete-orphan",
        order_by="SiteMonitorSample.checked_at",
    )


class SiteMonitorSample(TimestampMixin, Base):
    __tablename__ = "site_monitor_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site_monitor_id: Mapped[int] = mapped_column(ForeignKey("site_monitors.id"), nullable=False, index=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    site_monitor: Mapped[SiteMonitor] = relationship("SiteMonitor", back_populates="samples")
