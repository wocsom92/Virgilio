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
    metrics._telegram_snapshot_cache = None
    metrics._wifi_snapshot_cache = None
    metrics._vpn_snapshot_cache = None
    monkeypatch.setattr(metrics.settings, "version", "9.9.9", raising=False)
    monkeypatch.setattr(metrics.settings, "expose_docker_running_containers", True, raising=False)
    monkeypatch.setattr(metrics, "_resolve_mount_points", lambda: ["/"])
    monkeypatch.setattr(metrics, "_get_disk_usage", lambda mount: SimpleNamespace(total=1024**3, free=512**3, percent=50.0))
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
    monkeypatch.setattr(metrics, "_collect_top_processes", lambda limit=10: {"cpu": [{"name": "api"}], "memory": []})
    monkeypatch.setattr(metrics, "_collect_telegram_bot_snapshot", lambda running: {"telegram_bot_status_level": 0})
    monkeypatch.setattr(metrics, "_collect_wifi_snapshot", lambda: {"wifi_status_level": 0, "wifi_status": "ok"})
    monkeypatch.setattr(metrics, "_collect_vpn_snapshot", lambda: {"vpn_status_level": 0, "vpn_status": "ok"})

    payload = metrics.collect_metrics()

    assert payload["monitor_version"] == "9.9.9"
    assert payload["memory_available_gb"] == 1.5
    assert payload["swap_used_percent"] == 12.5
    assert payload["docker_container_count"] == 7
    assert payload["docker_running_containers"] == ["api", "db"]
    assert payload["telegram_bot_status_level"] == 0
    assert payload["wifi_status"] == "ok"
    assert payload["vpn_status"] == "ok"
    assert payload["mounted_usage"] == [{"mount_point": "/", "total_gb": 1.0, "used_percent": 50.0}]
    assert payload["top_processes"] == {"cpu": [{"name": "api"}], "memory": []}


def test_collect_metrics_hides_running_container_names_when_disabled(monkeypatch):
    metrics._telegram_snapshot_cache = None
    metrics._wifi_snapshot_cache = None
    metrics._vpn_snapshot_cache = None
    monkeypatch.setattr(metrics.settings, "expose_docker_running_containers", False, raising=False)
    monkeypatch.setattr(metrics, "_resolve_mount_points", lambda: ["/"])
    monkeypatch.setattr(metrics, "_get_disk_usage", lambda mount: SimpleNamespace(total=1024**3, free=512**3, percent=50.0))
    monkeypatch.setattr(metrics, "_count_docker_containers", lambda: 7)
    monkeypatch.setattr(metrics, "_list_running_docker_containers", lambda: ["api", "db"])
    monkeypatch.setattr(
        metrics.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(percent=25.0, total=2 * 1024**3, available=1.5 * 1024**3),
    )
    monkeypatch.setattr(metrics.psutil, "swap_memory", lambda: SimpleNamespace(percent=12.5))
    monkeypatch.setattr(metrics.psutil, "boot_time", lambda: 0)
    monkeypatch.setattr(metrics, "_collect_top_processes", lambda limit=10: None)
    monkeypatch.setattr(metrics, "_collect_telegram_bot_snapshot", lambda running: {})
    monkeypatch.setattr(metrics, "_collect_wifi_snapshot", lambda: {})
    monkeypatch.setattr(metrics, "_collect_vpn_snapshot", lambda: {})

    payload = metrics.collect_metrics()

    assert payload["docker_container_count"] == 7
    assert payload["docker_running_containers"] is None


def test_collect_telegram_bot_snapshot_uses_bot_and_proxy_containers():
    metrics._telegram_snapshot_cache = None

    snapshot = metrics._collect_telegram_bot_snapshot(
        ["server-monitor-backend", "server-monitor-telegram-bot", "server-monitor-telegram-egress-proxy"]
    )

    assert snapshot == {
        "telegram_bot_status_level": 0,
        "telegram_bot_status": "ok",
        "telegram_bot_running": True,
        "telegram_egress_proxy_running": True,
    }


