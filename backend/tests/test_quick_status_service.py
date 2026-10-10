from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.app.models.monitors import (
    MonitoredBackend,
    MetricSnapshot,
    QuickStatusItem,
    QuickStatusPingSample,
    QuickStatusPrometheusSample,
)
from backend.app.services import telegram_settings
from backend.app.services.prometheus_client import PrometheusQueryValue
from backend.app.services import quick_status


@pytest.fixture(autouse=True)
def quick_status_zero_alert_confirmation(monkeypatch):
    real_get = telegram_settings.get_or_create_settings

    async def wrapped(session):
        row = await real_get(session)
        row.quick_status_alert_confirmation_minutes = 0
        return row

    monkeypatch.setattr(quick_status, "get_or_create_settings", wrapped)


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


def test_metric_value_reads_device_status_payload_fields():
    snapshot = _snapshot(
        {
            "telegram_bot_status_level": 1,
            "wifi_status_level": 0,
            "vpn_status_level": 2,
        }
    )

    assert quick_status._metric_value(snapshot, "telegram_bot_status", None) == 1
    assert quick_status._metric_value(snapshot, "wifi_status", None) == 0
    assert quick_status._metric_value(snapshot, "vpn_status", None) == 2


@pytest.mark.asyncio
async def test_build_quick_status_tile_summaries_returns_lightweight_tiles(db_session):
    backend = MonitoredBackend(
        name="alpha",
        base_url="http://alpha",
        api_token="token",
        display_order=0,
    )
    db_session.add(backend)
    await db_session.flush()

    snapshot = MetricSnapshot(
        backend_id=backend.id,
        reported_at=datetime.now(tz=timezone.utc),
        ram_used_percent=42,
        raw_payload={"sample": True},
    )
    item = QuickStatusItem(
        backend_id=backend.id,
        label="RAM usage",
        metric_key="ram_used_percent",
        warning_threshold=80,
        critical_threshold=90,
        display_order=0,
    )
    item.backend = backend
    db_session.add_all([snapshot, item])
    await db_session.commit()

    tiles = await quick_status.build_quick_status_tile_summaries(
        db_session,
        [item],
        include_heartbeat_tiles=False,
        backends=[backend],
    )

    assert len(tiles) == 1
    tile = tiles[0]
    assert tile.display_value == "42%"
    assert tile.history_loaded is False
    assert tile.history == []
    assert tile.series == []
    assert tile.details is None
    assert tile.has_details is False


@pytest.mark.asyncio
async def test_build_quick_status_tile_summaries_coerces_has_details_to_bool(db_session):
    backend = MonitoredBackend(
        name="alpha",
        base_url="http://alpha",
        api_token="token",
        display_order=0,
    )
    db_session.add(backend)
    await db_session.flush()

    db_session.add(
        MetricSnapshot(
            backend_id=backend.id,
            reported_at=datetime.now(tz=timezone.utc),
            raw_payload={
                "ssh_last_unsuccessful_attempt_seconds": 3600,
                "ssh_last_failure_auth_method": "invalid-user",
            },
        )
    )
    item = QuickStatusItem(
        backend_id=backend.id,
        label="SSH failed login",
        metric_key="ssh_last_unsuccessful_attempt",
        warning_threshold=168,
        critical_threshold=24,
        display_order=0,
    )
    item.backend = backend
    db_session.add(item)
    await db_session.commit()

    tiles = await quick_status.build_quick_status_tile_summaries(
        db_session,
        [item],
        include_heartbeat_tiles=False,
        backends=[backend],
    )

    assert len(tiles) == 1
    assert tiles[0].has_details is True
    assert isinstance(tiles[0].has_details, bool)


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
    assert quick_status._format_value("telegram_bot_status", 0) == "OK"
    assert quick_status._format_value("wifi_status", 1) == "WARN"
    assert quick_status._format_value("vpn_status", 2) == "CRIT"
    assert quick_status._format_value("cpu_temperature_c", 55.1) == "55.1 °C"


def test_format_value_for_mount_available_gb():
    assert quick_status._format_value("mount_available_gb", 2048) == "2 TiB"
    assert quick_status._format_value("mount_available_gb", 222.54) == "223 GiB"
    assert quick_status._format_value("mount_available_gb", 0.25) == "256 MiB"


