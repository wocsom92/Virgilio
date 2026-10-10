from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, inspect, text

from backend.app.db import schema_compat
from backend.app.db.session import schema_translate_map
from backend.app.models.base import Base


def _sqlite_engine(tmp_path):
    return create_engine(
        f"sqlite:///{tmp_path / 'compat.db'}",
        execution_options={"schema_translate_map": schema_translate_map(config_schema=None, history_schema=None)},
    )


def test_ensure_schema_compat_adds_missing_columns_and_indexes(tmp_path):
    engine = _sqlite_engine(tmp_path)
    with engine.begin() as conn:
        # A pre-"display_unit" quick_status_items table plus a legacy metric_snapshots table.
        conn.execute(
            text(
                "CREATE TABLE quick_status_items ("
                "id INTEGER PRIMARY KEY, backend_id INTEGER NOT NULL, label VARCHAR(120) NOT NULL, "
                "metric_key VARCHAR(64) NOT NULL, warning_threshold FLOAT NOT NULL, critical_threshold FLOAT NOT NULL)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE metric_snapshots ("
                "id INTEGER PRIMARY KEY, backend_id INTEGER NOT NULL, reported_at DATETIME NOT NULL, raw_payload JSON)"
            )
        )
        conn.run_sync = None  # not used; guard against accidental async usage
        schema_compat.ensure_schema_compat(conn)

    inspector = inspect(engine)
    quick_status_columns = {col["name"] for col in inspector.get_columns("quick_status_items")}
    assert {"display_unit", "alert_condition_started_at", "ping_interval_seconds", "display_variant"} <= quick_status_columns
    snapshot_columns = {col["name"] for col in inspector.get_columns("metric_snapshots")}
    assert {"backend_version", "network_counters", "docker_running_containers"} <= snapshot_columns
    index_names = {index["name"] for index in inspector.get_indexes("metric_snapshots")}
    assert "ix_metric_snapshots_backend_reported" in index_names
    item_indexes = {index["name"] for index in inspector.get_indexes("quick_status_items")}
    assert "ix_quick_status_items_pending_due" in item_indexes


def test_ensure_schema_compat_is_idempotent_on_fresh_schema(tmp_path):
    engine = _sqlite_engine(tmp_path)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        schema_compat.ensure_schema_compat(conn)
        before = {
            table: {col["name"] for col in inspect(conn).get_columns(table)}
            for table in inspect(conn).get_table_names()
        }
        schema_compat.ensure_schema_compat(conn)
        after = {
            table: {col["name"] for col in inspect(conn).get_columns(table)}
            for table in inspect(conn).get_table_names()
        }
    assert before == after
    assert "quick_status_items" in before


def test_ensure_schema_compat_rejects_mysql():
    connection = SimpleNamespace(dialect=SimpleNamespace(name="mysql"))
    with pytest.raises(RuntimeError, match="MySQL is no longer supported"):
        schema_compat.ensure_schema_compat(connection)