def test_collect_wifi_snapshot_reads_hostapd_and_leases(monkeypatch):
    metrics._wifi_snapshot_cache = None
    monkeypatch.setattr(metrics, "_detect_hotspot_interface", lambda: "wlan0")
    monkeypatch.setattr(metrics, "_detect_hotspot_ssid", lambda: "lab-wifi")
    monkeypatch.setattr(metrics, "_read_host_text", lambda path: "up\n" if path.endswith("/operstate") else None)
    monkeypatch.setattr(metrics, "_host_file_exists", lambda path: path == "/run/hostapd/wlan0")
    monkeypatch.setattr(metrics, "_count_wifi_clients", lambda interface: 2 if interface == "wlan0" else None)
    monkeypatch.setattr(metrics, "_count_host_file_lines", lambda path: 3 if path == "/var/lib/misc/dnsmasq.leases" else 0)

    snapshot = metrics._collect_wifi_snapshot()

    assert snapshot == {
        "wifi_status_level": 0,
        "wifi_status": "ok",
        "wifi_ssid": "lab-wifi",
        "wifi_interface": "wlan0",
        "wifi_client_count": 2,
        "wifi_dhcp_lease_count": 3,
        "wifi_operstate": "up",
        "wifi_hostapd_socket_present": True,
    }


