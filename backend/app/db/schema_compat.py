from __future__ import annotations

from dataclasses import dataclass
import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.app.core.config import settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ColumnAddition:
    table: str
    column: str
    ddl: str


@dataclass(frozen=True, slots=True)
class IndexAddition:
    table: str
    name: str
    columns: tuple[str, ...]


# Columns that were introduced after the tables first shipped. Tables themselves are created by
# ``Base.metadata.create_all`` on startup; this list only patches pre-existing tables.
_COLUMN_ADDITIONS: tuple[ColumnAddition, ...] = (
    ColumnAddition("metric_snapshots", "backend_version", "VARCHAR(40) NULL"),
    ColumnAddition("metric_snapshots", "network_counters", "JSON NULL"),
    ColumnAddition("metric_snapshots", "disk_temperatures", "JSON NULL"),
    ColumnAddition("metric_snapshots", "memory_available_gb", "FLOAT NULL"),
    ColumnAddition("metric_snapshots", "swap_used_percent", "FLOAT NULL"),
    ColumnAddition("metric_snapshots", "docker_container_count", "INTEGER NULL"),
    ColumnAddition("metric_snapshots", "docker_running_containers", "JSON NULL"),
    ColumnAddition("metric_snapshots", "fan_level", "INTEGER NULL"),
    ColumnAddition("metric_snapshots", "fan_max_level", "INTEGER NULL"),
    ColumnAddition("metric_snapshots", "fan_rpm", "INTEGER NULL"),
    ColumnAddition("quick_status_items", "lower_warning_threshold", "FLOAT NULL"),
    ColumnAddition("quick_status_items", "lower_critical_threshold", "FLOAT NULL"),
    ColumnAddition("quick_status_items", "ping_endpoint", "VARCHAR(255) NULL"),
    ColumnAddition("quick_status_items", "ping_interval_seconds", "INTEGER NOT NULL DEFAULT 60"),
    ColumnAddition("quick_status_items", "last_notified_status", "VARCHAR(16) NULL"),
    ColumnAddition("quick_status_items", "pending_notification_status", "VARCHAR(16) NULL"),
    ColumnAddition("quick_status_items", "pending_notification_due_at", "DATETIME NULL"),
    ColumnAddition("quick_status_items", "prometheus_url", "VARCHAR(500) NULL"),
    ColumnAddition("quick_status_items", "prometheus_query", "TEXT NULL"),
    ColumnAddition("quick_status_items", "prometheus_interval_seconds", "INTEGER NOT NULL DEFAULT 60"),
    ColumnAddition("quick_status_items", "prometheus_threshold_direction", "VARCHAR(16) NOT NULL DEFAULT 'higher'"),
    ColumnAddition("quick_status_items", "show_updated_age", "BOOLEAN NOT NULL DEFAULT 0"),
    ColumnAddition("quick_status_items", "display_variant", "VARCHAR(24) NOT NULL DEFAULT 'tile'"),
    ColumnAddition("quick_status_items", "display_unit", "VARCHAR(16) NULL"),
    ColumnAddition("quick_status_items", "alert_condition_started_at", "DATETIME NULL"),
    ColumnAddition("quick_status_prometheus_samples", "sampled_at", "DATETIME NULL"),
    ColumnAddition("telegram_settings", "notification_batch_window_seconds", "INTEGER NOT NULL DEFAULT 60"),
    ColumnAddition("telegram_settings", "notification_cooldown_minutes", "INTEGER NOT NULL DEFAULT 15"),
    ColumnAddition("telegram_settings", "quick_status_last_notification_at", "DATETIME NULL"),
    ColumnAddition("telegram_settings", "quick_status_alert_confirmation_minutes", "INTEGER NOT NULL DEFAULT 5"),
    ColumnAddition("site_monitors", "timeout_ms", "INTEGER NOT NULL DEFAULT 3000"),
    ColumnAddition("site_monitors", "warning_consecutive_failures", "INTEGER NOT NULL DEFAULT 3"),
    ColumnAddition("site_monitors", "critical_consecutive_failures", "INTEGER NOT NULL DEFAULT 5"),
    ColumnAddition("site_monitors", "check_interval_seconds", "INTEGER NOT NULL DEFAULT 1800"),
    ColumnAddition("site_monitors", "last_checked_at", "DATETIME NULL"),
    ColumnAddition("site_monitors", "last_success", "BOOLEAN NULL"),
    ColumnAddition("site_monitors", "last_latency_ms", "FLOAT NULL"),
    ColumnAddition("site_monitors", "last_status_code", "INTEGER NULL"),
    ColumnAddition("site_monitors", "last_detail", "TEXT NULL"),
    ColumnAddition("site_monitors", "consecutive_failures", "INTEGER NOT NULL DEFAULT 0"),
    ColumnAddition("site_monitor_samples", "consecutive_failures", "INTEGER NOT NULL DEFAULT 0"),
    ColumnAddition(
        "system_settings",
        "auth_session_minutes",
        f"INTEGER NOT NULL DEFAULT {int(settings.auth_access_token_exp_minutes)}",
    ),
)

