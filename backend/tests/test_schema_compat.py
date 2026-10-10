from types import SimpleNamespace

import pytest

from backend.app.db import schema_compat


class _FakeResult:
    def __init__(self, value=0):
        self._value = value

    def scalar(self):
        return self._value


class _FakeConnection:
    def __init__(self, *, dialect_name: str = "mysql"):
        self.dialect = SimpleNamespace(name=dialect_name)
        self.executed_sql: list[str] = []

    def execute(self, statement):
        self.executed_sql.append(str(statement))
        return _FakeResult()


@pytest.mark.parametrize("dialect_name", ["sqlite", "postgresql"])
def test_migrate_legacy_history_tables_is_noop_for_non_mysql(dialect_name):
    connection = _FakeConnection(dialect_name=dialect_name)

    schema_compat.migrate_legacy_history_tables(connection)

    assert connection.executed_sql == []


def test_migrate_legacy_history_tables_copies_and_drops_legacy_tables(monkeypatch):
    connection = _FakeConnection()

    monkeypatch.setattr(
        schema_compat,
        "_resolved_schemas",
        lambda connection: ("server_monitor", "backend_monitor"),
    )
    monkeypatch.setattr(
        schema_compat,
        "_has_table",
        lambda connection, table_name, schema=None: table_name == "metric_snapshots",
    )
    monkeypatch.setattr(
        schema_compat,
        "_common_non_id_columns",
        lambda connection, table_name, source_schema=None, target_schema=None: [
            "backend_id",
            "reported_at",
            "raw_payload",
            "created_at",
            "updated_at",
        ],
    )
    monkeypatch.setattr(
        schema_compat,
        "_count_unmigrated_legacy_rows",
        lambda connection, **kwargs: 0,
    )

    schema_compat.migrate_legacy_history_tables(connection)

    assert len(connection.executed_sql) == 2
    assert "INSERT INTO `backend_monitor`.`metric_snapshots`" in connection.executed_sql[0]
    assert "FROM `server_monitor`.`metric_snapshots` AS source_row" in connection.executed_sql[0]
    assert "DROP TABLE `server_monitor`.`metric_snapshots`" in connection.executed_sql[1]


def test_migrate_legacy_history_tables_raises_when_rows_remain_unmigrated(monkeypatch):
    connection = _FakeConnection()

    monkeypatch.setattr(
        schema_compat,
        "_resolved_schemas",
        lambda connection: ("server_monitor", "backend_monitor"),
    )
    monkeypatch.setattr(
        schema_compat,
        "_has_table",
        lambda connection, table_name, schema=None: table_name == "metric_snapshots",
    )
    monkeypatch.setattr(
        schema_compat,
        "_common_non_id_columns",
        lambda connection, table_name, source_schema=None, target_schema=None: [
            "backend_id",
            "reported_at",
        ],
    )
    monkeypatch.setattr(
        schema_compat,
        "_count_unmigrated_legacy_rows",
        lambda connection, **kwargs: 3,
    )

    with pytest.raises(RuntimeError, match="Legacy history migration incomplete"):
        schema_compat.migrate_legacy_history_tables(connection)

    assert len(connection.executed_sql) == 1
    assert "INSERT INTO `backend_monitor`.`metric_snapshots`" in connection.executed_sql[0]


@pytest.mark.parametrize("dialect_name", ["sqlite", "postgresql"])
def test_remove_legacy_project_schema_is_noop_for_non_mysql(dialect_name):
    connection = _FakeConnection(dialect_name=dialect_name)

    schema_compat.remove_legacy_project_schema(connection)

    assert connection.executed_sql == []


def test_remove_legacy_project_schema_removes_project_items_and_tables(monkeypatch):
    connection = _FakeConnection()

    monkeypatch.setattr(
        schema_compat,
        "_resolved_schemas",
        lambda connection: ("server_monitor", "backend_monitor"),
    )
    monkeypatch.setattr(schema_compat, "_has_table", lambda connection, table_name, schema=None: True)
    monkeypatch.setattr(
        schema_compat,
        "_get_columns",
        lambda connection, table_name, schema=None: {"id", "backend_id", "project_id"},
    )
    monkeypatch.setattr(
        schema_compat,
        "_get_foreign_keys",
        lambda connection, table_name, schema=None: [
            {"name": "quick_status_items_ibfk_2", "constrained_columns": ["project_id"]}
        ],
    )
    monkeypatch.setattr(
        schema_compat,
        "inspect",
        lambda connection: SimpleNamespace(
            get_columns=lambda table_name, schema=None: [{"name": "backend_id", "nullable": True}]
        ),
    )

    schema_compat.remove_legacy_project_schema(connection)

    sql = "\n".join(connection.executed_sql)
    assert "DELETE FROM `backend_monitor`.`quick_status_ping_samples`" in sql
    assert "DELETE FROM `backend_monitor`.`quick_status_prometheus_samples`" in sql
    assert "DELETE FROM `server_monitor`.`quick_status_items` WHERE backend_id IS NULL" in sql
    assert "DROP FOREIGN KEY `quick_status_items_ibfk_2`" in sql
    assert "DROP COLUMN project_id" in sql
    assert "MODIFY COLUMN backend_id INT NOT NULL" in sql
    assert "DROP TABLE `server_monitor`.`projects`" in sql
