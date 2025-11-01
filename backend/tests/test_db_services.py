from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from backend.app.models.monitors import MetricSnapshot, MonitoredBackend
from backend.app.schemas.telegram import WarnThresholds
from backend.app.services import telegram_settings, warnings


@pytest.mark.asyncio
async def test_get_or_create_settings_persists_defaults(db_session, monkeypatch):
    monkeypatch.setattr(
        telegram_settings,
        "settings",
        SimpleNamespace(
            telegram_bot_token="token",
            telegram_default_chat_id="12345",
        ),
        raising=False,
    )

    row = await telegram_settings.get_or_create_settings(db_session)
    assert row.bot_token == "token"
    assert row.default_chat_id == "12345"
    assert row.is_active is True

    same_row = await telegram_settings.get_or_create_settings(db_session)
    assert same_row.id == row.id

    row.warn_thresholds = {"cpu_temperature_c": 91}
    await db_session.commit()

    thresholds = await telegram_settings.get_warn_thresholds(db_session)
    assert thresholds is not None
    assert thresholds.cpu_temperature_c == 91


def _snapshot(
    backend_id: int,
    reported_at: datetime,
    **overrides,
) -> MetricSnapshot:
    base = dict(
        backend_id=backend_id,
        reported_at=reported_at,
        cpu_temperature_c=overrides.get("cpu_temperature_c"),
        ram_used_percent=overrides.get("ram_used_percent"),
        total_ram_gb=overrides.get("total_ram_gb"),
        disk_usage_percent=overrides.get("disk_usage_percent"),
        mounted_usage=overrides.get("mounted_usage"),
        cpu_load=overrides.get("cpu_load"),
        backend_version=overrides.get("backend_version"),
        os_version=overrides.get("os_version"),
        uptime_seconds=overrides.get("uptime_seconds"),
        warnings=overrides.get("warnings"),
        raw_payload=overrides.get("raw_payload", {"sample": True}),
    )
    return MetricSnapshot(**base)


@pytest.mark.asyncio
async def test_recalculate_latest_snapshot_warnings_updates_models(db_session):
    now = datetime.now(tz=timezone.utc)
    backend_one = MonitoredBackend(
        name="alpha",
        base_url="http://alpha",
        api_token="a",
    )
    backend_two = MonitoredBackend(
        name="bravo",
        base_url="http://bravo",
        api_token="b",
    )
    db_session.add_all([backend_one, backend_two])
    await db_session.commit()
    await db_session.refresh(backend_one)
    await db_session.refresh(backend_two)

    db_session.add_all(
        [
            _snapshot(
                backend_one.id,
                now - timedelta(minutes=5),
                ram_used_percent=20,
                raw_payload={"reported_at": "old"},
            ),
            _snapshot(
                backend_one.id,
                now,
                cpu_temperature_c=85.0,
                ram_used_percent=95.0,
                disk_usage_percent=70.0,
                mounted_usage=[
                    {"mount_point": "/data", "used_percent": 92.0},
                    {"mount_point": "/srv", "used_percent": 40.0},
                ],
            ),
            _snapshot(
                backend_two.id,
                now,
                cpu_temperature_c=50.0,
                ram_used_percent=40.0,
                disk_usage_percent=45.0,
            ),
        ]
    )
    await db_session.commit()

    thresholds = WarnThresholds(
        cpu_temperature_c=80.0,
        ram_used_percent=90.0,
        disk_usage_percent=80.0,
        mounted_usage_percent=90.0,
    )

    await warnings.recalculate_latest_snapshot_warnings(db_session, thresholds)

    latest_snapshots = await db_session.execute(
        select(MetricSnapshot).where(MetricSnapshot.backend_id.in_([backend_one.id, backend_two.id]))
    )
    rows = {snapshot.backend_id: snapshot for snapshot in latest_snapshots.scalars()}

    backend_one_snapshot = rows[backend_one.id]
    assert backend_one_snapshot.warnings == [
        "High CPU temperature 85.0°C",
        "High RAM usage 95.0%",
        "/data usage critical at 92.0%",
    ]

    backend_two_snapshot = rows[backend_two.id]
    assert backend_two_snapshot.warnings is None

    await db_session.refresh(backend_one)
    await db_session.refresh(backend_two)
    assert backend_one.last_warning == "; ".join(backend_one_snapshot.warnings)
    assert backend_two.last_warning is None