def test_format_item_value_for_prometheus_units_and_humidity_variant():
    humidity_item = QuickStatusItem(
        label="Humidity",
        metric_key="prometheus_query",
        warning_threshold=55,
        critical_threshold=60,
        lower_warning_threshold=40,
        lower_critical_threshold=35,
        prometheus_url="http://prometheus:9090",
        prometheus_query="room_humidity_percent",
        display_variant="humidity",
        display_unit="%",
    )
    graph_item = QuickStatusItem(
        label="Latency",
        metric_key="prometheus_query",
        warning_threshold=150,
        critical_threshold=300,
        prometheus_url="http://prometheus:9090",
        prometheus_query="http_latency_ms",
        display_variant="graph",
        display_unit="ms",
    )

    assert quick_status._format_item_value(humidity_item, 48.6) == "49%"
    assert quick_status._format_item_value(graph_item, 123.4) == "123.4ms"


def test_resolve_status_for_ssh_metrics():
    assert quick_status._resolve_status(None, 0, 0, "ssh_last_successful_login") == "unknown"
    assert quick_status._resolve_status(0, 0, 0, "ssh_last_successful_login") == "ok"

    assert quick_status._resolve_status(12, 168, 24, "ssh_last_unsuccessful_attempt") == "critical"
    assert quick_status._resolve_status(72, 168, 24, "ssh_last_unsuccessful_attempt") == "warn"
    assert quick_status._resolve_status(240, 168, 24, "ssh_last_unsuccessful_attempt") == "ok"

    assert quick_status._resolve_status(0, 0, 0, "ssh_status") == "ok"
    assert quick_status._resolve_status(1, 0, 0, "ssh_status") == "warn"
    assert quick_status._resolve_status(2, 0, 0, "ssh_status") == "critical"
    assert quick_status._resolve_status(0, 0, 0, "telegram_bot_status") == "ok"
    assert quick_status._resolve_status(1, 0, 0, "wifi_status") == "warn"
    assert quick_status._resolve_status(2, 0, 0, "vpn_status") == "critical"


def test_build_detail_lines_for_device_status_tiles():
    snapshot = _snapshot(
        {
            "telegram_bot_running": True,
            "telegram_egress_proxy_running": True,
            "wifi_ssid": "lab-wifi",
            "wifi_interface": "wlan0",
            "wifi_client_count": 2,
            "wifi_associated_clients": [
                {"mac": "aa:bb:cc:dd:ee:01", "signal_dbm": -45},
                {"mac": "aa:bb:cc:dd:ee:02"},
            ],
            "wifi_dhcp_lease_count": 3,
            "wifi_operstate": "up",
            "wifi_hostapd_socket_present": True,
            "vpn_active_backend": "wg0",
            "vpn_hotspot_public_ip": "198.51.100.27",
            "vpn_hotspot_probe_service": "ifconfig.me",
            "vpn_hotspot_country_code": "DE",
            "vpn_expected_public_ip": "198.51.100.27",
            "vpn_expected_probe_service": "icanhazip",
            "vpn_expected_country_code": "DE",
            "vpn_default_public_ip": "203.0.113.42",
            "vpn_default_probe_service": "ipify",
            "vpn_default_country_code": "US",
        }
    )

    telegram_item = QuickStatusItem(label="Telegram", metric_key="telegram_bot_status", warning_threshold=0, critical_threshold=0)
    wifi_item = QuickStatusItem(label="Wi-Fi", metric_key="wifi_status", warning_threshold=0, critical_threshold=0)
    vpn_item = QuickStatusItem(label="VPN", metric_key="vpn_status", warning_threshold=0, critical_threshold=0)

    telegram_lines = quick_status._build_detail_lines(telegram_item, snapshot, status="ok")
    wifi_lines = quick_status._build_detail_lines(wifi_item, snapshot, status="ok")
    vpn_lines = quick_status._build_detail_lines(vpn_item, snapshot, status="ok")

    assert telegram_lines is not None
    assert [line.text for line in telegram_lines] == [
        "Bot container: running",
        "Egress proxy: running",
    ]
    assert wifi_lines is not None
    assert [line.text for line in wifi_lines] == [
        "SSID: lab-wifi",
        "Interface: wlan0",
        "Operstate: up",
        "hostapd socket: present",
        "Associated clients: 2",
        "Client: aa:bb:cc:dd:ee:01 · -45 dBm",
        "Client: aa:bb:cc:dd:ee:02",
        "DHCP leases: 3",
    ]
    assert vpn_lines is not None
    assert [line.text for line in vpn_lines] == [
        "Active backend: wg0",
        "Hotspot egress IP: 198.51.100.27 (DE) via ifconfig.me",
        "Backend egress IP: 198.51.100.27 (DE) via icanhazip",
        "Host default IP: 203.0.113.42 (US) via ipify",
    ]


