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


def test_collect_metrics_includes_monitor_version(monkeypatch):
    monkeypatch.setattr(metrics.settings, "version", "9.9.9", raising=False)
    monkeypatch.setattr(metrics.settings, "expose_docker_running_containers", True, raising=False)
    monkeypatch.setattr(metrics, "_resolve_mount_points", lambda: ["/"])
    monkeypatch.setattr(metrics, "_get_disk_usage", lambda mount: SimpleNamespace(total=1024**3, percent=50.0))
    monkeypatch.setattr(metrics, "_count_docker_containers", lambda: 7)
    monkeypatch.setattr(metrics, "_list_running_docker_containers", lambda: ["api", "db"])
    monkeypatch.setattr(metrics, "_get_cpu_temperature", lambda: None)
    monkeypatch.setattr(metrics.os, "getloadavg", lambda: (0.1, 0.2, 0.3))
    monkeypatch.setattr(
        metrics.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(percent=25.0, total=2 * 1024**3, available=1.5 * 1024**3),
    )
    monkeypatch.setattr(metrics.psutil, "swap_memory", lambda: SimpleNamespace(percent=12.5))
    monkeypatch.setattr(metrics.psutil, "boot_time", lambda: 0)

    payload = metrics.collect_metrics()

    assert payload["monitor_version"] == "9.9.9"
    assert payload["memory_available_gb"] == 1.5
    assert payload["swap_used_percent"] == 12.5
    assert payload["docker_container_count"] == 7
    assert payload["docker_running_containers"] == ["api", "db"]
    assert payload["mounted_usage"] == [{"mount_point": "/", "total_gb": 1.0, "used_percent": 50.0}]


def test_collect_metrics_hides_running_container_names_when_disabled(monkeypatch):
    monkeypatch.setattr(metrics.settings, "expose_docker_running_containers", False, raising=False)
    monkeypatch.setattr(metrics, "_resolve_mount_points", lambda: ["/"])
    monkeypatch.setattr(metrics, "_get_disk_usage", lambda mount: SimpleNamespace(total=1024**3, percent=50.0))
    monkeypatch.setattr(metrics, "_count_docker_containers", lambda: 7)
    monkeypatch.setattr(metrics, "_list_running_docker_containers", lambda: ["api", "db"])
    monkeypatch.setattr(
        metrics.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(percent=25.0, total=2 * 1024**3, available=1.5 * 1024**3),
    )
    monkeypatch.setattr(metrics.psutil, "swap_memory", lambda: SimpleNamespace(percent=12.5))
    monkeypatch.setattr(metrics.psutil, "boot_time", lambda: 0)

    payload = metrics.collect_metrics()

    assert payload["docker_container_count"] == 7
    assert payload["docker_running_containers"] is None


def test_count_docker_containers_falls_back_to_running_list(monkeypatch):
    monkeypatch.setattr(metrics.settings, "host_root_target", "/hostfs", raising=False)
    monkeypatch.setattr(metrics.os, "listdir", lambda _path: (_ for _ in ()).throw(FileNotFoundError()))
    monkeypatch.setattr(metrics, "_list_running_docker_containers", lambda: ["api", "db"])

    assert metrics._count_docker_containers() == 2


def test_collect_ssh_snapshot_levels(monkeypatch):
    monkeypatch.setattr(metrics, "_extract_ssh_login_ages", lambda: (3600, 7200))
    monkeypatch.setattr(metrics, "_read_sshd_effective_settings", lambda: (True, True, True, "prohibit-password"))
    snapshot = metrics._collect_ssh_snapshot()
    assert snapshot["ssh_status_level"] == 0
    assert snapshot["ssh_status"] == "ok"

    monkeypatch.setattr(metrics, "_read_sshd_effective_settings", lambda: (False, True, True, "prohibit-password"))
    snapshot = metrics._collect_ssh_snapshot()
    assert snapshot["ssh_status_level"] == 1
    assert snapshot["ssh_status"] == "warn"

    monkeypatch.setattr(metrics, "_read_sshd_effective_settings", lambda: (False, True, True, "yes"))
    snapshot = metrics._collect_ssh_snapshot()
    assert snapshot["ssh_status_level"] == 2
    assert snapshot["ssh_status"] == "critical"

    monkeypatch.setattr(metrics, "_read_sshd_effective_settings", lambda: (True, False, True, "prohibit-password"))
    snapshot = metrics._collect_ssh_snapshot()
    assert snapshot["ssh_status_level"] == 1
    assert snapshot["ssh_status"] == "warn"

    monkeypatch.setattr(metrics, "_read_sshd_effective_settings", lambda: (True, True, False, "prohibit-password"))
    snapshot = metrics._collect_ssh_snapshot()
    assert snapshot["ssh_status_level"] == 1
    assert snapshot["ssh_status"] == "warn"

    monkeypatch.setattr(metrics, "_read_sshd_effective_settings", lambda: (True, True, True, "without-password"))
    snapshot = metrics._collect_ssh_snapshot()
    assert snapshot["ssh_status_level"] == 1
    assert snapshot["ssh_status"] == "warn"


def test_read_sshd_effective_settings_parses_include(tmp_path, monkeypatch):
    host_root = tmp_path / "hostfs"
    ssh_dir = host_root / "etc" / "ssh" / "sshd_config.d"
    ssh_dir.mkdir(parents=True)
    (host_root / "etc" / "ssh" / "sshd_config").write_text(
        "PubkeyAuthentication no\nPasswordAuthentication yes\nInclude /etc/ssh/sshd_config.d/*.conf\n",
        encoding="utf-8",
    )
    (ssh_dir / "hardening.conf").write_text(
        "PubkeyAuthentication yes\nPermitRootLogin yes\nKbdInteractiveAuthentication no\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(metrics.settings, "host_root_target", str(host_root), raising=False)

    pubkey_enabled, password_auth_disabled, kbd_interactive_disabled, permit_root_token = metrics._read_sshd_effective_settings()

    assert pubkey_enabled is True
    assert password_auth_disabled is False
    assert kbd_interactive_disabled is True
    assert permit_root_token == "yes"


def test_extract_ssh_login_ages_uses_last_fallback(monkeypatch):
    monkeypatch.setattr(metrics, "_candidate_ssh_log_files", lambda: [])
    monkeypatch.setattr(metrics, "_extract_ssh_login_ages_from_journal", lambda: (None, None))
    monkeypatch.setattr(
        metrics,
        "_extract_login_age_from_last_command",
        lambda command, _path: 123 if command == "last" else 456,
    )

    success_age, failure_age = metrics._extract_ssh_login_ages()

    assert success_age == 123
    assert failure_age == 456


def test_extract_ssh_login_ages_uses_journal_fallback(monkeypatch):
    fixed_now = metrics.datetime(2026, 3, 7, 14, 40, 0)

    class FixedDateTime(metrics.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is None else fixed_now.astimezone(tz)

    monkeypatch.setattr(metrics, "datetime", FixedDateTime)
    monkeypatch.setattr(metrics, "_candidate_ssh_log_files", lambda: [])
    monkeypatch.setattr(
        metrics,
        "_extract_ssh_login_ages_from_journal",
        lambda: (
            FixedDateTime(2026, 3, 7, 14, 39, 30),
            FixedDateTime(2026, 3, 7, 14, 35, 0),
        ),
    )
    monkeypatch.setattr(metrics, "_extract_login_age_from_last_command", lambda _command, _path: None)

    success_age, failure_age = metrics._extract_ssh_login_ages()

    assert success_age == 30
    assert failure_age == 300
