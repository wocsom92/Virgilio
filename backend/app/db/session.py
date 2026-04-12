from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.core.config import settings
from backend.app.models.base import CONFIG_SCHEMA, HISTORY_SCHEMA

_USE_DEFAULT = object()


def schema_translate_map(
    *,
    config_schema: str | None | object = _USE_DEFAULT,
    history_schema: str | None | object = _USE_DEFAULT,
) -> dict[str, str | None]:
    return {
        CONFIG_SCHEMA: settings.db_name if config_schema is _USE_DEFAULT else config_schema,
        HISTORY_SCHEMA: settings.history_db_name if history_schema is _USE_DEFAULT else history_schema,
    }


engine = create_async_engine(
    settings.sqlalchemy_database_uri(),
    echo=settings.debug,
    future=True,
    execution_options={"schema_translate_map": schema_translate_map()},
)

async_session_factory = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_session() -> AsyncSession:
    """FastAPI dependency that yields a database session."""
    async with async_session_factory() as session:
        yield session
