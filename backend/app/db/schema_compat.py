from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.app.core.config import settings


def ensure_schema_compat(connection: Connection) -> None:
    """Lightweight, safe migrations for small schema deltas."""
    inspector = inspect(connection)

    # Add backend_version column if missing (introduced in 2025-02).
    if inspector.has_table("metric_snapshots"):
        columns = {col["name"] for col in inspector.get_columns("metric_snapshots")}
        if "backend_version" not in columns:
            connection.execute(text("ALTER TABLE metric_snapshots ADD COLUMN backend_version VARCHAR(40) NULL"))
        if "network_counters" not in columns:
            connection.execute(text("ALTER TABLE metric_snapshots ADD COLUMN network_counters JSON NULL"))
        if "disk_temperatures" not in columns:
            connection.execute(text("ALTER TABLE metric_snapshots ADD COLUMN disk_temperatures JSON NULL"))

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
