from __future__ import annotations

from dataclasses import dataclass
import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.app.core.config import settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HistoryTableMigrationSpec:
    table_name: str
    key_columns: tuple[str, ...]


_LEGACY_HISTORY_TABLES: tuple[HistoryTableMigrationSpec, ...] = (
    HistoryTableMigrationSpec("metric_snapshots", ("backend_id", "reported_at")),
    HistoryTableMigrationSpec("quick_status_ping_samples", ("quick_status_item_id", "checked_at")),
    HistoryTableMigrationSpec("quick_status_prometheus_samples", ("quick_status_item_id", "checked_at")),
    HistoryTableMigrationSpec("site_monitor_samples", ("site_monitor_id", "checked_at")),
)


def _quote_identifier(identifier: str) -> str:
    return f"`{identifier.replace('`', '``')}`"


def _resolved_schemas(connection: Connection) -> tuple[str | None, str | None]:
    if connection.dialect.name == "sqlite":
        return None, None
    return settings.db_name, settings.history_db_name


def _qualified_table_name(table_name: str, *, schema: str | None) -> str:
    if schema:
        return f"{_quote_identifier(schema)}.{_quote_identifier(table_name)}"
    return _quote_identifier(table_name)


def _has_table(connection: Connection, table_name: str, *, schema: str | None) -> bool:
    return inspect(connection).has_table(table_name, schema=schema)


def _get_columns(connection: Connection, table_name: str, *, schema: str | None) -> set[str]:
    return {col["name"] for col in inspect(connection).get_columns(table_name, schema=schema)}


def _get_ordered_columns(connection: Connection, table_name: str, *, schema: str | None) -> list[str]:
    return [col["name"] for col in inspect(connection).get_columns(table_name, schema=schema)]


def _common_non_id_columns(
    connection: Connection,
    table_name: str,
    *,
    source_schema: str | None,
    target_schema: str | None,
) -> list[str]:
    source_columns = set(_get_ordered_columns(connection, table_name, schema=source_schema))
    target_columns = _get_ordered_columns(connection, table_name, schema=target_schema)
    return [column for column in target_columns if column != "id" and column in source_columns]


def _build_match_predicate(*, target_alias: str, source_alias: str, key_columns: tuple[str, ...]) -> str:
    return " AND ".join(
        f"{target_alias}.{_quote_identifier(column)} = {source_alias}.{_quote_identifier(column)}"
        for column in key_columns
    )


def _count_unmigrated_legacy_rows(
    connection: Connection,
    *,
    table_name: str,
    source_schema: str | None,
    target_schema: str | None,
    key_columns: tuple[str, ...],
) -> int:
    source_table = _qualified_table_name(table_name, schema=source_schema)
    target_table = _qualified_table_name(table_name, schema=target_schema)
    result = connection.execute(
        text(
            "SELECT COUNT(*) FROM {source_table} AS source_row "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM {target_table} AS target_row WHERE {match_predicate}"
            ")".format(
                source_table=source_table,
                target_table=target_table,
                match_predicate=_build_match_predicate(
                    target_alias="target_row",
                    source_alias="source_row",
                    key_columns=key_columns,
                ),
            )
        )
    )
    return int(result.scalar() or 0)