def test_resolve_status_for_mount_available_gb():
    assert quick_status._resolve_status(0.5, 5, 1, "mount_available_gb") == "critical"
    assert quick_status._resolve_status(3, 5, 1, "mount_available_gb") == "warn"
    assert quick_status._resolve_status(10, 5, 1, "mount_available_gb") == "ok"


def test_resolve_status_for_swap_metric_is_informational_only():
    assert quick_status._resolve_status(None, 50, 80, "swap_used_percent") == "unknown"
    assert quick_status._resolve_status(0, 50, 80, "swap_used_percent") == "info"
    assert quick_status._resolve_status(95, 50, 80, "swap_used_percent") == "info"


def test_resolve_status_for_prometheus_lower_thresholds():
    assert quick_status._resolve_status(5, 10, 3, "prometheus_query", prometheus_threshold_direction="lower") == "warn"
    assert quick_status._resolve_status(2, 10, 3, "prometheus_query", prometheus_threshold_direction="lower") == "critical"
    assert quick_status._resolve_status(20, 10, 3, "prometheus_query", prometheus_threshold_direction="lower") == "ok"


def test_resolve_status_for_temperature_band_thresholds():
    assert (
        quick_status._resolve_status(
            17,
            26,
            28,
            "prometheus_query",
            lower_warning_threshold=18,
            lower_critical_threshold=16,
        )
        == "warn"
    )
    assert (
        quick_status._resolve_status(
            15,
            26,
            28,
            "prometheus_query",
            lower_warning_threshold=18,
            lower_critical_threshold=16,
        )
        == "critical"
    )
    assert (
        quick_status._resolve_status(
            27,
            26,
            28,
            "prometheus_query",
            lower_warning_threshold=18,
            lower_critical_threshold=16,
        )
        == "warn"
    )
    assert (
        quick_status._resolve_status(
            29,
            26,
            28,
            "prometheus_query",
            lower_warning_threshold=18,
            lower_critical_threshold=16,
        )
        == "critical"
    )
    assert (
        quick_status._resolve_status(
            22,
            26,
            28,
            "prometheus_query",
            lower_warning_threshold=18,
            lower_critical_threshold=16,
        )
        == "ok"
    )


def test_is_prometheus_result_stale_after_thirty_minutes():
    sampled_at = datetime(2026, 3, 30, 10, 0, tzinfo=timezone.utc)
    result = quick_status.PrometheusCheckResult(
        checked_at=sampled_at + timedelta(minutes=1),
        sampled_at=sampled_at,
        value=23.1,
    )

    assert quick_status._is_prometheus_result_stale(result, now=sampled_at + timedelta(minutes=30)) is False
    assert quick_status._is_prometheus_result_stale(result, now=sampled_at + timedelta(minutes=31)) is True


def test_reference_stale_after_thirty_minutes():
    reported_at = datetime(2026, 3, 30, 10, 0, tzinfo=timezone.utc)

    assert quick_status._is_reference_stale(reported_at, now=reported_at + timedelta(minutes=30)) is False
    assert quick_status._is_reference_stale(reported_at, now=reported_at + timedelta(minutes=31)) is True


