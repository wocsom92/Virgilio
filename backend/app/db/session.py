from __future__ import annotations

import logging
import os

from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import AsyncAdaptedQueuePool
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from backend.app.core.config import settings
from backend.app.models.base import CONFIG_SCHEMA, HISTORY_SCHEMA

_USE_DEFAULT = object()

# Pragmas applied to every new SQLite connection. WAL keeps readers from blocking the
# single writer, synchronous=NORMAL is durable enough for WAL and saves SD-card fsyncs.
SQLITE_PRAGMAS: tuple[str, ...] = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA busy_timeout=30000",
    "PRAGMA temp_store=MEMORY",
    "PRAGMA cache_size=-16000",
    "PRAGMA journal_size_limit=67108864",
)


def schema_translate_map(
    *,
    config_schema: str | None | object = _USE_DEFAULT,
    history_schema: str | None | object = _USE_DEFAULT,
) -> dict[str, str | None]:
    defaults = settings.sqlalchemy_schema_translate_map()
    return {
        CONFIG_SCHEMA: defaults[CONFIG_SCHEMA] if config_schema is _USE_DEFAULT else config_schema,
        HISTORY_SCHEMA: defaults[HISTORY_SCHEMA] if history_schema is _USE_DEFAULT else history_schema,
    }


def apply_sqlite_pragmas(target: AsyncEngine | Engine) -> None:
    """Register a connect hook that applies SQLITE_PRAGMAS on SQLite engines."""
    sync_engine = target.sync_engine if isinstance(target, AsyncEngine) else target
    if sync_engine.dialect.name != "sqlite":
        return

    @event.listens_for(sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # pragma: no cover - driver glue
        cursor = dbapi_connection.cursor()
        try:
            for pragma in SQLITE_PRAGMAS:
                cursor.execute(pragma)
        finally:
            cursor.close()


def _ensure_sqlite_directory(url: str) -> None:
    prefix = "sqlite+aiosqlite:///"
    if not url.startswith(prefix):
        return
    path = url[len(prefix):]
    if not path or path == ":memory:" or path.startswith("file:"):
        return
    directory = os.path.dirname(path)
    if directory:
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as exc:  # pragma: no cover - surfaced again on first connect
            logging.getLogger(__name__).warning("Could not create database directory %s: %s", directory, exc)


def build_engine(url: str | None = None, *, pool_size: int | None = None) -> AsyncEngine:
    database_url = url or settings.sqlalchemy_database_uri()
    _ensure_sqlite_directory(database_url)
    kwargs: dict = {
        "echo": settings.debug,
        "future": True,
        "execution_options": {"schema_translate_map": schema_translate_map()},
    }
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"timeout": 30}
        if ":memory:" not in database_url:
            # Keep a few open connections instead of the aiosqlite default NullPool so each
            # request does not pay for reopening the file and re-applying pragmas.
            kwargs["poolclass"] = AsyncAdaptedQueuePool
            kwargs["pool_size"] = pool_size or settings.db_pool_size
            kwargs["max_overflow"] = 2
            kwargs["pool_timeout"] = 30
    created = create_async_engine(database_url, **kwargs)
    apply_sqlite_pragmas(created)
    return created


engine = build_engine()

async_session_factory = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def wal_checkpoint(target: AsyncEngine | None = None, *, mode: str = "TRUNCATE") -> None:
    """Fold the WAL back into the main file so it does not grow unbounded."""
    target = target or engine
    if target.sync_engine.dialect.name != "sqlite":
        return
    async with target.connect() as conn:
        await conn.execute(text(f"PRAGMA wal_checkpoint({mode})"))


async def get_session() -> AsyncSession:
    """FastAPI dependency that yields a database session."""
    async with async_session_factory() as session:
        yield session
