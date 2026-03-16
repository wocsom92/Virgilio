from datetime import datetime, timezone

import pytest

from backend.app.models.monitors import MonitoredBackend, MetricSnapshot, QuickStatusItem
from backend.app.services import quick_status


def _snapshot(raw_payload: dict) -> MetricSnapshot:
    return MetricSnapshot(
        backend_id=1,
        reported_at=datetime.now(tz=timezone.utc),
        raw_payload=raw_payload,
    )


def test_metric_value_reads_ssh_payload_fields():
    snapshot = _snapshot(
        {
            "ssh_last_successful_login_seconds": 3661,
            "ssh_last_unsuccessful_attempt_seconds": 7200,
            "ssh_status_level": 1,
        }
    )

    assert quick_status._metric_value(snapshot, "ssh_last_successful_login", None) == 3661
    assert quick_status._metric_value(snapshot, "ssh_last_unsuccessful_attempt", None) == 2
    assert quick_status._metric_value(snapshot, "ssh_status", None) == 1


def test_metric_value_reads_mount_available_gb():
    snapshot = MetricSnapshot(
        backend_id=1,
        reported_at=datetime.now(tz=timezone.utc),
        raw_payload={},
        mounted_usage=[
            {"mount_point": "/data", "total_gb": 100, "used_percent": 90},
            {"mount_point": "/tiny", "total_gb": 0.5, "used_percent": 50},
        ],
    )

    assert quick_status._metric_value(snapshot, "mount_available_gb", "/data") == 10
    assert quick_status._metric_value(snapshot, "mount_available_gb", "/tiny") == 0.25


def test_metric_value_reads_root_mount_available_gb_from_raw_payload():
    snapshot = MetricSnapshot(
        backend_id=1,
        reported_at=datetime.now(tz=timezone.utc),
        raw_payload={"disk_available_gb": 123.45},
        mounted_usage=[{"mount_point": "/data", "total_gb": 100, "used_percent": 90}],
    )

    assert quick_status._metric_value(snapshot, "mount_available_gb", "/") == 123.45


def test_format_value_for_ssh_metrics():
    assert quick_status._format_value("ssh_last_successful_login", 3661) == "1h 1m"
    assert quick_status._format_value("ssh_last_unsuccessful_attempt", 2) == "2h"
    assert quick_status._format_value("ssh_status", 0) == "OK"
    assert quick_status._format_value("ssh_status", 1) == "WARN"
    assert quick_status._format_value("ssh_status", 2) == "CRIT"
    assert quick_status._format_value("cpu_temperature_c", 55.1) == "55.1 °C"


def test_format_value_for_mount_available_gb():
    assert quick_status._format_value("mount_available_gb", 2048) == "2 TiB"
    assert quick_status._format_value("mount_available_gb", 222.54) == "223 GiB"
    assert quick_status._format_value("mount_available_gb", 0.25) == "256 MiB"


def test_resolve_status_for_ssh_metrics():
    assert quick_status._resolve_status(None, 0, 0, "ssh_last_successful_login") == "unknown"
    assert quick_status._resolve_status(0, 0, 0, "ssh_last_successful_login") == "ok"

    assert quick_status._resolve_status(12, 168, 24, "ssh_last_unsuccessful_attempt") == "critical"
    assert quick_status._resolve_status(72, 168, 24, "ssh_last_unsuccessful_attempt") == "warn"
    assert quick_status._resolve_status(240, 168, 24, "ssh_last_unsuccessful_attempt") == "ok"

    assert quick_status._resolve_status(0, 0, 0, "ssh_status") == "ok"
    assert quick_status._resolve_status(1, 0, 0, "ssh_status") == "warn"
    assert quick_status._resolve_status(2, 0, 0, "ssh_status") == "critical"