# Indexes that ``create_all`` does not add to tables that already exist.
_INDEX_ADDITIONS: tuple[IndexAddition, ...] = (
    IndexAddition("metric_snapshots", "ix_metric_snapshots_backend_reported", ("backend_id", "reported_at")),
    IndexAddition(
        "quick_status_ping_samples",
        "ix_quick_status_ping_samples_item_checked",
        ("quick_status_item_id", "checked_at"),
    ),
    IndexAddition(
        "quick_status_prometheus_samples",
        "ix_quick_status_prometheus_samples_item_checked",
        ("quick_status_item_id", "checked_at"),
    ),
    IndexAddition("site_monitor_samples", "ix_site_monitor_samples_monitor_checked", ("site_monitor_id", "checked_at")),
    IndexAddition(
        "quick_status_status_events",
        "ix_quick_status_status_events_item_started",
        ("quick_status_item_id", "started_at"),
    ),
    IndexAddition("notification_events", "ix_notification_events_created_at", ("created_at",)),
    IndexAddition("quick_status_items", "ix_quick_status_items_pending_due", ("pending_notification_due_at",)),
)


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _has_table(connection: Connection, table_name: str) -> bool:
    return inspect(connection).has_table(table_name)


def _get_columns(connection: Connection, table_name: str) -> set[str]:
    return {col["name"] for col in inspect(connection).get_columns(table_name)}


def _get_index_names(connection: Connection, table_name: str) -> set[str]:
    return {index["name"] for index in inspect(connection).get_indexes(table_name) if index.get("name")}


def ensure_schema_compat(connection: Connection) -> None:
    """Lightweight, idempotent migrations for small schema deltas on the SQLite file."""
    if connection.dialect.name == "mysql":
        raise RuntimeError(
            "MySQL is no longer supported. Export the old databases with "
            "backend/scripts/migrate_mysql_to_sqlite.py and point SERVER_MONITOR_DB_PATH at the result."
        )

    column_cache: dict[str, set[str]] = {}
    for addition in _COLUMN_ADDITIONS:
        if addition.table not in column_cache:
            if not _has_table(connection, addition.table):
                continue
            column_cache[addition.table] = _get_columns(connection, addition.table)
        columns = column_cache[addition.table]
        if addition.column in columns:
            continue
        logger.info("Adding missing column %s.%s", addition.table, addition.column)
        connection.execute(
            text(
                f"ALTER TABLE {_quote_identifier(addition.table)} "
                f"ADD COLUMN {_quote_identifier(addition.column)} {addition.ddl}"
            )
        )
        columns.add(addition.column)

    for index in _INDEX_ADDITIONS:
        if not _has_table(connection, index.table):
            continue
        if index.name in _get_index_names(connection, index.table):
            continue
        column_list = ", ".join(_quote_identifier(column) for column in index.columns)
        connection.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS {_quote_identifier(index.name)} "
                f"ON {_quote_identifier(index.table)} ({column_list})"
            )
        )


async def ensure_schema_compat_async(engine: AsyncEngine) -> None:
    """Async entrypoint for triggering schema compatibility adjustments."""
    async with engine.begin() as conn:
        await conn.run_sync(ensure_schema_compat)
