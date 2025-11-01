from types import SimpleNamespace

import pytest

from monitor.app import metrics


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", "/"),
        ("   ", "/"),
        ("/", "/"),
        ("/var/log/", "/var/log"),
        ("/mnt/data//", "/mnt/data"),
    ],
)
def test_normalize_mount_path(value, expected):
    assert metrics._normalize_mount_path(value) == expected


def test_resolve_mount_points_combines_configured_and_discovered(monkeypatch):
    monkeypatch.setattr(metrics.settings, "mounted_points", ["auto", "/data/", " /srv "], raising=False)
    monkeypatch.setattr(metrics, "_discover_mount_points", lambda: ["/", "/data", "/mnt/storage"])

    result = metrics._resolve_mount_points()

    assert result == ["/data", "/srv", "/", "/mnt/storage"]


def test_candidate_paths_respect_host_root_target(monkeypatch):
    monkeypatch.setattr(metrics.settings, "host_root_target", "/hostfs", raising=False)

    assert metrics._candidate_paths_for_mount("/") == ["/hostfs", "/"]

    candidates = metrics._candidate_paths_for_mount("/var/log/")
    assert candidates == ["/hostfs/var/log", "/var/log"]


def test_detect_warnings_flags_all_categories():
    payload = {
        "cpu_temperature_c": 85.2,
        "ram_used_percent": 91.0,
        "disk_usage_percent": 95.0,
        "mounted_usage": [
            {"mount_point": "/data", "used_percent": 90.0},
            {"mount_point": "/srv", "used_percent": 40.0},
            {"mount_point": "/logs", "used_percent": 97.0},
        ],
    }

    warnings = metrics._detect_warnings(payload)

    assert warnings == [
        "High CPU temperature 85.2°C",
        "High RAM usage 91.0%",
        "Disk usage critical at 95.0%",
        "/data usage critical at 90.0%",
        "/logs usage critical at 97.0%",
    ]


def test_mounted_usage_skips_unreadable_mounts(monkeypatch):
    stats_map = {
        "/": SimpleNamespace(total=2 * 1024**3, percent=12.345),
        "/tmp": SimpleNamespace(total=5 * 1024**3, percent=90.4),
    }

    monkeypatch.setattr(metrics, "_get_disk_usage", lambda mount: stats_map.get(mount))

    usage = metrics._mounted_usage(["/", "/data", "/tmp"])

    assert usage == [
        {"mount_point": "/", "total_gb": 2.0, "used_percent": 12.35},
        {"mount_point": "/tmp", "total_gb": 5.0, "used_percent": 90.4},
    ]


def test_collect_metrics_includes_backend_version(monkeypatch):
    monkeypatch.setattr(metrics.settings, "version", "9.9.9", raising=False)
    monkeypatch.setattr(metrics, "_resolve_mount_points", lambda: ["/"])
    monkeypatch.setattr(metrics, "_get_disk_usage", lambda mount: SimpleNamespace(total=1024**3, percent=50.0))
    monkeypatch.setattr(metrics, "_get_cpu_temperature", lambda: None)
    monkeypatch.setattr(metrics.os, "getloadavg", lambda: (0.1, 0.2, 0.3))
    monkeypatch.setattr(metrics.psutil, "virtual_memory", lambda: SimpleNamespace(percent=25.0, total=2 * 1024**3))
    monkeypatch.setattr(metrics.psutil, "boot_time", lambda: 0)

    payload = metrics.collect_metrics()

    assert payload["backend_version"] == "9.9.9"
    assert payload["mounted_usage"] == [{"mount_point": "/", "total_gb": 1.0, "used_percent": 50.0}]