def test_build_prometheus_series_points_pads_window_and_marks_gaps():
    window_start = datetime(2026, 4, 2, 12, 0, tzinfo=timezone.utc)
    window_end = window_start + timedelta(hours=24)
    samples = [
        QuickStatusPrometheusSample(
            quick_status_item_id=1,
            checked_at=window_start + timedelta(hours=1),
            sampled_at=window_start + timedelta(hours=1),
            value=45.0,
        ),
        QuickStatusPrometheusSample(
            quick_status_item_id=1,
            checked_at=window_start + timedelta(hours=2),
            sampled_at=window_start + timedelta(hours=2),
            value=46.0,
        ),
        QuickStatusPrometheusSample(
            quick_status_item_id=1,
            checked_at=window_start + timedelta(hours=6),
            sampled_at=window_start + timedelta(hours=6),
            value=49.0,
        ),
    ]

    points = quick_status._build_prometheus_series_points(
        samples,
        window_start,
        window_end,
        interval_seconds=3600,
    )

    assert [(point.recorded_at, point.value) for point in points] == [
        (window_start, None),
        (window_start + timedelta(hours=1), 45.0),
        (window_start + timedelta(hours=2), 46.0),
        (window_start + timedelta(hours=6), None),
        (window_start + timedelta(hours=6), 49.0),
        (window_end, None),
    ]

    summary = quick_status._build_prometheus_series_summary(points)
    assert summary is not None
    assert summary.min_value == 45.0
    assert summary.max_value == 49.0
    assert summary.average_value == pytest.approx((45.0 + 46.0 + 49.0) / 3)
    assert summary.delta_value == pytest.approx(4.0)


def test_build_prometheus_series_points_connects_sparse_samples_until_stale_gap():
    window_start = datetime(2026, 4, 2, 12, 0, tzinfo=timezone.utc)
    window_end = window_start + timedelta(hours=1)
    samples = [
        QuickStatusPrometheusSample(
            quick_status_item_id=1,
            checked_at=window_start + timedelta(minutes=1),
            sampled_at=window_start + timedelta(minutes=1),
            value=22.0,
        ),
        QuickStatusPrometheusSample(
            quick_status_item_id=1,
            checked_at=window_start + timedelta(minutes=8),
            sampled_at=window_start + timedelta(minutes=8),
            value=22.5,
        ),
        QuickStatusPrometheusSample(
            quick_status_item_id=1,
            checked_at=window_start + timedelta(minutes=39),
            sampled_at=window_start + timedelta(minutes=39),
            value=23.0,
        ),
    ]

    points = quick_status._build_prometheus_series_points(
        samples,
        window_start,
        window_end,
        interval_seconds=60,
    )

    assert [(point.recorded_at, point.value) for point in points] == [
        (window_start, None),
        (window_start + timedelta(minutes=1), 22.0),
        (window_start + timedelta(minutes=8), 22.5),
        (window_start + timedelta(minutes=39), None),
        (window_start + timedelta(minutes=39), 23.0),
        (window_end, None),
    ]


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


@pytest.mark.asyncio
async def test_build_quick_status_tiles_include_ssh_failure_details(db_session):
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
                "ssh_last_unsuccessful_attempt_seconds": 3600,
                "ssh_last_failure_auth_method": "publickey",
                "ssh_last_failure_username": "root",
                "ssh_last_failure_source_ip": "192.0.2.8",
                "ssh_last_failure_port": 55123,
                "ssh_last_failure_line": "Failed publickey for root from 192.0.2.8 port 55123 ssh2",
            },
        )
    )
    item = QuickStatusItem(
        backend_id=backend.id,
        backend=backend,
        label="SSH failed login",
        metric_key="ssh_last_unsuccessful_attempt",
        warning_threshold=168,
        critical_threshold=24,
        display_order=0,
    )
    db_session.add(item)
    await db_session.commit()

    tiles = await quick_status.build_quick_status_tiles(db_session, [item])

    assert tiles[0].status == "critical"
    assert tiles[0].details is not None
    assert any(line.text == "Method: publickey" and line.severity == "critical" for line in tiles[0].details)
    assert any(line.text == "User: root" and line.severity == "critical" for line in tiles[0].details)
    assert any(line.text == "Source: 192.0.2.8" and line.severity == "critical" for line in tiles[0].details)
    assert any(line.text == "Port: 55123" and line.severity == "critical" for line in tiles[0].details)
    assert any(
        line.text == "Log: Failed publickey for root from 192.0.2.8 port 55123 ssh2"
        and line.severity == "critical"
        for line in tiles[0].details
    )


@pytest.mark.asyncio
async def test_build_quick_status_tiles_marks_metric_tiles_stale_when_snapshot_is_old(db_session):
    now = datetime(2026, 3, 30, 12, 0, tzinfo=timezone.utc)
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
            reported_at=now - timedelta(minutes=31),
            raw_payload={},
            ram_used_percent=91,
        )
    )
    item = QuickStatusItem(
        backend_id=backend.id,
        backend=backend,
        label="RAM",
        metric_key="ram_used_percent",
        warning_threshold=80,
        critical_threshold=90,
        display_order=0,
    )
    db_session.add(item)
    await db_session.commit()

    tiles = await quick_status.build_quick_status_tiles(
        db_session,
        [item],
        now=now,
    )

    assert tiles[0].status == "critical"
    assert tiles[0].stale is True
    assert tiles[0].reported_at == now - timedelta(minutes=31)