def test_resolve_status_for_mount_available_gb():
    assert quick_status._resolve_status(0.5, 5, 1, "mount_available_gb") == "critical"
    assert quick_status._resolve_status(3, 5, 1, "mount_available_gb") == "warn"
    assert quick_status._resolve_status(10, 5, 1, "mount_available_gb") == "ok"


def test_resolve_status_for_swap_metric_is_informational_only():
    assert quick_status._resolve_status(None, 50, 80, "swap_used_percent") == "unknown"
    assert quick_status._resolve_status(0, 50, 80, "swap_used_percent") == "info"
    assert quick_status._resolve_status(95, 50, 80, "swap_used_percent") == "info"


@pytest.mark.asyncio
async def test_build_quick_status_tiles_keeps_backend_order_stable(db_session):
    backend_b = MonitoredBackend(
        name="beta",
        base_url="http://beta",
        api_token="token-beta",
        display_order=2,
    )
    backend_a = MonitoredBackend(
        name="alpha",
        base_url="http://alpha",
        api_token="token-alpha",
        display_order=1,
    )
    db_session.add_all([backend_b, backend_a])
    await db_session.flush()

    db_session.add_all(
        [
            MetricSnapshot(
                backend_id=backend_a.id,
                reported_at=datetime.now(tz=timezone.utc),
                raw_payload={},
                ram_used_percent=10,
            ),
            MetricSnapshot(
                backend_id=backend_b.id,
                reported_at=datetime.now(tz=timezone.utc),
                raw_payload={},
                ram_used_percent=20,
            ),
        ]
    )

    item_b = QuickStatusItem(
        backend_id=backend_b.id,
        backend=backend_b,
        label="RAM B",
        metric_key="ram_used_percent",
        warning_threshold=80,
        critical_threshold=90,
        display_order=0,
    )
    item_a = QuickStatusItem(
        backend_id=backend_a.id,
        backend=backend_a,
        label="RAM A",
        metric_key="ram_used_percent",
        warning_threshold=80,
        critical_threshold=90,
        display_order=0,
    )
    db_session.add_all([item_b, item_a])
    await db_session.commit()

    tiles = await quick_status.build_quick_status_tiles(db_session, [item_b, item_a])

    assert [(tile.backend_name, tile.label) for tile in tiles] == [
        ("alpha", "RAM A"),
        ("beta", "RAM B"),
    ]
    assert [tile.backend_display_order for tile in tiles] == [1, 2]


@pytest.mark.asyncio
async def test_build_quick_status_tiles_include_ssh_action_details(db_session):
    backend = MonitoredBackend(
        name="alpha",
        base_url="http://alpha",
        api_token="token-alpha",
        display_order=1,
    )
    db_session.add(backend)
    await db_session.flush()
    db_session.add(
        MetricSnapshot(
            backend_id=backend.id,
            reported_at=datetime.now(tz=timezone.utc),
            raw_payload={
                "ssh_status_level": 2,
                "ssh_pubkey_auth_enabled": False,
                "ssh_password_auth_disabled": False,
                "ssh_kbd_interactive_auth_disabled": False,
                "ssh_permit_root_login_mode": "yes",
                "ssh_pubkey_auth_line": "PubkeyAuthentication no",
                "ssh_password_auth_line": "PasswordAuthentication yes",
                "ssh_kbd_interactive_auth_line": "KbdInteractiveAuthentication yes",
                "ssh_permit_root_login_line": "PermitRootLogin yes",
            },
        )
    )
    item = QuickStatusItem(
        backend_id=backend.id,
        backend=backend,
        label="SSH",
        metric_key="ssh_status",
        warning_threshold=0,
        critical_threshold=0,
        display_order=0,
    )
    db_session.add(item)
    await db_session.commit()

    tiles = await quick_status.build_quick_status_tiles(db_session, [item])

    assert tiles[0].details is not None
    assert any(line.text == "PubkeyAuthentication no" and line.severity == "warn" for line in tiles[0].details)
    assert any(line.text == "PermitRootLogin yes" and line.severity == "critical" for line in tiles[0].details)
