"""One-time migration of the legacy MySQL databases into the SQLite file.

Example (inside the backend container, with the legacy ``db`` service running):

    pip install -q pymysql
    python -m backend.scripts.migrate_mysql_to_sqlite \
        --mysql-url mysql+pymysql://root:changeme@db:3306 \
        --config-db server_monitor --history-db backend_monitor \
        --sqlite-path /app/data/virgilio.db

The script creates the current schema in the target file, copies every table in
foreign-key order (ids preserved), verifies row counts, then switches the file to
WAL mode so the backend can use it directly.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Iterable

from sqlalchemy import Table, create_engine, func, inspect, select, text
from sqlalchemy.engine import Engine

from backend.app.models import monitors as _monitors  # noqa: F401  (register tables)
from backend.app.models import users as _users  # noqa: F401
from backend.app.models.base import Base, CONFIG_SCHEMA, HISTORY_SCHEMA


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mysql-url", required=True, help="SQLAlchemy URL without database, e.g. mysql+pymysql://root:pw@db:3306")
    parser.add_argument("--config-db", default="server_monitor")
    parser.add_argument("--history-db", default="backend_monitor")
    parser.add_argument("--sqlite-path", default=os.environ.get("SERVER_MONITOR_DB_PATH", "/app/data/virgilio.db"))
    parser.add_argument("--chunk", type=int, default=2000)
    parser.add_argument("--force", action="store_true", help="Overwrite an existing SQLite file")
    parser.add_argument("--skip-history", action="store_true", help="Copy configuration tables only")
    return parser.parse_args(argv)


def _history_tables() -> set[str]:
    return {table.name for table in Base.metadata.sorted_tables if table.schema == HISTORY_SCHEMA}


def copy_table(
    source: Engine,
    target: Engine,
    table: Table,
    *,
    chunk: int = 2000,
    transform=None,
) -> tuple[int, int]:
    """Copy ``table`` from ``source`` to ``target`` in id order. Returns (copied, source_count)."""
    source_columns = {col["name"] for col in inspect(source).get_columns(table.name, schema=None)}
    columns = [column for column in table.columns if column.name in source_columns]
    if not columns:
        return 0, 0
    id_column = table.c.id
    last_id = 0
    copied = 0
    with source.connect() as src_conn:
        total = int(src_conn.execute(select(func.count()).select_from(table)).scalar() or 0)
        while True:
            rows = (
                src_conn.execute(
                    select(*columns).where(id_column > last_id).order_by(id_column).limit(chunk)
                )
                .mappings()
                .all()
            )
            if not rows:
                break
            payload = [dict(row) for row in rows]
            if transform is not None:
                payload = [transform(row) for row in payload]
            with target.begin() as dst_conn:
                dst_conn.execute(table.insert(), payload)
            copied += len(payload)
            last_id = int(payload[-1]["id"])
    return copied, total


def migrate(
    *,
    source_engines: dict[str, Engine],
    target: Engine,
    chunk: int = 2000,
    skip_history: bool = False,
    log=print,
) -> dict[str, tuple[int, int]]:
    """Copy all tables. ``source_engines`` maps logical schema -> engine bound to that database."""
    try:
        from backend.app.services.metrics_service import slim_extra_payload
    except ImportError:  # pragma: no cover - older tree without payload slimming
        slim_extra_payload = None

    def _slim_snapshot(row: dict) -> dict:
        if slim_extra_payload is not None and isinstance(row.get("raw_payload"), dict):
            row["raw_payload"] = slim_extra_payload(row["raw_payload"])
        return row

    Base.metadata.create_all(target)
    results: dict[str, tuple[int, int]] = {}
    history = _history_tables()
    for table in Base.metadata.sorted_tables:
        if skip_history and table.name in history:
            continue
        source = source_engines[table.schema or CONFIG_SCHEMA]
        if not inspect(source).has_table(table.name):
            log(f"skip {table.name}: not present in source")
            continue
        transform = _slim_snapshot if table.name == "metric_snapshots" else None
        copied, total = copy_table(source, target, table, chunk=chunk, transform=transform)
        results[table.name] = (copied, total)
        log(f"{table.name}: copied {copied}/{total}")
    return results


def verify(results: dict[str, tuple[int, int]]) -> list[str]:
    return [f"{name}: copied {copied} of {total}" for name, (copied, total) in results.items() if copied != total]


def finalize_sqlite(target: Engine) -> None:
    with target.connect() as conn:
        conn.execution_options(isolation_level="AUTOCOMMIT")
        problems = conn.execute(text("PRAGMA foreign_key_check")).fetchall()
        if problems:
            raise RuntimeError(f"foreign_key_check reported {len(problems)} violations: {problems[:5]}")
        conn.execute(text("ANALYZE"))
        conn.execute(text("VACUUM"))
        conn.execute(text("PRAGMA journal_mode=WAL"))


def _mysql_engine(base_url: str, database: str, schema_key: str) -> Engine:
    url = base_url.rstrip("/") + "/" + database
    return create_engine(
        url,
        execution_options={"schema_translate_map": {CONFIG_SCHEMA: None, HISTORY_SCHEMA: None}},
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if os.path.exists(args.sqlite_path):
        if not args.force:
            print(f"refusing to overwrite existing {args.sqlite_path} (use --force)", file=sys.stderr)
            return 2
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(args.sqlite_path + suffix)
            except FileNotFoundError:
                pass
    directory = os.path.dirname(args.sqlite_path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    source_engines = {
        CONFIG_SCHEMA: _mysql_engine(args.mysql_url, args.config_db, CONFIG_SCHEMA),
        HISTORY_SCHEMA: _mysql_engine(args.mysql_url, args.history_db, HISTORY_SCHEMA),
    }
    target = create_engine(
        f"sqlite:///{args.sqlite_path}",
        execution_options={"schema_translate_map": {CONFIG_SCHEMA: None, HISTORY_SCHEMA: None}},
    )
    with target.connect() as conn:
        conn.execution_options(isolation_level="AUTOCOMMIT")
        conn.execute(text("PRAGMA journal_mode=OFF"))
        conn.execute(text("PRAGMA synchronous=OFF"))

    results = migrate(source_engines=source_engines, target=target, chunk=args.chunk, skip_history=args.skip_history)
    mismatches = verify(results)
    if mismatches:
        print("row count mismatch:\n  " + "\n  ".join(mismatches), file=sys.stderr)
        return 1
    finalize_sqlite(target)
    print(f"migration complete: {args.sqlite_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
