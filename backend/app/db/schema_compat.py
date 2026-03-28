from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.app.core.config import settings


def ensure_schema_compat(connection: Connection) -> None:
    """Lightweight, safe migrations for small schema deltas."""
    inspector = inspect(connection)

    # Add backend_version column if missing (introduced in 2025-02, stores monitor version).
    if inspector.has_table("metric_snapshots"):
        columns = {col["name"] for col in inspector.get_columns("metric_snapshots")}
        if "backend_version" not in columns:
            connection.execute(text("ALTER TABLE metric_snapshots ADD COLUMN backend_version VARCHAR(40) NULL"))
        if "network_counters" not in columns:
            connection.execute(text("ALTER TABLE metric_snapshots ADD COLUMN network_counters JSON NULL"))
        if "disk_temperatures" not in columns:
            connection.execute(text("ALTER TABLE metric_snapshots ADD COLUMN disk_temperatures JSON NULL"))
        if "memory_available_gb" not in columns:
            connection.execute(text("ALTER TABLE metric_snapshots ADD COLUMN memory_available_gb FLOAT NULL"))
        if "swap_used_percent" not in columns:
            connection.execute(text("ALTER TABLE metric_snapshots ADD COLUMN swap_used_percent FLOAT NULL"))
        if "docker_container_count" not in columns:
            connection.execute(text("ALTER TABLE metric_snapshots ADD COLUMN docker_container_count INT NULL"))
        if "docker_running_containers" not in columns:
            connection.execute(text("ALTER TABLE metric_snapshots ADD COLUMN docker_running_containers JSON NULL"))

    # Add ping fields to quick status items if missing (introduced in 2025-03).
    if inspector.has_table("quick_status_items"):
        columns = {col["name"] for col in inspector.get_columns("quick_status_items")}
        if "ping_endpoint" not in columns:
            connection.execute(text("ALTER TABLE quick_status_items ADD COLUMN ping_endpoint VARCHAR(255) NULL"))
        if "ping_interval_seconds" not in columns:
            connection.execute(
                text(
                    "ALTER TABLE quick_status_items "
                    f"ADD COLUMN ping_interval_seconds INT NOT NULL DEFAULT 60"
                )
            )
        if "last_notified_status" not in columns:
            connection.execute(text("ALTER TABLE quick_status_items ADD COLUMN last_notified_status VARCHAR(16) NULL"))
        if "pending_notification_status" not in columns:
            connection.execute(text("ALTER TABLE quick_status_items ADD COLUMN pending_notification_status VARCHAR(16) NULL"))
        if "pending_notification_due_at" not in columns:
            connection.execute(text("ALTER TABLE quick_status_items ADD COLUMN pending_notification_due_at DATETIME NULL"))
    if not inspector.has_table("quick_status_ping_samples"):
        connection.execute(
            text(
                """
                CREATE TABLE quick_status_ping_samples (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    quick_status_item_id INT NOT NULL,
                    checked_at DATETIME NOT NULL,
                    success BOOLEAN NOT NULL DEFAULT FALSE,
                    latency_ms FLOAT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX ix_quick_status_ping_samples_quick_status_item_id (quick_status_item_id),
                    INDEX ix_quick_status_ping_samples_checked_at (checked_at),
                    CONSTRAINT fk_quick_status_ping_samples_item
                        FOREIGN KEY (quick_status_item_id) REFERENCES quick_status_items(id)
                        ON DELETE CASCADE
                )
                """
            )
        )

    if inspector.has_table("telegram_settings"):
        columns = {col["name"] for col in inspector.get_columns("telegram_settings")}
        if "notification_batch_window_seconds" not in columns:
            connection.execute(
                text(
                    "ALTER TABLE telegram_settings "
                    "ADD COLUMN notification_batch_window_seconds INT NOT NULL DEFAULT 60"
                )
            )
        if "notification_cooldown_minutes" not in columns:
            connection.execute(
                text(
                    "ALTER TABLE telegram_settings "
                    "ADD COLUMN notification_cooldown_minutes INT NOT NULL DEFAULT 15"
                )
            )
        if "quick_status_last_notification_at" not in columns:
            connection.execute(
                text("ALTER TABLE telegram_settings ADD COLUMN quick_status_last_notification_at DATETIME NULL")
            )

    # Create reboot_events table if missing (introduced in 2025-02).
    if not inspector.has_table("reboot_events"):
        connection.execute(
            text(
                """
                CREATE TABLE reboot_events (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    requested_by VARCHAR(120) NOT NULL,
                    chat_id VARCHAR(120) NULL,
                    note TEXT NULL,
                    back_notified_at DATETIME NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )
                """
            )
        )

    # Create users table if missing (introduced in 2025-03).
    if not inspector.has_table("users"):
        connection.execute(
            text(
                """
                CREATE TABLE users (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    username VARCHAR(120) NOT NULL UNIQUE,
                    hashed_password VARCHAR(255) NOT NULL,
                    role VARCHAR(20) NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )
                """
            )
        )

    if not inspector.has_table("notification_events"):
        connection.execute(
            text(
                """
                CREATE TABLE notification_events (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    channel VARCHAR(32) NOT NULL,
                    category VARCHAR(64) NOT NULL,
                    severity VARCHAR(16) NOT NULL DEFAULT 'info',
                    title VARCHAR(255) NOT NULL,
                    body TEXT NOT NULL,
                    backend_id INT NULL,
                    backend_name VARCHAR(100) NULL,
                    delivery_status VARCHAR(16) NOT NULL DEFAULT 'sent',
                    target VARCHAR(120) NULL,
                    error_message TEXT NULL,
                    read_at DATETIME NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX ix_notification_events_backend_id (backend_id),
                    CONSTRAINT fk_notification_events_backend
                        FOREIGN KEY (backend_id) REFERENCES monitored_backends(id)
                        ON DELETE SET NULL
                )
                """
            )
        )

    if not inspector.has_table("site_monitors"):
        connection.execute(
            text(
                """
                CREATE TABLE site_monitors (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(120) NOT NULL UNIQUE,
                    check_type VARCHAR(16) NOT NULL,
                    target VARCHAR(500) NOT NULL,
                    expected_status_codes JSON NULL,
                    expected_response_substring TEXT NULL,
                    timeout_ms INT NOT NULL DEFAULT 3000,
                    warning_consecutive_failures INT NOT NULL DEFAULT 3,
                    critical_consecutive_failures INT NOT NULL DEFAULT 5,
                    check_interval_seconds INT NOT NULL DEFAULT 1800,
                    display_order INT NOT NULL DEFAULT 0,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )
                """
            )
        )
    elif inspector.has_table("site_monitors"):
        columns = {col["name"] for col in inspector.get_columns("site_monitors")}
        if "timeout_ms" not in columns:
            connection.execute(
                text("ALTER TABLE site_monitors ADD COLUMN timeout_ms INT NOT NULL DEFAULT 3000")
            )
        if "warning_consecutive_failures" not in columns:
            connection.execute(
                text(
                    "ALTER TABLE site_monitors "
                    "ADD COLUMN warning_consecutive_failures INT NOT NULL DEFAULT 3"
                )
            )
        if "critical_consecutive_failures" not in columns:
            connection.execute(
                text(
                    "ALTER TABLE site_monitors "
                    "ADD COLUMN critical_consecutive_failures INT NOT NULL DEFAULT 5"
                )
            )
        if "warning_threshold_ms" in columns:
            connection.execute(
                text(
                    "ALTER TABLE site_monitors "
                    "MODIFY COLUMN warning_threshold_ms INT NOT NULL DEFAULT 0"
                )
            )
        if "critical_threshold_ms" in columns:
            connection.execute(
                text(
                    "ALTER TABLE site_monitors "
                    "MODIFY COLUMN critical_threshold_ms INT NOT NULL DEFAULT 0"
                )
            )
        if "check_interval_seconds" in columns:
            connection.execute(
                text(
                    "ALTER TABLE site_monitors "
                    "MODIFY COLUMN check_interval_seconds INT NOT NULL DEFAULT 1800"
                )
            )

    if not inspector.has_table("site_monitor_samples"):
        connection.execute(
            text(
                """
                CREATE TABLE site_monitor_samples (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    site_monitor_id INT NOT NULL,
                    checked_at DATETIME NOT NULL,
                    success BOOLEAN NOT NULL DEFAULT FALSE,
                    latency_ms FLOAT NULL,
                    status_code INT NULL,
                    detail TEXT NULL,
                    consecutive_failures INT NOT NULL DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX ix_site_monitor_samples_site_monitor_id (site_monitor_id),
                    INDEX ix_site_monitor_samples_checked_at (checked_at),
                    CONSTRAINT fk_site_monitor_samples_site_monitor
                        FOREIGN KEY (site_monitor_id) REFERENCES site_monitors(id)
                        ON DELETE CASCADE
                )
                """
            )
        )
    elif inspector.has_table("site_monitor_samples"):
        columns = {col["name"] for col in inspector.get_columns("site_monitor_samples")}
        if "consecutive_failures" not in columns:
            connection.execute(
                text(
                    "ALTER TABLE site_monitor_samples "
                    "ADD COLUMN consecutive_failures INT NOT NULL DEFAULT 0"
                )
            )

    # Add auth_session_minutes to system_settings if missing (introduced in 2025-03).
    if inspector.has_table("system_settings"):
        columns = {col["name"] for col in inspector.get_columns("system_settings")}
        if "auth_session_minutes" not in columns:
            connection.execute(
                text(
                    "ALTER TABLE system_settings "
                    f"ADD COLUMN auth_session_minutes INT NOT NULL DEFAULT {settings.auth_access_token_exp_minutes}"
                )
            )


async def ensure_schema_compat_async(engine: AsyncEngine) -> None:
    """Async entrypoint for triggering schema compatibility adjustments."""
    async with engine.begin() as conn:
        await conn.run_sync(ensure_schema_compat)