@pytest.mark.asyncio
async def test_build_quick_status_tiles_history_uses_longest_status_duration_per_bucket(db_session):
    now = datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc)
    backend = MonitoredBackend(
        name="alpha",
        base_url="http://alpha",
        api_token="token-alpha",
        display_order=1,
        poll_interval_seconds=60,
        last_seen_at=now,
    )
    db_session.add(backend)
    await db_session.flush()
    db_session.add_all(
        [
            MetricSnapshot(
                backend_id=backend.id,
                reported_at=now.replace(day=27, hour=11),
                raw_payload={},
                ram_used_percent=20,
            ),
            MetricSnapshot(
                backend_id=backend.id,
                reported_at=now.replace(hour=10, minute=0),
                raw_payload={},
                ram_used_percent=95,
            ),
            MetricSnapshot(
                backend_id=backend.id,
                reported_at=now.replace(hour=11, minute=0),
                raw_payload={},
                ram_used_percent=85,
            ),
            MetricSnapshot(
                backend_id=backend.id,
                reported_at=now.replace(hour=11, minute=30),
                raw_payload={},
                ram_used_percent=None,
            ),
        ]
    )
    item = QuickStatusItem(
        backend_id=backend.id,
        backend=backend,
        label="RAM",
        metric_key="ram_used_percent",
        warning_threshold=80,
        critical_threshold=90,
        display_order=0,
    )
    db_session.add(item)
    await db_session.commit()

    tiles = await quick_status.build_quick_status_tiles(db_session, [item], now=now)

    assert tiles[0].history == ["ok"] * 11 + ["critical"]


@pytest.mark.asyncio
async def test_build_quick_status_tiles_can_include_heartbeat_tiles(db_session):
    now = datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc)
    backend = MonitoredBackend(
        name="alpha",
        base_url="http://alpha",
        api_token="token-alpha",
        display_order=1,
        poll_interval_seconds=3600,
        last_seen_at=now,
    )
    db_session.add(backend)
    await db_session.flush()
    db_session.add_all(
        [
            MetricSnapshot(
                backend_id=backend.id,
                reported_at=now.replace(day=27, hour=11),
                raw_payload={},
                ram_used_percent=20,
            ),
            MetricSnapshot(
                backend_id=backend.id,
                reported_at=now.replace(hour=10, minute=30),
                raw_payload={},
                ram_used_percent=20,
            ),
        ]
    )
    await db_session.commit()

    tiles = await quick_status.build_quick_status_tiles(
        db_session,
        [],
        include_heartbeat_tiles=True,
        backends=[backend],
        now=now,
    )

    assert [tile.label for tile in tiles] == ["HB"]
    assert tiles[0].status == "ok"
    assert len(tiles[0].history) == 12
    assert tiles[0].history[-1] == "ok"


@pytest.mark.asyncio
async def test_build_quick_status_tiles_aggregates_stored_ping_history(db_session):
    now = datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc)
    backend = MonitoredBackend(
        name="alpha",
        base_url="http://alpha",
        api_token="token-alpha",
        display_order=1,
    )
    db_session.add(backend)
    await db_session.flush()
    item = QuickStatusItem(
        backend_id=backend.id,
        backend=backend,
        label="Ping",
        metric_key="ping_delay_ms",
        warning_threshold=100,
        critical_threshold=200,
        ping_endpoint="https://example.com/health",
        ping_interval_seconds=7200,
        display_order=0,
    )
    db_session.add(item)
    await db_session.flush()
    db_session.add_all(
        [
            QuickStatusPingSample(
                quick_status_item_id=item.id,
                checked_at=now.replace(day=27, hour=11),
                success=True,
                latency_ms=50,
            ),
            QuickStatusPingSample(
                quick_status_item_id=item.id,
                checked_at=now.replace(hour=10, minute=0),
                success=False,
                latency_ms=None,
            ),
            QuickStatusPingSample(
                quick_status_item_id=item.id,
                checked_at=now.replace(hour=11, minute=0),
                success=True,
                latency_ms=120,
            ),
            QuickStatusPingSample(
                quick_status_item_id=item.id,
                checked_at=now.replace(hour=11, minute=30),
                success=True,
                latency_ms=50,
            ),
        ]
    )
    await db_session.commit()

    tiles = await quick_status.build_quick_status_tiles(db_session, [item], now=now)

    assert tiles[0].history == ["ok"] * 11 + ["critical"]
    assert tiles[0].display_value == "50ms"
    assert tiles[0].status == "ok"


