from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from monitor.app import storage
from monitor.app.schemas import MetricPayload


def _payload(report_offset_seconds: int = 0, hostname: str = "host") -> MetricPayload:
    reported_at = datetime.now(tz=timezone.utc) + timedelta(seconds=report_offset_seconds)
    return MetricPayload(
        reported_at=reported_at,
        hostname=hostname,
    )


@pytest.fixture
def repo(monkeypatch) -> storage.MetricsRepository:
    monkeypatch.setattr(
        storage,
        "settings",
        SimpleNamespace(history_retention=lambda: timedelta(minutes=30)),
        raising=False,
    )
    return storage.MetricsRepository(max_entries=3)


@pytest.mark.asyncio
async def test_record_and_latest_return_cloned_payload(repo):
    payload = _payload()
    await repo.record(payload)

    latest = await repo.latest()
    assert latest is not None
    assert latest.hostname == "host"

    latest.hostname = "mutated"
    newest = await repo.latest()
    assert newest.hostname == "host"


@pytest.mark.asyncio
async def test_prune_drops_entries_outside_retention(repo):
    old_payload = _payload(report_offset_seconds=-3600, hostname="old")
    fresh_payload = _payload(hostname="fresh")

    await repo.record(old_payload)
    await repo.record(fresh_payload)

    await repo.prune()

    latest = await repo.latest()
    assert latest is not None
    assert latest.hostname == "fresh"


@pytest.mark.asyncio
async def test_max_entries_enforced(repo):
    await repo.record(_payload(hostname="one"))
    await repo.record(_payload(hostname="two"))
    await repo.record(_payload(hostname="three"))
    await repo.record(_payload(hostname="four"))

    assert len(repo._items) == 3  # noqa: SLF001 - intentional internal check
    assert repo._items[0].hostname == "two"
    assert repo._items[-1].hostname == "four"