def test_collect_vpn_snapshot_detects_matching_hotspot_and_backend_egress(monkeypatch):
    metrics._vpn_snapshot_cache = None
    metrics._public_ip_probe_cache.clear()
    metrics._country_code_probe_cache.clear()
    monkeypatch.setattr(metrics, "_read_active_vpn_backend", lambda: "wg0")
    monkeypatch.setattr(metrics, "_detect_hotspot_interface", lambda: "wlan0")
    monkeypatch.setattr(metrics, "_host_interface_ipv4", lambda interface: "192.0.2.10" if interface == "wlan0" else None)
    monkeypatch.setattr(metrics, "_probe_default_public_ip", lambda: ("203.0.113.42", "ipify"))
    monkeypatch.setattr(metrics, "_probe_public_ip_via_interface", lambda interface: ("198.51.100.27", "icanhazip"))
    monkeypatch.setattr(metrics, "_probe_public_ip_via_source_ip", lambda source_ip: ("198.51.100.27", "ifconfig.me"))
    monkeypatch.setattr(
        metrics,
        "_probe_country_code",
        lambda *, ip_address_value: (
            "DE" if ip_address_value == "198.51.100.27" else "US" if ip_address_value == "203.0.113.42" else None,
            "ipapi",
        ),
    )

    snapshot = metrics._collect_vpn_snapshot()

    assert snapshot == {
        "vpn_status_level": 0,
        "vpn_status": "ok",
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


def test_probe_public_ip_falls_back_across_three_services(monkeypatch):
    metrics._public_ip_probe_cache.clear()
    calls: list[list[str]] = []

    def fake_run_host_network_command(command, args, *, timeout=8, include_chroot=True):
        assert command == "curl"
        calls.append(args)
        url = args[-1]
        if url == "https://api.ipify.org":
            return None
        if url == "https://ipv4.icanhazip.com":
            return "not-an-ip"
        if url == "https://ifconfig.me/ip":
            return "198.51.100.27\n"
        return None

    monkeypatch.setattr(metrics, "_run_host_network_command", fake_run_host_network_command)

    ip_value, service_name = metrics._probe_public_ip(cache_key=("default", ""), curl_args=[])

    assert (ip_value, service_name) == ("198.51.100.27", "ifconfig.me")
    assert [call[-1] for call in calls] == [
        "https://api.ipify.org",
        "https://ipv4.icanhazip.com",
        "https://ifconfig.me/ip",
    ]


def test_probe_country_code_falls_back_across_three_services(monkeypatch):
    metrics._country_code_probe_cache.clear()
    calls: list[list[str]] = []

    def fake_run_host_network_command(command, args, *, timeout=8, include_chroot=True):
        assert command == "curl"
        calls.append(args)
        url = args[-1]
        if url == "https://ipapi.co/198.51.100.27/country/":
            return None
        if url == "https://ipinfo.io/198.51.100.27/country":
            return "bad-country"
        if url == "https://ipwho.is/198.51.100.27":
            return '{"success":true,"country_code":"DE"}'
        return None

    monkeypatch.setattr(metrics, "_run_host_network_command", fake_run_host_network_command)

    country_code, service_name = metrics._probe_country_code(ip_address_value="198.51.100.27")

    assert (country_code, service_name) == ("DE", "ipwhois")
    assert [call[-1] for call in calls] == [
        "https://ipapi.co/198.51.100.27/country/",
        "https://ipinfo.io/198.51.100.27/country",
        "https://ipwho.is/198.51.100.27",
    ]


def test_collect_top_processes_sorts_cpu_and_memory(monkeypatch):
    monkeypatch.setattr(metrics.time, "sleep", lambda _seconds: None)

    class FakeProcess:
        def __init__(self, pid, name, cpu_percent, memory_percent):
            self.info = {"pid": pid, "name": name, "memory_percent": memory_percent}
            self._cpu_percent = cpu_percent

        def cpu_percent(self, interval=None):
            return self._cpu_percent

    monkeypatch.setattr(
        metrics.psutil,
        "process_iter",
        lambda _attrs: iter(
            [
                FakeProcess(1, "worker-a", 12.0, 3.5),
                FakeProcess(2, "worker-b", 55.0, 1.0),
                FakeProcess(3, "worker-c", 9.0, 7.5),
            ]
        ),
    )

    result = metrics._collect_top_processes(limit=2)

    assert result == {
        "cpu": [
            {"pid": 2, "name": "worker-b", "cpu_percent": 55.0, "memory_percent": 1.0},
            {"pid": 1, "name": "worker-a", "cpu_percent": 12.0, "memory_percent": 3.5},
        ],
        "memory": [
            {"pid": 3, "name": "worker-c", "cpu_percent": 9.0, "memory_percent": 7.5},
            {"pid": 1, "name": "worker-a", "cpu_percent": 12.0, "memory_percent": 3.5},
        ],
    }


def test_count_docker_containers_falls_back_to_running_list(monkeypatch):
    monkeypatch.setattr(metrics.settings, "host_root_target", "/hostfs", raising=False)
    monkeypatch.setattr(metrics.os, "listdir", lambda _path: (_ for _ in ()).throw(FileNotFoundError()))
    monkeypatch.setattr(metrics, "_list_running_docker_containers", lambda: ["api", "db"])

    assert metrics._count_docker_containers() == 2


def test_collect_ssh_snapshot_levels(monkeypatch):
    metrics._ssh_snapshot_cache = None
    monkeypatch.setattr(metrics, "_extract_ssh_login_ages", lambda: (3600, 7200, None, None))
    monkeypatch.setattr(
        metrics,
        "_read_sshd_effective_settings",
        lambda: (
            True,
            True,
            True,
            "no",
            "PubkeyAuthentication yes",
            "PasswordAuthentication no",
            "KbdInteractiveAuthentication no",
            "PermitRootLogin no",
        ),
    )
    snapshot = metrics._collect_ssh_snapshot()
    assert snapshot["ssh_status_level"] == 0
    assert snapshot["ssh_status"] == "ok"
    assert snapshot["ssh_pubkey_auth_line"] == "PubkeyAuthentication yes"

    metrics._ssh_snapshot_cache = None
    monkeypatch.setattr(
        metrics,
        "_read_sshd_effective_settings",
        lambda: (
            False,
            True,
            True,
            "no",
            "PubkeyAuthentication no",
            "PasswordAuthentication no",
            "KbdInteractiveAuthentication no",
            "PermitRootLogin no",
        ),
    )
    snapshot = metrics._collect_ssh_snapshot()
    assert snapshot["ssh_status_level"] == 1
    assert snapshot["ssh_status"] == "warn"

    metrics._ssh_snapshot_cache = None
    monkeypatch.setattr(
        metrics,
        "_read_sshd_effective_settings",
        lambda: (
            False,
            True,
            True,
            "yes",
            "PubkeyAuthentication no",
            "PasswordAuthentication no",
            "KbdInteractiveAuthentication no",
            "PermitRootLogin yes",
        ),
    )
    snapshot = metrics._collect_ssh_snapshot()
    assert snapshot["ssh_status_level"] == 2
    assert snapshot["ssh_status"] == "critical"

    metrics._ssh_snapshot_cache = None
    monkeypatch.setattr(
        metrics,
        "_read_sshd_effective_settings",
        lambda: (
            True,
            False,
            True,
            "prohibit-password",
            "PubkeyAuthentication yes",
            "PasswordAuthentication yes",
            "KbdInteractiveAuthentication no",
            "PermitRootLogin prohibit-password",
        ),
    )
    snapshot = metrics._collect_ssh_snapshot()
    assert snapshot["ssh_status_level"] == 1
    assert snapshot["ssh_status"] == "warn"

    metrics._ssh_snapshot_cache = None
    monkeypatch.setattr(
        metrics,
        "_read_sshd_effective_settings",
        lambda: (
            True,
            True,
            False,
            "prohibit-password",
            "PubkeyAuthentication yes",
            "PasswordAuthentication no",
            "KbdInteractiveAuthentication yes",
            "PermitRootLogin prohibit-password",
        ),
    )
    snapshot = metrics._collect_ssh_snapshot()
    assert snapshot["ssh_status_level"] == 1
    assert snapshot["ssh_status"] == "warn"

    metrics._ssh_snapshot_cache = None
    monkeypatch.setattr(
        metrics,
        "_read_sshd_effective_settings",
        lambda: (
            True,
            True,
            True,
            "without-password",
            "PubkeyAuthentication yes",
            "PasswordAuthentication no",
            "KbdInteractiveAuthentication no",
            "PermitRootLogin without-password",
        ),
    )
    snapshot = metrics._collect_ssh_snapshot()
    assert snapshot["ssh_status_level"] == 1
    assert snapshot["ssh_status"] == "warn"

    monkeypatch.setattr(
        metrics,
        "_read_sshd_effective_settings",
        lambda: (
            True,
            True,
            True,
            "prohibit-password",
            "PubkeyAuthentication yes",
            "PasswordAuthentication no",
            "KbdInteractiveAuthentication no",
            "PermitRootLogin prohibit-password",
        ),
    )
    snapshot = metrics._collect_ssh_snapshot()
    assert snapshot["ssh_status_level"] == 1
    assert snapshot["ssh_status"] == "warn"


def test_read_sshd_effective_settings_parses_include(tmp_path, monkeypatch):
    metrics._ssh_settings_cache = None
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

    (
        pubkey_enabled,
        password_auth_disabled,
        kbd_interactive_disabled,
        permit_root_token,
        pubkey_line,
        password_auth_line,
        kbd_interactive_line,
        permit_root_line,
    ) = metrics._read_sshd_effective_settings()

    assert pubkey_enabled is True
    assert password_auth_disabled is False
    assert kbd_interactive_disabled is True
    assert permit_root_token == "yes"
    assert pubkey_line == "PubkeyAuthentication yes"
    assert password_auth_line == "PasswordAuthentication yes"
    assert kbd_interactive_line == "KbdInteractiveAuthentication no"
    assert permit_root_line == "PermitRootLogin yes"


def test_extract_ssh_login_ages_uses_last_fallback(monkeypatch):
    monkeypatch.setattr(metrics, "_candidate_ssh_log_files", lambda: [])
    monkeypatch.setattr(metrics, "_extract_ssh_login_ages_from_journal", lambda: (None, None, None, None))
    monkeypatch.setattr(
        metrics,
        "_extract_login_age_from_last_command",
        lambda command, _path: 123 if command == "last" else 456,
    )

    success_age, failure_age, success_details, failure_details = metrics._extract_ssh_login_ages()

    assert success_age == 123
    assert failure_age == 456
    assert success_details is None
    assert failure_details is None


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
            {"method": "publickey"},
            {"method": "password"},
        ),
    )
    monkeypatch.setattr(metrics, "_extract_login_age_from_last_command", lambda _command, _path: None)

    success_age, failure_age, success_details, failure_details = metrics._extract_ssh_login_ages()

    assert success_age == 30
    assert failure_age == 300
    assert success_details == {"method": "publickey"}
    assert failure_details == {"method": "password"}