@pytest.mark.asyncio
async def test_build_quick_status_tiles_persists_new_ping_samples(db_session, monkeypatch):
    backend = MonitoredBackend(
        name="alpha",
        base_url="http://alpha",
        api_token="token-alpha",
        display_order=1,
    )
    db_session.add(backend)
    await db_session.flush()
    item = QuickStatusItem(
        backend_id=backend.id,
        backend=backend,
        label="Ping",
        metric_key="ping_result",
        warning_threshold=0,
        critical_threshold=0,
        ping_endpoint="https://example.com/health",
        ping_interval_seconds=300,
        display_order=0,
    )
    db_session.add(item)
    await db_session.commit()

    async def fake_fetch_ping(base_url: str, token: str, target: str, timeout_seconds: int) -> dict:
        assert base_url == "http://alpha"
        assert token == "token-alpha"
        assert target == "https://example.com/health"
        return {"success": False, "latency_ms": None}

    monkeypatch.setattr(quick_status, "fetch_ping", fake_fetch_ping)

    persisted = await quick_status.refresh_due_quick_status_samples(db_session, [item])
    assert persisted == 1
    # A second refresh inside the interval is a no-op (cache decides due-ness, no DB work).
    assert await quick_status.refresh_due_quick_status_samples(db_session, [item]) == 0
    tiles = await quick_status.build_quick_status_tiles(db_session, [item])

    samples = (
        await db_session.execute(
            select(QuickStatusPingSample)
            .where(QuickStatusPingSample.quick_status_item_id == item.id)
            .order_by(QuickStatusPingSample.checked_at.asc(), QuickStatusPingSample.id.asc())
        )
    ).scalars().all()

    assert tiles[0].status == "critical"
    assert tiles[0].display_value == "NOK"
    assert len(samples) == 1
    assert samples[0].success is False


@pytest.mark.asyncio
async def test_build_quick_status_tiles_queries_prometheus_and_persists_history(db_session, monkeypatch):
    now = datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc)
    backend = MonitoredBackend(
        name="alpha",
        base_url="http://alpha",
        api_token="token-alpha",
        display_order=1,
    )
    db_session.add(backend)
    await db_session.flush()
    item = QuickStatusItem(
        backend_id=backend.id,
        backend=backend,
        label="5xx rate",
        metric_key="prometheus_query",
        warning_threshold=5,
        critical_threshold=10,
        prometheus_url="http://prometheus:9090",
        prometheus_query='sum(rate(http_requests_total{status=~"5.."}[5m]))',
        prometheus_interval_seconds=300,
        prometheus_threshold_direction="higher",
        display_order=0,
    )
    db_session.add(item)
    await db_session.commit()

    async def fake_fetch_prometheus_query_value(base_url: str, query: str):
        assert base_url == "http://prometheus:9090"
        assert query == 'sum(rate(http_requests_total{status=~"5.."}[5m]))'
        return PrometheusQueryValue(
            value=12.4,
            sampled_at=datetime(2026, 3, 28, 11, 59, tzinfo=timezone.utc),
        )

    monkeypatch.setattr(quick_status, "fetch_prometheus_query_value", fake_fetch_prometheus_query_value)

    assert await quick_status.refresh_due_quick_status_samples(db_session, [item], now=now) == 1
    tiles = await quick_status.build_quick_status_tiles(db_session, [item], now=now)

    samples = (
        await db_session.execute(
            select(QuickStatusPrometheusSample)
            .where(QuickStatusPrometheusSample.quick_status_item_id == item.id)
            .order_by(QuickStatusPrometheusSample.checked_at.asc(), QuickStatusPrometheusSample.id.asc())
        )
    ).scalars().all()

    assert tiles[0].status == "critical"
    assert tiles[0].display_value == "12.4"
    assert tiles[0].details is not None
    assert any("Endpoint: http://prometheus:9090" == line.text for line in tiles[0].details)
    assert any('Query: sum(rate(http_requests_total{status=~"5.."}[5m]))' == line.text for line in tiles[0].details)
    assert len(samples) == 1
    assert samples[0].value == pytest.approx(12.4)
    assert quick_status._coerce_utc(samples[0].sampled_at) == datetime(2026, 3, 28, 11, 59, tzinfo=timezone.utc)
    assert samples[0].error_message is None


