from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from monitor.app import storage
from monitor.app.schemas import MetricPayload


def _payload(report_offset_seconds: int = 0, hostname: str = "host") -> MetricPayload:
    reported_at = datetime.now(tz=timezone.utc) + timedelta(seconds=report_offset_seconds)
    return MetricPayload(reported_at=reported_at, hostname=hostname)


@pytest.fixture
def repo(monkeypatch) -> storage.MetricsRepository:
    monkeypatch.setattr(
        storage,
        "settings",
        SimpleNamespace(history_retention=lambda: timedelta(minutes=30)),
        raising=False,
    )
    return storage.MetricsRepository()


@pytest.mark.asyncio
async def test_record_and_latest_return_copied_payload(repo):
    await repo.record(_payload())

    latest = await repo.latest()
    assert latest is not None
    assert latest.hostname == "host"

    latest.hostname = "mutated"
    newest = await repo.latest()
    assert newest is not None
    assert newest.hostname == "host"


@pytest.mark.asyncio
async def test_only_the_newest_sample_is_kept(repo):
    await repo.record(_payload(hostname="one"))
    await repo.record(_payload(hostname="two"))

    latest = await repo.latest()
    assert latest is not None
    assert latest.hostname == "two"


@pytest.mark.asyncio
async def test_stale_sample_is_dropped_after_retention(repo):
    await repo.record(_payload(report_offset_seconds=-3600, hostname="old"))
    await repo.prune()
    assert await repo.latest() is None
