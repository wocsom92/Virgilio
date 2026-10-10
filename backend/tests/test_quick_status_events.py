import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from backend.app.models.monitors import MetricSnapshot, MonitoredBackend, QuickStatusItem, QuickStatusStatusEvent
from backend.app.services import maintenance, quick_status, system_settings


async def _seed_backend_item(session, **item_overrides):
    backend = MonitoredBackend(name="alpha", base_url="http://alpha", api_token="token-alpha", display_order=1)
    session.add(backend)
    await session.flush()
    item = QuickStatusItem(
        backend_id=backend.id,
        backend=backend,
        label="RAM",
        metric_key="ram_used_percent",
        warning_threshold=80,
        critical_threshold=90,
        display_order=0,
        **item_overrides,
    )
    session.add(item)
    await session.commit()
    return backend, item


@pytest.mark.parametrize("seed", [1, 7, 42, 1234, 99991])
@pytest.mark.asyncio
async def test_event_log_history_matches_snapshot_algorithm(db_session, seed):
    """The event log must reproduce the legacy snapshot-walk strip exactly."""
    rng = random.Random(seed)
    now = datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc)
    backend, item = await _seed_backend_item(db_session)

    snapshots: list[MetricSnapshot] = []
    at = now - timedelta(hours=26)
    while at <= now:
        value = rng.choice([None, 10.0, 50.0, 85.0, 95.0, 92.0, 20.0])
        snapshots.append(MetricSnapshot(backend_id=backend.id, reported_at=at, raw_payload={}, ram_used_percent=value))
        at += timedelta(minutes=rng.choice([1, 5, 17, 40, 120]))
    db_session.add_all(snapshots)
    await db_session.commit()

    window_start, window_end = quick_status._history_window_bounds(now)
    expected = quick_status._build_item_history(item, snapshots, window_start, window_end)

    await quick_status.rebuild_status_events_for_item(db_session, item, now=now)
    await db_session.commit()
    tiles = await quick_status.build_quick_status_tiles(db_session, [item], now=now)

    assert tiles[0].history == expected
    event_count = await db_session.scalar(select(func.count()).select_from(QuickStatusStatusEvent))
    assert event_count < len(snapshots)


@pytest.mark.asyncio
async def test_record_events_only_on_status_change_and_seeds_from_history(db_session):
    now = datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc)
    backend, item = await _seed_backend_item(db_session)
    older = MetricSnapshot(backend_id=backend.id, reported_at=now - timedelta(hours=2), raw_payload={}, ram_used_percent=20)
    db_session.add(older)
    await db_session.commit()

    first = MetricSnapshot(backend_id=backend.id, reported_at=now, raw_payload={}, ram_used_percent=25)
    db_session.add(first)
    # Cold start on a database without a log: seeds one "ok" event from the stored history.
    written = await quick_status.record_quick_status_status_events(db_session, [item], first)
    await db_session.commit()
    assert written == 1

    same = MetricSnapshot(backend_id=backend.id, reported_at=now + timedelta(minutes=1), raw_payload={}, ram_used_percent=30)
    db_session.add(same)
    assert await quick_status.record_quick_status_status_events(db_session, [item], same) == 0

    worse = MetricSnapshot(backend_id=backend.id, reported_at=now + timedelta(minutes=2), raw_payload={}, ram_used_percent=95)
    db_session.add(worse)
    assert await quick_status.record_quick_status_status_events(db_session, [item], worse) == 1
    await db_session.commit()

    statuses = (
        await db_session.execute(
            select(QuickStatusStatusEvent.status).order_by(QuickStatusStatusEvent.started_at, QuickStatusStatusEvent.id)
        )
    ).scalars().all()
    assert statuses == ["ok", "critical"]


@pytest.mark.asyncio
async def test_updating_thresholds_rebuilds_the_log(db_session):
    # update_quick_status_item rebuilds against the real clock, so seed relative to it.
    now = datetime.now(tz=timezone.utc)
    backend, item = await _seed_backend_item(db_session)
    db_session.add_all(
        [
            MetricSnapshot(backend_id=backend.id, reported_at=now - timedelta(hours=3), raw_payload={}, ram_used_percent=50),
            MetricSnapshot(backend_id=backend.id, reported_at=now - timedelta(hours=1), raw_payload={}, ram_used_percent=70),
        ]
    )
    await db_session.commit()
    await quick_status.rebuild_status_events_for_item(db_session, item, now=now)
    await db_session.commit()
    assert (await db_session.execute(select(QuickStatusStatusEvent.status))).scalars().all() == ["ok"]

    from backend.app.schemas.quick_status import QuickStatusItemCreate

    payload = QuickStatusItemCreate(
        backend_id=backend.id,
        label="RAM",
        metric_key="ram_used_percent",
        warning_threshold=60,
        critical_threshold=65,
    )
    await quick_status.update_quick_status_item(db_session, item, payload)
    statuses = (
        await db_session.execute(select(QuickStatusStatusEvent.status).order_by(QuickStatusStatusEvent.started_at))
    ).scalars().all()
    assert statuses == ["ok", "critical"]


@pytest.mark.asyncio
async def test_retention_keeps_each_items_newest_event(db_session):
    now = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
    backend, item = await _seed_backend_item(db_session)
    await system_settings.update_metric_retention_days(db_session, 1)
    db_session.add_all(
        [
            QuickStatusStatusEvent(quick_status_item_id=item.id, status="ok", started_at=now - timedelta(days=5)),
            QuickStatusStatusEvent(quick_status_item_id=item.id, status="warn", started_at=now - timedelta(days=4)),
            QuickStatusStatusEvent(quick_status_item_id=item.id, status="critical", started_at=now - timedelta(days=3)),
        ]
    )
    await db_session.commit()

    deleted = await maintenance.run_retention(db_session, now=now)

    assert deleted["quick_status_status_events"] == 2
    remaining = (await db_session.execute(select(QuickStatusStatusEvent.status))).scalars().all()
    assert remaining == ["critical"]