@pytest.mark.asyncio
async def test_build_quick_status_tiles_marks_stale_prometheus_metric_with_stale_flag(db_session, monkeypatch):
    now = datetime(2026, 3, 30, 12, 0, tzinfo=timezone.utc)
    backend = MonitoredBackend(
        name="alpha",
        base_url="http://alpha",
        api_token="token-alpha",
        display_order=1,
    )
    db_session.add(backend)
    await db_session.flush()
    item = QuickStatusItem(
        backend_id=backend.id,
        backend=backend,
        label="Room temp",
        metric_key="prometheus_query",
        warning_threshold=26,
        critical_threshold=28,
        prometheus_url="http://prometheus:9090",
        prometheus_query='room_temperature_celsius{room="bedroom"}',
        prometheus_interval_seconds=300,
        prometheus_threshold_direction="higher",
        display_order=0,
    )
    db_session.add(item)
    await db_session.commit()

    async def fake_fetch_prometheus_query_value(base_url: str, query: str):
        assert base_url == "http://prometheus:9090"
        assert query == 'room_temperature_celsius{room="bedroom"}'
        return PrometheusQueryValue(
            value=23.4,
            sampled_at=now - timedelta(minutes=31),
        )

    monkeypatch.setattr(quick_status, "fetch_prometheus_query_value", fake_fetch_prometheus_query_value)

    await quick_status.refresh_due_quick_status_samples(db_session, [item], now=now)
    tiles = await quick_status.build_quick_status_tiles(db_session, [item], now=now)

    assert tiles[0].status == "ok"
    assert tiles[0].stale is True
    assert tiles[0].display_value == "23.4"
    assert tiles[0].reported_at == now - timedelta(minutes=31)
    assert tiles[0].details is not None
    assert any(line.text == "Stale: no Prometheus update in the last 30 minutes" for line in tiles[0].details)


@pytest.mark.asyncio
async def test_build_quick_status_tiles_aggregates_stored_prometheus_history(db_session):
    now = datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc)
    backend = MonitoredBackend(
        name="alpha",
        base_url="http://alpha",
        api_token="token-alpha",
        display_order=1,
    )
    db_session.add(backend)
    await db_session.flush()
    item = QuickStatusItem(
        backend_id=backend.id,
        backend=backend,
        label="Queue depth",
        metric_key="prometheus_query",
        warning_threshold=10,
        critical_threshold=20,
        prometheus_url="http://prometheus:9090",
        prometheus_query='queue_depth{queue="jobs"}',
        prometheus_interval_seconds=7200,
        prometheus_threshold_direction="higher",
        display_order=0,
    )
    db_session.add(item)
    await db_session.flush()
    db_session.add_all(
        [
            QuickStatusPrometheusSample(
                quick_status_item_id=item.id,
                checked_at=now.replace(day=27, hour=11),
                value=2,
                error_message=None,
            ),
            QuickStatusPrometheusSample(
                quick_status_item_id=item.id,
                checked_at=now.replace(hour=10, minute=0),
                value=25,
                error_message=None,
            ),
            QuickStatusPrometheusSample(
                quick_status_item_id=item.id,
                checked_at=now.replace(hour=11, minute=0),
                value=12,
                error_message=None,
            ),
            QuickStatusPrometheusSample(
                quick_status_item_id=item.id,
                checked_at=now.replace(hour=11, minute=30),
                value=4,
                error_message=None,
            ),
        ]
    )
    await db_session.commit()

    tiles = await quick_status.build_quick_status_tiles(
        db_session,
        [item],
        now=now,
    )

    assert tiles[0].history == ["ok"] * 11 + ["critical"]
    assert tiles[0].display_value == "4"
    assert tiles[0].status == "ok"


