from datetime import datetime, timedelta, timezone

from backend.app.services.series import SeriesRow, downsample_series, reboot_markers


def _row(at: datetime, value: float | None, *, sent: float = 0, uptime: int | None = None) -> SeriesRow:
    return SeriesRow(
        reported_at=at,
        cpu_temperature_c=value,
        ram_used_percent=value,
        memory_available_gb=None,
        swap_used_percent=None,
        disk_usage_percent=None,
        cpu_load={"one": value, "five": None, "fifteen": None} if value is not None else None,
        mounted_usage=[{"mount_point": "/", "used_percent": value}],
        disk_temperatures=[{"device": "sda", "temperature_c": value}],
        network_counters=[{"interface": "eth0", "bytes_sent": sent, "bytes_recv": 0}],
        uptime_seconds=uptime,
    )


def test_small_windows_are_returned_untouched():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [_row(start + timedelta(minutes=i), float(i)) for i in range(5)]
    points, bucket = downsample_series(rows, start, start + timedelta(hours=1), interfaces=["eth0"], max_points=300)
    assert bucket is None
    assert [point.ram_used_percent for point in points] == [0.0, 1.0, 2.0, 3.0, 4.0]


def test_large_windows_are_bucketed_with_mean_last_and_counter_deltas():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)
    # 60 rows, one per minute, 1 MB sent per minute.
    rows = [_row(start + timedelta(minutes=i), float(i), sent=i * 1_000_000) for i in range(60)]
    points, bucket = downsample_series(rows, start, end, interfaces=["eth0"], max_points=6)

    assert bucket == 600
    assert len(points) == 6
    first, second = points[0], points[1]
    # Bucket 0 holds minutes 0-9: mean 4.5, last value 9.
    assert first.ram_used_percent == 4.5
    assert first.cpu_load.one == 4.5
    assert first.mounted_usage[0].used_percent == 9.0
    assert first.disk_temperatures[0].temperature_c == 9.0
    assert first.network_bps[0]["tx_bps"] is None  # no previous bucket
    # Bucket 1 delta: minutes 9 -> 19 = 10 MB over 600 s = 133,333 bps
    assert round(second.network_bps[0]["tx_bps"]) == round(10_000_000 * 8 / 600)
    assert second.reported_at == start + timedelta(minutes=19)


def test_buckets_average_only_present_values():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [_row(start + timedelta(minutes=i), None if i % 2 else 10.0) for i in range(20)]
    points, _ = downsample_series(rows, start, start + timedelta(hours=1), interfaces=[], max_points=2)
    assert all(point.ram_used_percent == 10.0 for point in points)
    assert all(point.network_bps is None for point in points)


def test_reboot_markers_come_from_raw_rows():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [
        _row(start, 1.0, uptime=5000),
        _row(start + timedelta(minutes=1), 1.0, uptime=5060),
        _row(start + timedelta(minutes=2), 1.0, uptime=30),
    ]
    assert reboot_markers(rows) == [start + timedelta(minutes=2)]