def test_extract_ssh_success_details_parses_auth_method_and_source():
    line = "Mar 16 11:22:33 host sshd[123]: Accepted publickey for root from 192.0.2.8 port 55123 ssh2: RSA SHA256:abc"

    details = metrics._extract_ssh_success_details(line)

    assert details == {
        "method": "publickey",
        "username": "root",
        "source_ip": "192.0.2.8",
        "port": 55123,
        "raw_line": "Mar 16 11:22:33 host sshd[123]: Accepted publickey for root from 192.0.2.8 port 55123 ssh2: RSA SHA256:abc",
    }


def test_extract_ssh_failure_details_parses_auth_method_and_source():
    line = "Mar 16 11:22:33 host sshd[123]: Failed publickey for root from 192.0.2.8 port 55123 ssh2"

    details = metrics._extract_ssh_failure_details(line)

    assert details == {
        "method": "publickey",
        "username": "root",
        "source_ip": "192.0.2.8",
        "port": 55123,
        "raw_line": "Mar 16 11:22:33 host sshd[123]: Failed publickey for root from 192.0.2.8 port 55123 ssh2",
    }


def test_extract_ssh_failure_details_parses_preauth_disconnect():
    line = "Mar 16 11:22:33 host sshd[123]: Connection closed by authenticating user root 192.0.2.8 port 55123 [preauth]"

    details = metrics._extract_ssh_failure_details(line)

    assert details == {
        "method": "preauth-disconnect",
        "username": "root",
        "source_ip": "192.0.2.8",
        "port": 55123,
        "raw_line": "Mar 16 11:22:33 host sshd[123]: Connection closed by authenticating user root 192.0.2.8 port 55123 [preauth]",
    }