@pytest.mark.asyncio
async def test_build_quick_status_tiles_includes_prometheus_series_for_temperature_items(db_session, monkeypatch):
    backend = MonitoredBackend(
        name="home",
        base_url="http://home",
        api_token="token",
        display_order=0,
    )
    db_session.add(backend)
    await db_session.flush()

    item = QuickStatusItem(
        backend_id=backend.id,
        label="Room temperature",
        metric_key="prometheus_query",
        display_variant="temperature",
        lower_warning_threshold=18,
        lower_critical_threshold=16,
        warning_threshold=26,
        critical_threshold=28,
        prometheus_url="http://prometheus:9090",
        prometheus_query="room_temperature_celsius",
        display_order=0,
    )
    db_session.add(item)
    await db_session.flush()

    now = datetime(2026, 4, 3, 12, 0, tzinfo=timezone.utc)
    db_session.add_all(
        [
            QuickStatusPrometheusSample(
                quick_status_item_id=item.id,
                checked_at=now - timedelta(hours=3),
                sampled_at=now - timedelta(hours=3),
                value=21.5,
            ),
            QuickStatusPrometheusSample(
                quick_status_item_id=item.id,
                checked_at=now - timedelta(hours=1),
                sampled_at=now - timedelta(hours=1),
                value=23.0,
            ),
        ]
    )
    await db_session.flush()

    async def fake_fetch_prometheus_query_value(base_url: str, query: str):
        return PrometheusQueryValue(value=22.4, sampled_at=now)

    monkeypatch.setattr(quick_status, "fetch_prometheus_query_value", fake_fetch_prometheus_query_value)

    await quick_status.refresh_due_quick_status_samples(db_session, [item], now=now)
    tiles = await quick_status.build_quick_status_tiles(db_session, [item], now=now)

    assert len(tiles) == 1
    tile = tiles[0]
    assert tile.display_variant == "temperature"
    assert tile.display_value == "22.4 °C"
    assert tile.series[0].recorded_at == now - timedelta(hours=24)
    assert tile.series[0].value is None
    assert [point.value for point in tile.series if point.value is not None] == [21.5, 23.0, 22.4]
    assert tile.series_summary is not None
    assert tile.series_summary.min_value == 21.5
    assert tile.series_summary.max_value == 23.0
    assert tile.series_summary.delta_value == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_quick_status_alert_suppression_keeps_ok_until_confirmation_elapses(db_session, monkeypatch):
    monkeypatch.setattr(quick_status, "get_or_create_settings", telegram_settings.get_or_create_settings)

    settings = await telegram_settings.get_or_create_settings(db_session)
    settings.quick_status_alert_confirmation_minutes = 5
    await db_session.commit()

    now = datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc)
    backend = MonitoredBackend(
        name="alpha",
        base_url="http://alpha",
        api_token="token-alpha",
        display_order=1,
        poll_interval_seconds=60,
        last_seen_at=now,
    )
    db_session.add(backend)
    await db_session.flush()
    db_session.add(
        MetricSnapshot(
            backend_id=backend.id,
            reported_at=now,
            raw_payload={},
            ram_used_percent=95,
        )
    )
    item = QuickStatusItem(
        backend_id=backend.id,
        backend=backend,
        label="RAM",
        metric_key="ram_used_percent",
        warning_threshold=80,
        critical_threshold=90,
        display_order=0,
    )
    db_session.add(item)
    await db_session.commit()

    # Dashboard reads never write: without the poller's bookkeeping the alert stays hidden.
    tiles = await quick_status.build_quick_status_tiles(db_session, [item], now=now)
    assert tiles[0].status == "ok"
    assert item.alert_condition_started_at is None

    await quick_status.refresh_quick_status_alert_state(db_session, now=now)
    assert quick_status._coerce_utc(item.alert_condition_started_at) == now
    tiles = await quick_status.build_quick_status_tiles(db_session, [item], now=now)
    assert tiles[0].status == "ok"

    later = now + timedelta(minutes=6)
    await quick_status.refresh_quick_status_alert_state(db_session, now=later)
    tiles = await quick_status.build_quick_status_tiles(db_session, [item], now=later)
    assert tiles[0].status == "critical"

    # Once the condition clears the poller resets the marker.
    snapshot = (await db_session.execute(select(MetricSnapshot))).scalars().first()
    snapshot.ram_used_percent = 10
    await db_session.commit()
    await quick_status.refresh_quick_status_alert_state(db_session, now=later)
    assert item.alert_condition_started_at is None
