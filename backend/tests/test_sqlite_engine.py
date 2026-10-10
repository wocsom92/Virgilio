import pytest
from sqlalchemy import text

from backend.app.db.session import build_engine, wal_checkpoint


@pytest.mark.asyncio
async def test_file_engine_applies_wal_and_foreign_keys(tmp_path):
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path / 'nested' / 'virgilio.db'}", pool_size=2)
    try:
        async with engine.connect() as conn:
            journal_mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar()
            foreign_keys = (await conn.execute(text("PRAGMA foreign_keys"))).scalar()
            busy_timeout = (await conn.execute(text("PRAGMA busy_timeout"))).scalar()
        assert str(journal_mode).lower() == "wal"
        assert int(foreign_keys) == 1
        assert int(busy_timeout) == 30000
        await wal_checkpoint(engine)
    finally:
        await engine.dispose()
    assert (tmp_path / "nested" / "virgilio.db").exists()