def migrate_legacy_history_tables(connection: Connection) -> None:
    if connection.dialect.name != "mysql":
        return

    config_schema, history_schema = _resolved_schemas(connection)
    if not config_schema or not history_schema or config_schema == history_schema:
        return

    for spec in _LEGACY_HISTORY_TABLES:
        if not _has_table(connection, spec.table_name, schema=config_schema):
            continue
        if not _has_table(connection, spec.table_name, schema=history_schema):
            logger.warning(
                "Skipping legacy history migration for %s because target table %s.%s does not exist",
                spec.table_name,
                history_schema,
                spec.table_name,
            )
            continue

        common_columns = _common_non_id_columns(
            connection,
            spec.table_name,
            source_schema=config_schema,
            target_schema=history_schema,
        )
        if not common_columns:
            logger.warning(
                "Skipping legacy history migration for %s because no shared columns were found",
                spec.table_name,
            )
            continue

        source_table = _qualified_table_name(spec.table_name, schema=config_schema)
        target_table = _qualified_table_name(spec.table_name, schema=history_schema)
        quoted_columns = ", ".join(_quote_identifier(column) for column in common_columns)
        selected_columns = ", ".join(f"source_row.{_quote_identifier(column)}" for column in common_columns)

        connection.execute(
            text(
                "INSERT INTO {target_table} ({quoted_columns}) "
                "SELECT {selected_columns} FROM {source_table} AS source_row "
                "WHERE NOT EXISTS ("
                "SELECT 1 FROM {target_table} AS target_row WHERE {match_predicate}"
                ")".format(
                    target_table=target_table,
                    quoted_columns=quoted_columns,
                    selected_columns=selected_columns,
                    source_table=source_table,
                    match_predicate=_build_match_predicate(
                        target_alias="target_row",
                        source_alias="source_row",
                        key_columns=spec.key_columns,
                    ),
                )
            )
        )

        remaining_rows = _count_unmigrated_legacy_rows(
            connection,
            table_name=spec.table_name,
            source_schema=config_schema,
            target_schema=history_schema,
            key_columns=spec.key_columns,
        )
        if remaining_rows:
            raise RuntimeError(
                f"Legacy history migration incomplete for {config_schema}.{spec.table_name}: "
                f"{remaining_rows} rows were not copied to {history_schema}.{spec.table_name}"
            )

        logger.info(
            "Dropping legacy history table %s.%s after migrating rows to %s.%s",
            config_schema,
            spec.table_name,
            history_schema,
            spec.table_name,
        )
        connection.execute(text(f"DROP TABLE {source_table}"))


def ensure_databases_exist(connection: Connection) -> None:
    """Create the configured physical databases when the server supports it."""
    if connection.dialect.name != "mysql":
        return
    for name in settings.database_names():
        connection.exec_driver_sql(
            f"CREATE DATABASE IF NOT EXISTS {_quote_identifier(name)} "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )


