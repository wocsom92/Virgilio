from datetime import datetime, timezone

from sqlalchemy import create_engine, func, inspect, select, text

from backend.app.models.base import Base, CONFIG_SCHEMA, HISTORY_SCHEMA
from backend.app.models.monitors import MetricSnapshot, MonitoredBackend, QuickStatusItem, QuickStatusPingSample
from backend.scripts import migrate_mysql_to_sqlite as migration


def _engine(path):
    return create_engine(
        f"sqlite:///{path}",
        execution_options={"schema_translate_map": {CONFIG_SCHEMA: None, HISTORY_SCHEMA: None}},
    )


def test_migrate_copies_tables_preserving_ids_and_slimming_payload(tmp_path):
    source = _engine(tmp_path / "source.db")
    Base.metadata.create_all(source)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with source.begin() as conn:
        conn.execute(
            MonitoredBackend.__table__.insert(),
            [
                {"id": 3, "name": "alpha", "base_url": "http://alpha", "api_token": "t", "is_active": True, "display_order": 0, "poll_interval_seconds": 60},
                {"id": 7, "name": "beta", "base_url": "http://beta", "api_token": "t", "is_active": True, "display_order": 1, "poll_interval_seconds": 60},
            ],
        )
        conn.execute(
            QuickStatusItem.__table__.insert(),
            [{"id": 11, "backend_id": 7, "label": "Ping", "metric_key": "ping_delay_ms", "warning_threshold": 1, "critical_threshold": 2}],
        )
        conn.execute(
            MetricSnapshot.__table__.insert(),
            [
                {
                    "id": 100 + index,
                    "backend_id": 3,
                    "reported_at": now,
                    "ram_used_percent": 10.0 + index,
                    "raw_payload": {"ram_used_percent": 10.0, "ssh_last_failure_username": "root", "top_processes": [], "huge_blob": "x" * 50},
                }
                for index in range(5)
            ],
        )
        conn.execute(
            QuickStatusPingSample.__table__.insert(),
            [{"id": 500, "quick_status_item_id": 11, "checked_at": now, "success": True, "latency_ms": 12.0}],
        )

    target = _engine(tmp_path / "target.db")
    results = migration.migrate(
        source_engines={CONFIG_SCHEMA: source, HISTORY_SCHEMA: source},
        target=target,
        chunk=2,
        log=lambda *_: None,
    )
    assert migration.verify(results) == []
    migration.finalize_sqlite(target)

    with target.connect() as conn:
        backend_ids = sorted(conn.execute(select(MonitoredBackend.__table__.c.id)).scalars())
        assert backend_ids == [3, 7]
        snapshot_count = conn.execute(select(func.count()).select_from(MetricSnapshot.__table__)).scalar()
        assert snapshot_count == 5
        payload = conn.execute(
            select(MetricSnapshot.__table__.c.raw_payload).where(MetricSnapshot.__table__.c.id == 100)
        ).scalar()
        assert "huge_blob" not in payload
        assert payload.get("ssh_last_failure_username") == "root"
        ping_ids = conn.execute(select(QuickStatusPingSample.__table__.c.id)).scalars().all()
        assert ping_ids == [500]
        assert str(conn.execute(text("PRAGMA journal_mode")).scalar()).lower() == "wal"
    assert "metric_snapshots" in inspect(target).get_table_names()