def ensure_schema_compat(connection: Connection) -> None:
    """Lightweight, safe migrations for small schema deltas."""
    config_schema, history_schema = _resolved_schemas(connection)
    config_projects = _qualified_table_name("projects", schema=config_schema)
    config_quick_status_items = _qualified_table_name("quick_status_items", schema=config_schema)
    config_telegram_settings = _qualified_table_name("telegram_settings", schema=config_schema)
    config_reboot_events = _qualified_table_name("reboot_events", schema=config_schema)
    config_users = _qualified_table_name("users", schema=config_schema)
    config_notification_events = _qualified_table_name("notification_events", schema=config_schema)
    config_monitored_backends = _qualified_table_name("monitored_backends", schema=config_schema)
    config_site_monitors = _qualified_table_name("site_monitors", schema=config_schema)
    config_system_settings = _qualified_table_name("system_settings", schema=config_schema)
    history_metric_snapshots = _qualified_table_name("metric_snapshots", schema=history_schema)
    history_ping_samples = _qualified_table_name("quick_status_ping_samples", schema=history_schema)
    history_prometheus_samples = _qualified_table_name("quick_status_prometheus_samples", schema=history_schema)
    history_site_monitor_samples = _qualified_table_name("site_monitor_samples", schema=history_schema)

    if not _has_table(connection, "projects", schema=config_schema):
        connection.execute(
            text(
                """
                CREATE TABLE {config_projects} (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(120) NOT NULL UNIQUE,
                    display_order INT NOT NULL DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )
                """.format(config_projects=config_projects)
            )
        )

    # Add backend_version column if missing (introduced in 2025-02, stores monitor version).
    if _has_table(connection, "metric_snapshots", schema=history_schema):
        columns = _get_columns(connection, "metric_snapshots", schema=history_schema)
        if "backend_version" not in columns:
            connection.execute(text(f"ALTER TABLE {history_metric_snapshots} ADD COLUMN backend_version VARCHAR(40) NULL"))
        if "network_counters" not in columns:
            connection.execute(text(f"ALTER TABLE {history_metric_snapshots} ADD COLUMN network_counters JSON NULL"))
        if "disk_temperatures" not in columns:
            connection.execute(text(f"ALTER TABLE {history_metric_snapshots} ADD COLUMN disk_temperatures JSON NULL"))
        if "memory_available_gb" not in columns:
            connection.execute(text(f"ALTER TABLE {history_metric_snapshots} ADD COLUMN memory_available_gb FLOAT NULL"))
        if "swap_used_percent" not in columns:
            connection.execute(text(f"ALTER TABLE {history_metric_snapshots} ADD COLUMN swap_used_percent FLOAT NULL"))
        if "docker_container_count" not in columns:
            connection.execute(text(f"ALTER TABLE {history_metric_snapshots} ADD COLUMN docker_container_count INT NULL"))
        if "docker_running_containers" not in columns:
            connection.execute(text(f"ALTER TABLE {history_metric_snapshots} ADD COLUMN docker_running_containers JSON NULL"))

    # Add ping fields to quick status items if missing (introduced in 2025-03).
    if _has_table(connection, "quick_status_items", schema=config_schema):
        quick_status_columns = {
            col["name"]: col for col in inspect(connection).get_columns("quick_status_items", schema=config_schema)
        }
        columns = set(quick_status_columns)
        if "project_id" not in columns:
            connection.execute(text(f"ALTER TABLE {config_quick_status_items} ADD COLUMN project_id INT NULL"))
        backend_column = quick_status_columns.get("backend_id")
        if backend_column is not None and backend_column.get("nullable") is False:
            connection.execute(text(f"ALTER TABLE {config_quick_status_items} MODIFY COLUMN backend_id INT NULL"))
        if "lower_warning_threshold" not in columns:
            connection.execute(text(f"ALTER TABLE {config_quick_status_items} ADD COLUMN lower_warning_threshold FLOAT NULL"))
        if "lower_critical_threshold" not in columns:
            connection.execute(text(f"ALTER TABLE {config_quick_status_items} ADD COLUMN lower_critical_threshold FLOAT NULL"))
        if "ping_endpoint" not in columns:
            connection.execute(text(f"ALTER TABLE {config_quick_status_items} ADD COLUMN ping_endpoint VARCHAR(255) NULL"))
        if "ping_interval_seconds" not in columns:
            connection.execute(
                text(
                    f"ALTER TABLE {config_quick_status_items} "
                    f"ADD COLUMN ping_interval_seconds INT NOT NULL DEFAULT 60"
                )
            )
        if "last_notified_status" not in columns:
            connection.execute(text(f"ALTER TABLE {config_quick_status_items} ADD COLUMN last_notified_status VARCHAR(16) NULL"))
        if "pending_notification_status" not in columns:
            connection.execute(
                text(f"ALTER TABLE {config_quick_status_items} ADD COLUMN pending_notification_status VARCHAR(16) NULL")
            )
        if "pending_notification_due_at" not in columns:
            connection.execute(text(f"ALTER TABLE {config_quick_status_items} ADD COLUMN pending_notification_due_at DATETIME NULL"))
        if "prometheus_url" not in columns:
            connection.execute(text(f"ALTER TABLE {config_quick_status_items} ADD COLUMN prometheus_url VARCHAR(500) NULL"))
        if "prometheus_query" not in columns:
            connection.execute(text(f"ALTER TABLE {config_quick_status_items} ADD COLUMN prometheus_query TEXT NULL"))
        if "prometheus_interval_seconds" not in columns:
            connection.execute(
                text(
                    f"ALTER TABLE {config_quick_status_items} "
                    "ADD COLUMN prometheus_interval_seconds INT NOT NULL DEFAULT 60"
                )
            )
        if "prometheus_threshold_direction" not in columns:
            connection.execute(
                text(
                    f"ALTER TABLE {config_quick_status_items} "
                    "ADD COLUMN prometheus_threshold_direction VARCHAR(16) NOT NULL DEFAULT 'higher'"
                )
            )
        if "show_updated_age" not in columns:
            connection.execute(
                text(
                    f"ALTER TABLE {config_quick_status_items} "
                    "ADD COLUMN show_updated_age BOOLEAN NOT NULL DEFAULT FALSE"
                )
            )
        if "display_variant" not in columns:
            connection.execute(
                text(
                    f"ALTER TABLE {config_quick_status_items} "
                    "ADD COLUMN display_variant VARCHAR(24) NOT NULL DEFAULT 'tile'"
                )
            )
        if "display_unit" not in columns:
            connection.execute(text(f"ALTER TABLE {config_quick_status_items} ADD COLUMN display_unit VARCHAR(16) NULL"))
        if "alert_condition_started_at" not in columns:
            connection.execute(
                text(f"ALTER TABLE {config_quick_status_items} ADD COLUMN alert_condition_started_at DATETIME NULL")
            )
    if not _has_table(connection, "quick_status_ping_samples", schema=history_schema):
        connection.execute(
            text(
                """
                CREATE TABLE {history_ping_samples} (
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
                        FOREIGN KEY (quick_status_item_id) REFERENCES {config_quick_status_items}(id)
                        ON DELETE CASCADE
                )
                """.format(
                    history_ping_samples=history_ping_samples,
                    config_quick_status_items=config_quick_status_items,
                )
            )
        )
    if not _has_table(connection, "quick_status_prometheus_samples", schema=history_schema):
        connection.execute(
            text(
                """
                CREATE TABLE {history_prometheus_samples} (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    quick_status_item_id INT NOT NULL,
                    checked_at DATETIME NOT NULL,
                    sampled_at DATETIME NULL,
                    value FLOAT NULL,
                    error_message TEXT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX ix_quick_status_prometheus_samples_quick_status_item_id (quick_status_item_id),
                    INDEX ix_quick_status_prometheus_samples_checked_at (checked_at),
                    CONSTRAINT fk_quick_status_prometheus_samples_item
                        FOREIGN KEY (quick_status_item_id) REFERENCES {config_quick_status_items}(id)
                        ON DELETE CASCADE
                )
                """.format(
                    history_prometheus_samples=history_prometheus_samples,
                    config_quick_status_items=config_quick_status_items,
                )
            )
        )
    else:
        columns = _get_columns(connection, "quick_status_prometheus_samples", schema=history_schema)
        if "sampled_at" not in columns:
            connection.execute(text(f"ALTER TABLE {history_prometheus_samples} ADD COLUMN sampled_at DATETIME NULL"))

    if _has_table(connection, "telegram_settings", schema=config_schema):
        columns = _get_columns(connection, "telegram_settings", schema=config_schema)
        if "notification_batch_window_seconds" not in columns:
            connection.execute(
                text(
                    f"ALTER TABLE {config_telegram_settings} "
                    "ADD COLUMN notification_batch_window_seconds INT NOT NULL DEFAULT 60"
                )
            )
        if "notification_cooldown_minutes" not in columns:
            connection.execute(
                text(
                    f"ALTER TABLE {config_telegram_settings} "
                    "ADD COLUMN notification_cooldown_minutes INT NOT NULL DEFAULT 15"
                )
            )
        if "quick_status_last_notification_at" not in columns:
            connection.execute(
                text(f"ALTER TABLE {config_telegram_settings} ADD COLUMN quick_status_last_notification_at DATETIME NULL")
            )
        if "quick_status_alert_confirmation_minutes" not in columns:
            connection.execute(
                text(
                    f"ALTER TABLE {config_telegram_settings} "
                    "ADD COLUMN quick_status_alert_confirmation_minutes INT NOT NULL DEFAULT 5"
                )
            )

    # Create reboot_events table if missing (introduced in 2025-02).
    if not _has_table(connection, "reboot_events", schema=config_schema):
        connection.execute(
            text(
                """
                CREATE TABLE {config_reboot_events} (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    requested_by VARCHAR(120) NOT NULL,
                    chat_id VARCHAR(120) NULL,
                    note TEXT NULL,
                    back_notified_at DATETIME NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )
                """.format(config_reboot_events=config_reboot_events)
            )
        )

    # Create users table if missing (introduced in 2025-03).
    if not _has_table(connection, "users", schema=config_schema):
        connection.execute(
            text(
                """
                CREATE TABLE {config_users} (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    username VARCHAR(120) NOT NULL UNIQUE,
                    hashed_password VARCHAR(255) NOT NULL,
                    role VARCHAR(20) NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )
                """.format(config_users=config_users)
            )
        )

    if not _has_table(connection, "notification_events", schema=config_schema):
        connection.execute(
            text(
                """
                CREATE TABLE {config_notification_events} (
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
                        FOREIGN KEY (backend_id) REFERENCES {config_monitored_backends}(id)
                        ON DELETE SET NULL
                )
                """.format(
                    config_notification_events=config_notification_events,
                    config_monitored_backends=config_monitored_backends,
                )
            )
        )

    if not _has_table(connection, "site_monitors", schema=config_schema):
        connection.execute(
            text(
                """
                CREATE TABLE {config_site_monitors} (
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
                """.format(config_site_monitors=config_site_monitors)
            )
        )
    elif _has_table(connection, "site_monitors", schema=config_schema):
        columns = _get_columns(connection, "site_monitors", schema=config_schema)
        if "timeout_ms" not in columns:
            connection.execute(
                text(f"ALTER TABLE {config_site_monitors} ADD COLUMN timeout_ms INT NOT NULL DEFAULT 3000")
            )
        if "warning_consecutive_failures" not in columns:
            connection.execute(
                text(
                    f"ALTER TABLE {config_site_monitors} "
                    "ADD COLUMN warning_consecutive_failures INT NOT NULL DEFAULT 3"
                )
            )
        if "critical_consecutive_failures" not in columns:
            connection.execute(
                text(
                    f"ALTER TABLE {config_site_monitors} "
                    "ADD COLUMN critical_consecutive_failures INT NOT NULL DEFAULT 5"
                )
            )
        if "warning_threshold_ms" in columns:
            connection.execute(
                text(
                    f"ALTER TABLE {config_site_monitors} "
                    "MODIFY COLUMN warning_threshold_ms INT NOT NULL DEFAULT 0"
                )
            )
        if "critical_threshold_ms" in columns:
            connection.execute(
                text(
                    f"ALTER TABLE {config_site_monitors} "
                    "MODIFY COLUMN critical_threshold_ms INT NOT NULL DEFAULT 0"
                )
            )
        if "check_interval_seconds" in columns:
            connection.execute(
                text(
                    f"ALTER TABLE {config_site_monitors} "
                    "MODIFY COLUMN check_interval_seconds INT NOT NULL DEFAULT 1800"
                )
            )

    if not _has_table(connection, "site_monitor_samples", schema=history_schema):
        connection.execute(
            text(
                """
                CREATE TABLE {history_site_monitor_samples} (
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
                        FOREIGN KEY (site_monitor_id) REFERENCES {config_site_monitors}(id)
                        ON DELETE CASCADE
                )
                """.format(
                    history_site_monitor_samples=history_site_monitor_samples,
                    config_site_monitors=config_site_monitors,
                )
            )
        )
    elif _has_table(connection, "site_monitor_samples", schema=history_schema):
        columns = _get_columns(connection, "site_monitor_samples", schema=history_schema)
        if "consecutive_failures" not in columns:
            connection.execute(
                text(
                    f"ALTER TABLE {history_site_monitor_samples} "
                    "ADD COLUMN consecutive_failures INT NOT NULL DEFAULT 0"
                )
            )

    # Add auth_session_minutes to system_settings if missing (introduced in 2025-03).
    if _has_table(connection, "system_settings", schema=config_schema):
        columns = _get_columns(connection, "system_settings", schema=config_schema)
        if "auth_session_minutes" not in columns:
            connection.execute(
                text(
                    f"ALTER TABLE {config_system_settings} "
                    f"ADD COLUMN auth_session_minutes INT NOT NULL DEFAULT {settings.auth_access_token_exp_minutes}"
                )
            )

    migrate_legacy_history_tables(connection)


async def ensure_schema_compat_async(engine: AsyncEngine) -> None:
    """Async entrypoint for triggering schema compatibility adjustments."""
    async with engine.begin() as conn:
        await conn.run_sync(ensure_databases_exist)
        await conn.run_sync(ensure_schema_compat)
