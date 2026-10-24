from __future__ import annotations

import asyncio
import glob
import gzip
import ipaddress
import json
import logging
import os
import platform
import re
import socket
import time
from datetime import datetime, timedelta, timezone
from typing import Any
import subprocess
import shlex
import shutil
from pathlib import Path

import psutil

from monitor.app.config import settings


CPU_TEMP_WARN = 80.0
RAM_WARN_PERCENT = 90.0
DISK_WARN_PERCENT = 90.0

logger = logging.getLogger(__name__)

_TOP_PROCESSES_CACHE_TTL_SECONDS = 5.0
_SSH_SNAPSHOT_CACHE_TTL_SECONDS = 60.0  # log tailing + subprocesses; once a minute is plenty
_SSH_SETTINGS_CACHE_TTL_SECONDS = 30.0
_MOUNT_POINTS_CACHE_TTL_SECONDS = 30.0
_OS_VERSION_CACHE_TTL_SECONDS = 300.0
_SERVICE_SNAPSHOT_CACHE_TTL_SECONDS = 15.0
_NETWORK_EGRESS_CACHE_TTL_SECONDS = 60.0
_top_processes_cache: tuple[float, dict[str, list[dict[str, Any]]] | None] | None = None
_top_processes_sampled_pids: set[int] = set()
_ssh_snapshot_cache: tuple[float, dict[str, Any]] | None = None
_ssh_settings_cache: tuple[float, tuple[bool, bool, bool, str, str, str, str, str]] | None = None
_mount_points_cache: tuple[float, list[str]] | None = None
_os_version_cache: tuple[float, str] | None = None
_telegram_snapshot_cache: tuple[float, dict[str, Any]] | None = None
_wifi_snapshot_cache: tuple[float, dict[str, Any]] | None = None
_vpn_snapshot_cache: tuple[float, dict[str, Any]] | None = None
_public_ip_probe_cache: dict[tuple[str, str], tuple[float, tuple[str | None, str | None]]] = {}
_country_code_probe_cache: dict[tuple[str, str], tuple[float, tuple[str | None, str | None]]] = {}

_SSH_LOG_TIMESTAMP_RE = re.compile(r"^(?P<stamp>[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+")
_LAST_OUTPUT_TIMESTAMP_RE = re.compile(r"([A-Z][a-z]{2}\s+[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4})")
_JOURNAL_SHORT_UNIX_RE = re.compile(r"^(?P<stamp>\d+(?:\.\d+)?)\s+")
_SSH_SUCCESS_MARKERS = (
    "Accepted password",
    "Accepted publickey",
    "Accepted keyboard-interactive",
)
_SSH_FAILURE_MARKERS = (
    "Failed password",
    "Failed publickey",
    "Invalid user",
    "authentication failure",
    "maximum authentication attempts exceeded",
    "Connection closed by authenticating user",
    "Connection reset by authenticating user",
    "Disconnected from authenticating user",
)
_SSH_ROOT_PASSWORD_DISABLED_VALUES = {
    "no",
    "prohibit-password",
    "without-password",
    "forced-commands-only",
}
_SSH_FALSE_VALUES = {"0", "false", "no", "off"}
_SSH_TRUE_VALUES = {"1", "true", "yes", "on"}
_SSH_FAILURE_DETAILS_RE = re.compile(
    r"Failed\s+(?P<method>[A-Za-z0-9-]+)\s+for\s+(?:(?:invalid user)\s+)?(?P<username>\S+)\s+from\s+"
    r"(?P<source_ip>\S+)(?:\s+port\s+(?P<port>\d+))?",
    re.IGNORECASE,
)
_SSH_INVALID_USER_RE = re.compile(
    r"Invalid user\s+(?P<username>\S+)\s+from\s+(?P<source_ip>\S+)(?:\s+port\s+(?P<port>\d+))?",
    re.IGNORECASE,
)
_SSH_AUTH_FAILURE_RE = re.compile(
    r"authentication failure.*?(?:user=(?P<username>\S+))?.*?(?:rhost=(?P<source_ip>\S+))?",
    re.IGNORECASE,
)
_SSH_PREAUTH_DISCONNECT_RE = re.compile(
    r"(?:Connection closed|Connection reset|Disconnected)\s+(?:from|by)\s+authenticating user\s+"
    r"(?P<username>\S+)\s+(?P<source_ip>\S+)(?:\s+port\s+(?P<port>\d+))?",
    re.IGNORECASE,
)
_SSH_SUCCESS_DETAILS_RE = re.compile(
    r"Accepted\s+(?P<method>[A-Za-z0-9-]+)\s+for\s+(?P<username>\S+)\s+from\s+"
    r"(?P<source_ip>\S+)(?:\s+port\s+(?P<port>\d+))?",
    re.IGNORECASE,
)
_IP_ADDR_SHOW_RE = re.compile(r"\binet\s+(?P<address>\d+\.\d+\.\d+\.\d+)/")
_PUBLIC_IP_PROBE_SERVICES: tuple[tuple[str, str], ...] = (
    ("ipify", "https://api.ipify.org"),
    ("icanhazip", "https://ipv4.icanhazip.com"),
    ("ifconfig.me", "https://ifconfig.me/ip"),
)
_COUNTRY_CODE_PROBE_SERVICES: tuple[tuple[str, str], ...] = (
    ("ipapi", "https://ipapi.co/{ip}/country/"),
    ("ipinfo", "https://ipinfo.io/{ip}/country"),
    ("ipwhois", "https://ipwho.is/{ip}"),
)
_XRAY_PROXY_URL = "http://127.0.0.1:10809"


def _safe_percent(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def _host_procfs_path() -> str | None:
    """The host's /proc through the host-root bind mount, when it is one."""
    host_target = _normalize_mount_path(settings.host_root_target) if settings.host_root_target else ""
    if not host_target or host_target == "/":
        return None
    return os.path.join(host_target, "proc")


def _configure_procfs() -> str:
    """Point psutil at the host's process table when the agent runs in its own PID namespace.

    Inside the container ``/proc`` only lists the agent itself, so the top-process lists would
    be useless. The read-only host root bind mount carries the host's ``/proc`` (bind mounts are
    recursive); when it exposes more processes than our own namespace, read from there.
    """
    current = getattr(psutil, "PROCFS_PATH", None)  # Linux only
    candidate = _host_procfs_path()
    if current is None or not candidate:
        return current or "/proc"
    try:
        host_pids = [entry for entry in os.listdir(candidate) if entry.isdigit()]
        local_pids = [entry for entry in os.listdir(current) if entry.isdigit()]
        with open(os.path.join(candidate, "1", "stat"), "r", encoding="utf-8") as handle:
            handle.read(64)
    except OSError:
        return current
    if len(host_pids) > len(local_pids):
        psutil.PROCFS_PATH = candidate
        logger.info("Reading the host process table from %s", candidate)
    return psutil.PROCFS_PATH


_CGROUP_CONTAINER_ID_RE = re.compile(r"([a-f0-9]{64})")


def _container_id_for_pid(pid: int) -> str | None:
    """Docker container id from the process cgroup (``docker-<id>.scope`` or ``/docker/<id>``)."""
    try:
        procfs = getattr(psutil, "PROCFS_PATH", "/proc")
        with open(os.path.join(procfs, str(pid), "cgroup"), "r", encoding="utf-8") as handle:
            for line in handle:
                match = _CGROUP_CONTAINER_ID_RE.search(line)
                if match:
                    return match.group(1)
    except (OSError, UnicodeDecodeError):
        return None
    return None


def _container_name_for_pid(pid: int, names_by_id: dict[str, str] | None) -> str | None:
    container_id = _container_id_for_pid(pid)
    if not container_id:
        return None
    if names_by_id and container_id in names_by_id:
        return names_by_id[container_id]
    return container_id[:12]


def _aggregate_containers(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_container: dict[str, dict[str, Any]] = {}
    for entry in entries:
        container = entry.get("container")
        if not container:
            continue
        aggregate = by_container.setdefault(
            container, {"name": container, "cpu_percent": 0.0, "memory_percent": 0.0, "process_count": 0}
        )
        aggregate["cpu_percent"] += entry.get("cpu_percent") or 0.0
        aggregate["memory_percent"] += entry.get("memory_percent") or 0.0
        aggregate["process_count"] += 1
    for aggregate in by_container.values():
        aggregate["cpu_percent"] = round(aggregate["cpu_percent"], 1)
        aggregate["memory_percent"] = round(aggregate["memory_percent"], 1)
    return list(by_container.values())


def _collect_top_processes(limit: int = 10) -> dict[str, list[dict[str, Any]]] | None:
    """Top processes (and Docker containers) by CPU and memory.

    CPU percentages are measured over the time since the previous collection (psutil keeps the
    last CPU times on the cached ``Process`` objects), so with a one-minute poll they reflect the
    whole minute instead of a 50 ms sample; processes seen for the first time get a short sample.
    """
    global _top_processes_cache, _top_processes_sampled_pids
    now = time.monotonic()
    if _top_processes_cache is not None:
        cached_at, cached_value = _top_processes_cache
        if now - cached_at <= _TOP_PROCESSES_CACHE_TTL_SECONDS:
            return cached_value

    try:
        processes = list(psutil.process_iter(["pid", "name", "memory_percent"]))
    except (psutil.Error, OSError):
        return None

    if not processes:
        return None

    def _pid_of(process: Any) -> int | None:
        pid = getattr(process, "pid", None)
        if pid is None:
            pid = (getattr(process, "info", None) or {}).get("pid")
        return pid if isinstance(pid, int) else None

    fresh = [process for process in processes if _pid_of(process) not in _top_processes_sampled_pids]
    for process in fresh:
        try:
            process.cpu_percent(interval=None)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    if fresh:
        time.sleep(0.25)

    scanned = _scan_docker_containers()
    names_by_id = scanned[2] if scanned is not None else None

    entries: list[dict[str, Any]] = []
    sampled: set[int] = set()
    for process in processes:
        try:
            info = process.info
            pid = info.get("pid")
            name = (info.get("name") or "").strip() or f"pid-{pid}"
            entry: dict[str, Any] = {
                "pid": pid,
                "name": name[:80],
                "cpu_percent": _safe_percent(process.cpu_percent(interval=None)),
                "memory_percent": _safe_percent(info.get("memory_percent")),
            }
            container = _container_name_for_pid(pid, names_by_id) if isinstance(pid, int) else None
            if container:
                entry["container"] = container
            entries.append(entry)
            if isinstance(pid, int):
                sampled.add(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    _top_processes_sampled_pids = sampled

    if not entries:
        return None

    cpu_entries = sorted(
        entries,
        key=lambda entry: (entry.get("cpu_percent") or 0.0, entry.get("memory_percent") or 0.0, entry.get("pid") or 0),
        reverse=True,
    )[:limit]
    memory_entries = sorted(
        entries,
        key=lambda entry: (
            entry.get("memory_percent") or 0.0,
            entry.get("cpu_percent") or 0.0,
            entry.get("pid") or 0,
        ),
        reverse=True,
    )[:limit]

    result: dict[str, list[dict[str, Any]]] = {
        "cpu": cpu_entries,
        "memory": memory_entries,
    }
    containers = _aggregate_containers(entries)
    if containers:
        result["containers_cpu"] = sorted(
            containers, key=lambda entry: (entry["cpu_percent"], entry["memory_percent"]), reverse=True
        )[:limit]
        result["containers_memory"] = sorted(
            containers, key=lambda entry: (entry["memory_percent"], entry["cpu_percent"]), reverse=True
        )[:limit]
    _top_processes_cache = (now, result)
    return result


_SHORT_MEMO_TTL_SECONDS = 1.0
_sensor_memo: tuple[float, dict[str, list[Any]] | None] | None = None
_docker_scan_memo: tuple[float, tuple[int, list[str], dict[str, str]] | None] | None = None


def _read_sensor_temperatures() -> dict[str, list[Any]] | None:
    """psutil.sensors_temperatures(), shared by the CPU and disk readings of one collection."""
    global _sensor_memo
    now = time.monotonic()
    if _sensor_memo is not None and now - _sensor_memo[0] <= _SHORT_MEMO_TTL_SECONDS:
        return _sensor_memo[1]
    try:
        temps = psutil.sensors_temperatures() or None
    except (AttributeError, NotImplementedError):
        temps = None
    _sensor_memo = (now, temps)
    return temps


def _get_cpu_temperature(temps: dict[str, list[Any]] | None = None) -> float | None:
    if temps is None:
        temps = _read_sensor_temperatures()
    if not temps:
        return None
    # Try common sensor labels
    for key in ("coretemp", "cpu_thermal", "soc_thermal"):
        if key in temps:
            entries = temps[key]
            if entries:
                return float(sum(entry.current for entry in entries if entry.current) / len(entries))
    # Fallback: use first reading
    for entries in temps.values():
        if entries:
            values = [entry.current for entry in entries if entry.current is not None]
            if values:
                return float(sum(values) / len(values))
    return None


def _sysfs_roots() -> list[str]:
    """sysfs is not namespaced, so the container's /sys is the host's; the bind mount is a fallback."""
    roots = ["/sys"]
    host_target = _normalize_mount_path(settings.host_root_target) if settings.host_root_target else ""
    if host_target and host_target != "/":
        roots.append(os.path.join(host_target, "sys"))
    return roots


def _read_sysfs_int(path: str) -> int | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return int(handle.read().strip())
    except (OSError, ValueError):
        return None


def _read_sysfs_text(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return None


def _collect_fan_snapshot() -> dict[str, int | None]:
    """Cooling fan level and speed (Raspberry Pi 5 active cooler and similar pwm-fan devices).

    The level comes from the thermal cooling device (``cur_state`` out of ``max_state``, 0 = off)
    and the speed from the first hwmon fan tachometer (``fan1_input``, RPM).
    """
    result: dict[str, int | None] = {"fan_level": None, "fan_max_level": None, "fan_rpm": None}
    for root in _sysfs_roots():
        for device in sorted(glob.glob(os.path.join(root, "class", "thermal", "cooling_device*"))):
            device_type = (_read_sysfs_text(os.path.join(device, "type")) or "").lower()
            if "fan" not in device_type:
                continue
            level = _read_sysfs_int(os.path.join(device, "cur_state"))
            if level is None:
                continue
            result["fan_level"] = level
            result["fan_max_level"] = _read_sysfs_int(os.path.join(device, "max_state"))
            break
        for hwmon in sorted(glob.glob(os.path.join(root, "class", "hwmon", "hwmon*"))):
            rpm = _read_sysfs_int(os.path.join(hwmon, "fan1_input"))
            if rpm is not None:
                result["fan_rpm"] = rpm
                break
        if result["fan_level"] is not None or result["fan_rpm"] is not None:
            break
    return result


def _normalize_mount_path(path: str) -> str:
    cleaned = str(path).strip()
    if not cleaned:
        return "/"
    if cleaned != "/":
        cleaned = cleaned.rstrip("/")
    return cleaned or "/"


def _host_candidate_paths(path: str) -> list[str]:
    normalized_path = _normalize_mount_path(path) if path.startswith("/") else path.strip()
    candidates: list[str] = []
    host_root = _normalize_mount_path(settings.host_root_target) if settings.host_root_target else ""
    if host_root and host_root != "/":
        stripped = normalized_path.lstrip("/")
        host_path = os.path.join(host_root, stripped) if stripped else host_root
        candidates.append(host_path)
    candidates.append(normalized_path)
    # Preserve order and deduplicate
    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        token = _normalize_mount_path(candidate) if candidate.startswith("/") else candidate
        if token not in seen:
            seen.add(token)
            deduped.append(token)
    return deduped


def _host_root() -> str | None:
    host_root = _normalize_mount_path(settings.host_root_target) if settings.host_root_target else ""
    if not host_root or host_root == "/":
        return None
    return host_root


def _candidate_host_command_invocations(command: str, args: list[str], *, include_chroot: bool = True) -> list[list[str]]:
    candidates: list[list[str]] = []
    local_binary = shutil.which(command)
    if local_binary:
        candidates.append([local_binary, *args])

    host_root = _host_root()
    chroot_bin = shutil.which("chroot")
    if include_chroot and host_root and chroot_bin:
        for base in (f"/usr/bin/{command}", f"/bin/{command}", f"/usr/sbin/{command}", f"/sbin/{command}"):
            host_binary = os.path.join(host_root, base.lstrip("/"))
            if os.path.exists(host_binary):
                candidates.append([chroot_bin, host_root, base, *args])

    seen: set[str] = set()
    deduped: list[list[str]] = []
    for candidate in candidates:
        key = " ".join(candidate)
        if key not in seen:
            seen.add(key)
            deduped.append(candidate)
    return deduped


def _read_text_tail(path: str, max_bytes: int = 131_072) -> str:
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8", errors="ignore") as handle:
            return handle.read()
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - max_bytes))
        return handle.read().decode("utf-8", errors="ignore")


def _candidate_ssh_log_files() -> list[str]:
    patterns = (
        "/var/log/auth.log",
        "/var/log/auth.log.*",
        "/var/log/secure",
        "/var/log/secure*",
        "/var/log/messages",
        "/var/log/messages*",
    )
    found: list[str] = []
    for pattern in patterns:
        for candidate_pattern in _host_candidate_paths(pattern):
            matches = glob.glob(candidate_pattern)
            if matches:
                found.extend(matches)
    # Deduplicate and keep newest logs first
    unique = sorted(set(found), key=lambda path: os.path.getmtime(path) if os.path.exists(path) else 0, reverse=True)
    return unique[:4]


def _parse_syslog_timestamp(line: str, now: datetime) -> datetime | None:
    match = _SSH_LOG_TIMESTAMP_RE.match(line)
    if not match:
        return None
    stamp = match.group("stamp")
    try:
        parsed = datetime.strptime(f"{now.year} {stamp}", "%Y %b %d %H:%M:%S")
    except ValueError:
        return None
    if parsed > now + timedelta(days=1):
        parsed = parsed.replace(year=parsed.year - 1)
    return parsed


def _extract_ssh_failure_details(line: str) -> dict[str, Any] | None:
    normalized = " ".join(line.split())

    match = _SSH_FAILURE_DETAILS_RE.search(normalized)
    if match:
        return {
            "method": (match.group("method") or "").lower() or None,
            "username": match.group("username"),
            "source_ip": match.group("source_ip"),
            "port": int(match.group("port")) if match.group("port") else None,
            "raw_line": normalized[-500:],
        }

    match = _SSH_INVALID_USER_RE.search(normalized)
    if match:
        return {
            "method": "invalid-user",
            "username": match.group("username"),
            "source_ip": match.group("source_ip"),
            "port": int(match.group("port")) if match.group("port") else None,
            "raw_line": normalized[-500:],
        }

    match = _SSH_AUTH_FAILURE_RE.search(normalized)
    if match:
        return {
            "method": "authentication-failure",
            "username": match.group("username"),
            "source_ip": match.group("source_ip"),
            "port": None,
            "raw_line": normalized[-500:],
        }

    match = _SSH_PREAUTH_DISCONNECT_RE.search(normalized)
    if match:
        return {
            "method": "preauth-disconnect",
            "username": match.group("username"),
            "source_ip": match.group("source_ip"),
            "port": int(match.group("port")) if match.group("port") else None,
            "raw_line": normalized[-500:],
        }

    return None


def _extract_ssh_success_details(line: str) -> dict[str, Any] | None:
    normalized = " ".join(line.split())
    match = _SSH_SUCCESS_DETAILS_RE.search(normalized)
    if not match:
        return None
    return {
        "method": (match.group("method") or "").lower() or None,
        "username": match.group("username"),
        "source_ip": match.group("source_ip"),
        "port": int(match.group("port")) if match.group("port") else None,
        "raw_line": normalized[-500:],
    }


def _extract_ssh_login_ages() -> tuple[int | None, int | None, dict[str, Any] | None, dict[str, Any] | None]:
    now = datetime.now()
    latest_success: datetime | None = None
    latest_failure: datetime | None = None
    latest_success_details: dict[str, Any] | None = None
    latest_failure_details: dict[str, Any] | None = None
    for path in _candidate_ssh_log_files():
        try:
            payload = _read_text_tail(path)
        except (OSError, gzip.BadGzipFile):
            continue
        for line in payload.splitlines():
            if "sshd" not in line:
                continue
            stamp = _parse_syslog_timestamp(line, now)
            if stamp is None:
                continue
            if any(marker in line for marker in _SSH_SUCCESS_MARKERS):
                if latest_success is None or stamp > latest_success:
                    latest_success = stamp
                    latest_success_details = _extract_ssh_success_details(line)
            if any(marker in line for marker in _SSH_FAILURE_MARKERS):
                if latest_failure is None or stamp > latest_failure:
                    latest_failure = stamp
                    latest_failure_details = _extract_ssh_failure_details(line)
    if latest_success is None or latest_failure is None:
        journal_success, journal_failure, journal_success_details, journal_failure_details = _extract_ssh_login_ages_from_journal()
        if latest_success is None:
            latest_success = journal_success
            latest_success_details = journal_success_details
        if latest_failure is None:
            latest_failure = journal_failure
            latest_failure_details = journal_failure_details
    success_age = int((now - latest_success).total_seconds()) if latest_success else None
    failure_age = int((now - latest_failure).total_seconds()) if latest_failure else None
    if success_age is None:
        success_age = _extract_login_age_from_last_command("last", "/var/log/wtmp")
    if failure_age is None:
        failure_age = _extract_login_age_from_last_command("lastb", "/var/log/btmp")
    if success_age is not None:
        success_age = max(0, success_age)
    if failure_age is not None:
        failure_age = max(0, failure_age)
    return success_age, failure_age, latest_success_details, latest_failure_details


def _journal_directories() -> list[str]:
    paths: list[str] = []
    for raw in ("/var/log/journal", "/run/log/journal"):
        for candidate in _host_candidate_paths(raw):
            if os.path.isdir(candidate):
                paths.append(candidate)
    deduped: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if path not in seen:
            seen.add(path)
            deduped.append(path)
    return deduped


def _journal_root() -> str | None:
    host_root = _host_root()
    if host_root is None:
        return None
    for raw in ("/var/log/journal", "/run/log/journal"):
        for candidate in _host_candidate_paths(raw):
            if os.path.isdir(candidate):
                return host_root
    return None


def _parse_journal_short_unix_timestamp(line: str) -> datetime | None:
    match = _JOURNAL_SHORT_UNIX_RE.match(line.strip())
    if not match:
        return None
    try:
        return datetime.fromtimestamp(float(match.group("stamp")))
    except (TypeError, ValueError, OSError):
        return None


def _extract_ssh_login_ages_from_journal() -> tuple[datetime | None, datetime | None, dict[str, Any] | None, dict[str, Any] | None]:
    journal_root = _journal_root()
    directories = _journal_directories() if journal_root is None else []
    if journal_root is None and not directories:
        return None, None, None, None

    def _run_journal(args: list[str]) -> str:
        base_args = ["--no-pager", "-n", "200", "-o", "short-unix"]
        if journal_root is not None:
            for command in _candidate_host_command_invocations("journalctl", [*base_args, *args]):
                try:
                    result = subprocess.run(
                        command,
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=8,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    continue
                if result.returncode == 0 and result.stdout:
                    return result.stdout
            for command in _candidate_host_command_invocations(
                "journalctl",
                [f"--root={journal_root}", *base_args, *args],
                include_chroot=False,
            ):
                try:
                    result = subprocess.run(
                        command,
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=8,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    continue
                if result.returncode == 0 and result.stdout:
                    return result.stdout
            return ""

        for directory in directories:
            for command in _candidate_host_command_invocations(
                "journalctl",
                ["--directory", directory, *base_args, *args],
                include_chroot=False,
            ):
                try:
                    result = subprocess.run(
                        command,
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=8,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    continue
                if result.returncode == 0 and result.stdout:
                    return result.stdout
        return ""

    latest_success: datetime | None = None
    latest_failure: datetime | None = None
    latest_success_details: dict[str, Any] | None = None
    latest_failure_details: dict[str, Any] | None = None
    output = _run_journal(["-u", "ssh"])
    if not output:
        output = _run_journal(["_COMM=sshd"])
    for line in output.splitlines():
        if "sshd" not in line:
            continue
        stamp = _parse_journal_short_unix_timestamp(line)
        if stamp is None:
            continue
        if any(marker in line for marker in _SSH_SUCCESS_MARKERS):
            if latest_success is None or stamp > latest_success:
                latest_success = stamp
                latest_success_details = _extract_ssh_success_details(line)
        if any(marker in line for marker in _SSH_FAILURE_MARKERS):
            if latest_failure is None or stamp > latest_failure:
                latest_failure = stamp
                latest_failure_details = _extract_ssh_failure_details(line)
    return latest_success, latest_failure, latest_success_details, latest_failure_details


def _extract_login_age_from_last_command(command: str, host_log_path: str) -> int | None:
    now = datetime.now()
    for candidate in _host_candidate_paths(host_log_path):
        for invocation in _candidate_host_command_invocations(
            command,
            ["-F", "-w", "-f", candidate, "-n", "20"],
            include_chroot=False,
        ):
            try:
                result = subprocess.run(
                    invocation,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if result.returncode not in (0, 1):
                continue
            output = (result.stdout or "").strip()
            if not output:
                continue
            for line in output.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith(("wtmp begins", "btmp begins")):
                    continue
                if stripped.startswith(("reboot", "shutdown", "runlevel")):
                    continue
                match = _LAST_OUTPUT_TIMESTAMP_RE.search(stripped)
                if not match:
                    continue
                try:
                    stamp = datetime.strptime(match.group(1), "%a %b %d %H:%M:%S %Y")
                except ValueError:
                    continue
                return max(0, int((now - stamp).total_seconds()))
    if _host_root():
        for invocation in _candidate_host_command_invocations(command, ["-F", "-w", "-f", host_log_path, "-n", "20"]):
            try:
                result = subprocess.run(
                    invocation,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if result.returncode not in (0, 1):
                continue
            output = (result.stdout or "").strip()
            if not output:
                continue
            for line in output.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith(("wtmp begins", "btmp begins")):
                    continue
                if stripped.startswith(("reboot", "shutdown", "runlevel")):
                    continue
                match = _LAST_OUTPUT_TIMESTAMP_RE.search(stripped)
                if not match:
                    continue
                try:
                    stamp = datetime.strptime(match.group(1), "%a %b %d %H:%M:%S %Y")
                except ValueError:
                    continue
                return max(0, int((now - stamp).total_seconds()))
    return None


def _coerce_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    token = value.strip().lower()
    if token in _SSH_TRUE_VALUES:
        return True
    if token in _SSH_FALSE_VALUES:
        return False
    return default


def _resolve_ssh_include_patterns(raw_value: str, from_file: str) -> list[str]:
    patterns: list[str] = []
    base_dir = os.path.dirname(from_file)
    for token in raw_value.split():
        cleaned = token.strip().strip('"').strip("'")
        if not cleaned:
            continue
        if cleaned.startswith("/"):
            patterns.extend(_host_candidate_paths(cleaned))
        else:
            patterns.append(os.path.join(base_dir, cleaned))
    return patterns


def _read_sshd_effective_settings() -> tuple[bool, bool, bool, str, str, str, str, str]:
    global _ssh_settings_cache
    now = time.monotonic()
    if _ssh_settings_cache is not None:
        cached_at, cached_value = _ssh_settings_cache
        if now - cached_at <= _SSH_SETTINGS_CACHE_TTL_SECONDS:
            return cached_value

    pubkey_value: str | None = None
    permit_root_value: str | None = None
    password_auth_value: str | None = None
    kbd_interactive_value: str | None = None
    pubkey_line: str | None = None
    permit_root_line: str | None = None
    password_auth_line: str | None = None
    kbd_interactive_line: str | None = None
    seen: set[str] = set()

    def parse_file(path: str) -> None:
        nonlocal pubkey_value, permit_root_value, password_auth_value, kbd_interactive_value
        nonlocal pubkey_line, permit_root_line, password_auth_line, kbd_interactive_line
        resolved = str(Path(path).resolve())
        if resolved in seen:
            return
        seen.add(resolved)
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                lines = handle.readlines()
        except OSError:
            return
        for raw in lines:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            key, value = parts[0].lower(), parts[1].strip()
            if key == "include":
                for pattern in _resolve_ssh_include_patterns(value, path):
                    for child in sorted(glob.glob(pattern)):
                        parse_file(child)
                continue
            if key == "pubkeyauthentication":
                pubkey_value = value
                pubkey_line = f"{parts[0]} {value}"
            elif key == "permitrootlogin":
                permit_root_value = value
                permit_root_line = f"{parts[0]} {value}"
            elif key == "passwordauthentication":
                password_auth_value = value
                password_auth_line = f"{parts[0]} {value}"
            elif key in {"kbdinteractiveauthentication", "challengeresponseauthentication"}:
                kbd_interactive_value = value
                kbd_interactive_line = f"{parts[0]} {value}"

    for candidate in _host_candidate_paths("/etc/ssh/sshd_config"):
        parse_file(candidate)

    pubkey_enabled = _coerce_bool(pubkey_value, default=True)
    password_auth_enabled = _coerce_bool(password_auth_value, default=True)
    kbd_interactive_enabled = _coerce_bool(kbd_interactive_value, default=False)
    permit_root_token = permit_root_value.strip().lower() if permit_root_value else "prohibit-password"
    result = (
        pubkey_enabled,
        not password_auth_enabled,
        not kbd_interactive_enabled,
        permit_root_token,
        pubkey_line or f"PubkeyAuthentication {'yes' if pubkey_enabled else 'no'} (default)",
        password_auth_line or f"PasswordAuthentication {'no' if not password_auth_enabled else 'yes'} (default)",
        kbd_interactive_line
        or f"KbdInteractiveAuthentication {'no' if not kbd_interactive_enabled else 'yes'} (default)",
        permit_root_line or f"PermitRootLogin {permit_root_token} (default)",
    )
    _ssh_settings_cache = (now, result)
    return result


def _collect_ssh_snapshot() -> dict[str, Any]:
    global _ssh_snapshot_cache
    now = time.monotonic()
    if _ssh_snapshot_cache is not None:
        cached_at, cached_value = _ssh_snapshot_cache
        if now - cached_at <= _SSH_SNAPSHOT_CACHE_TTL_SECONDS:
            return dict(cached_value)

    success_age_seconds, failure_age_seconds, success_details, failure_details = _extract_ssh_login_ages()
    (
        pubkey_enabled,
        password_auth_disabled,
        kbd_interactive_disabled,
        permit_root_token,
        pubkey_line,
        password_auth_line,
        kbd_interactive_line,
        permit_root_line,
    ) = _read_sshd_effective_settings()
    strict_root_policy = permit_root_token == "no"
    root_password_disabled = permit_root_token in _SSH_ROOT_PASSWORD_DISABLED_VALUES
    if not pubkey_enabled and not root_password_disabled:
        status_level = 2
    elif (
        not pubkey_enabled
        or not strict_root_policy
        or not password_auth_disabled
        or not kbd_interactive_disabled
    ):
        status_level = 1
    else:
        status_level = 0
    status_text = "ok" if status_level == 0 else "warn" if status_level == 1 else "critical"
    result = {
        "ssh_last_successful_login_seconds": success_age_seconds,
        "ssh_last_successful_auth_method": success_details.get("method") if success_details else None,
        "ssh_last_successful_username": success_details.get("username") if success_details else None,
        "ssh_last_successful_source_ip": success_details.get("source_ip") if success_details else None,
        "ssh_last_successful_port": success_details.get("port") if success_details else None,
        "ssh_last_successful_line": success_details.get("raw_line") if success_details else None,
        "ssh_last_unsuccessful_attempt_seconds": failure_age_seconds,
        "ssh_last_failure_auth_method": failure_details.get("method") if failure_details else None,
        "ssh_last_failure_username": failure_details.get("username") if failure_details else None,
        "ssh_last_failure_source_ip": failure_details.get("source_ip") if failure_details else None,
        "ssh_last_failure_port": failure_details.get("port") if failure_details else None,
        "ssh_last_failure_line": failure_details.get("raw_line") if failure_details else None,
        "ssh_pubkey_auth_enabled": pubkey_enabled,
        "ssh_root_password_login_disabled": root_password_disabled,
        "ssh_password_auth_disabled": password_auth_disabled,
        "ssh_kbd_interactive_auth_disabled": kbd_interactive_disabled,
        "ssh_permit_root_login_mode": permit_root_token,
        "ssh_pubkey_auth_line": pubkey_line,
        "ssh_password_auth_line": password_auth_line,
        "ssh_kbd_interactive_auth_line": kbd_interactive_line,
        "ssh_permit_root_login_line": permit_root_line,
        "ssh_status_level": status_level,
        "ssh_status": status_text,
    }
    _ssh_snapshot_cache = (now, result)
    return dict(result)


def _configured_mount_points() -> tuple[list[str], bool]:
    configured: list[str] = []
    auto = False
    for entry in settings.mounted_points:
        token = str(entry).strip()
        if not token:
            continue
        if token.lower() == "auto" or token == "*":
            auto = True
            continue
        configured.append(_normalize_mount_path(token))
    return configured, auto


def _discover_mount_points() -> list[str]:
    mounts: list[str] = []
    host_target = _normalize_mount_path(settings.host_root_target) if settings.host_root_target else ""
    try:
        partitions = psutil.disk_partitions(all=True)
    except Exception:
        partitions = []
    for partition in partitions:
        mount = getattr(partition, "mountpoint", "")
        mount = _normalize_mount_path(mount)
        if not mount:
            continue
        if host_target and (
            mount == host_target or mount.startswith(f"{host_target}/")
        ):
            suffix = mount[len(host_target):]
            if not suffix:
                translated = "/"
            else:
                translated = _normalize_mount_path(suffix)
                if not translated.startswith("/"):
                    translated = f"/{translated}"
            if translated not in mounts:
                mounts.append(translated)
            continue
        if mount not in mounts:
            mounts.append(mount)
    if "/" not in mounts:
        mounts.insert(0, "/")
    return mounts


def _resolve_mount_points() -> list[str]:
    global _mount_points_cache
    now = time.monotonic()
    if _mount_points_cache is not None:
        cached_at, cached_value = _mount_points_cache
        if now - cached_at <= _MOUNT_POINTS_CACHE_TTL_SECONDS:
            return list(cached_value)

    configured, auto = _configured_mount_points()
    discovered: list[str] = []
    if auto or not configured:
        discovered = _discover_mount_points()
    sources: list[str]
    if auto:
        sources = [*configured, *discovered]
    elif configured:
        sources = configured
    else:
        sources = discovered or ["/"]
    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for mount in sources:
        mount_path = _normalize_mount_path(mount)
        if mount_path not in seen:
            seen.add(mount_path)
            result.append(mount_path)
    if not result:
        result.append("/")
    _mount_points_cache = (now, list(result))
    return list(result)


def _get_os_version() -> str:
    global _os_version_cache
    now = time.monotonic()
    if _os_version_cache is not None:
        cached_at, cached_value = _os_version_cache
        if now - cached_at <= _OS_VERSION_CACHE_TTL_SECONDS:
            return cached_value

    result = platform.platform()
    _os_version_cache = (now, result)
    return result


def _host_path(path: str) -> str:
    normalized = _normalize_mount_path(settings.host_root_target) if settings.host_root_target else ""
    if normalized and normalized != "/":
        suffix = path.lstrip("/")
        return os.path.join(normalized, suffix) if suffix else normalized
    return path


def _host_file_exists(path: str) -> bool:
    for candidate in _host_candidate_paths(path):
        if os.path.exists(candidate):
            return True
    return False


def _read_host_text(path: str) -> str | None:
    for candidate in _host_candidate_paths(path):
        try:
            with open(candidate, "r", encoding="utf-8", errors="ignore") as handle:
                return handle.read()
        except OSError:
            continue
    return None


def _read_host_key_value_file(path: str) -> dict[str, str]:
    payload = _read_host_text(path)
    if payload is None:
        return {}
    result: dict[str, str] = {}
    for raw_line in payload.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            result[key] = value
    return result


def _count_host_file_lines(path: str) -> int:
    payload = _read_host_text(path)
    if payload is None:
        return 0
    return sum(1 for line in payload.splitlines() if line.strip())


def _host_network_namespace_path() -> str | None:
    candidate = os.path.join(_host_path("/proc/1/ns/net"))
    if os.path.exists(candidate):
        return candidate
    return None


def _host_network_command_invocations(command: str, args: list[str], *, include_chroot: bool = True) -> list[list[str]]:
    nsenter_bin = shutil.which("nsenter")
    namespace_path = _host_network_namespace_path()
    if not nsenter_bin or not namespace_path:
        return []
    invocations: list[list[str]] = []
    for candidate in _candidate_host_command_invocations(command, args, include_chroot=include_chroot):
        invocations.append([nsenter_bin, f"--net={namespace_path}", *candidate])
    return invocations


def _run_host_network_command(
    command: str,
    args: list[str],
    *,
    timeout: int = 8,
    include_chroot: bool = True,
) -> str | None:
    for invocation in _host_network_command_invocations(command, args, include_chroot=include_chroot):
        try:
            result = subprocess.run(
                invocation,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode != 0:
            continue
        output = (result.stdout or "").strip()
        if output:
            return output
    return None


def _detect_hotspot_interface() -> str | None:
    config = _read_host_key_value_file("/etc/hostapd/hostapd.conf")
    interface = (config.get("interface") or "").strip()
    return interface or None


def _detect_hotspot_ssid() -> str | None:
    config = _read_host_key_value_file("/etc/hostapd/hostapd.conf")
    ssid = (config.get("ssid") or "").strip()
    return ssid or None


def _host_interface_ipv4(interface: str | None) -> str | None:
    if not interface:
        return None
    output = _run_host_network_command("ip", ["-4", "-o", "addr", "show", "dev", interface], timeout=6)
    if not output:
        return None
    match = _IP_ADDR_SHOW_RE.search(output)
    return match.group("address") if match else None


def _normalize_public_ip_probe_value(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        parsed = ipaddress.ip_address(candidate)
    except ValueError:
        return None
    if parsed.version != 4:
        return None
    return str(parsed)


def _normalize_country_code_probe_value(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().upper()
    if re.fullmatch(r"[A-Z]{2}", candidate):
        return candidate
    try:
        payload = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    nested_candidate = payload.get("country_code")
    if isinstance(nested_candidate, str):
        nested_value = nested_candidate.strip().upper()
        if re.fullmatch(r"[A-Z]{2}", nested_value):
            return nested_value
    return None


def _probe_public_ip(*, cache_key: tuple[str, str], curl_args: list[str]) -> tuple[str | None, str | None]:
    now = time.monotonic()
    cached = _public_ip_probe_cache.get(cache_key)
    if cached is not None:
        cached_at, cached_value = cached
        if now - cached_at <= _NETWORK_EGRESS_CACHE_TTL_SECONDS:
            return cached_value

    result: tuple[str | None, str | None] = (None, None)
    for service_name, service_url in _PUBLIC_IP_PROBE_SERVICES:
        base_args = ["-4fsSL", "--max-time", "8", *_curl_user_agent_args(), *curl_args, service_url]
        output = _run_host_network_command("curl", base_args, timeout=10)
        value = _normalize_public_ip_probe_value(output)
        if value is not None:
            result = (value, service_name)
            break

    _public_ip_probe_cache[cache_key] = (now, result)
    return result


def _probe_country_code(*, ip_address_value: str) -> tuple[str | None, str | None]:
    now = time.monotonic()
    cache_key = ("ip", ip_address_value)
    cached = _country_code_probe_cache.get(cache_key)
    if cached is not None:
        cached_at, cached_value = cached
        if now - cached_at <= _NETWORK_EGRESS_CACHE_TTL_SECONDS:
            return cached_value

    result: tuple[str | None, str | None] = (None, None)
    for service_name, service_url_template in _COUNTRY_CODE_PROBE_SERVICES:
        service_url = service_url_template.format(ip=ip_address_value)
        base_args = ["-4fsSL", "--max-time", "8", *_curl_user_agent_args(), service_url]
        output = _run_host_network_command("curl", base_args, timeout=10)
        value = _normalize_country_code_probe_value(output)
        if value is not None:
            result = (value, service_name)
            break

    _country_code_probe_cache[cache_key] = (now, result)
    return result


def _curl_user_agent_args() -> list[str]:
    return ["-A", f"server-monitor/{settings.version}"]


def _probe_public_ip_via_interface(interface: str) -> tuple[str | None, str | None]:
    return _probe_public_ip(cache_key=("interface", interface), curl_args=["--interface", interface])


def _probe_public_ip_via_source_ip(source_ip: str) -> tuple[str | None, str | None]:
    return _probe_public_ip(cache_key=("source_ip", source_ip), curl_args=["--interface", source_ip])


def _probe_public_ip_via_proxy(proxy_url: str) -> tuple[str | None, str | None]:
    return _probe_public_ip(cache_key=("proxy", proxy_url), curl_args=["-x", proxy_url])


def _probe_default_public_ip() -> tuple[str | None, str | None]:
    return _probe_public_ip(cache_key=("default", ""), curl_args=[])


def _collect_telegram_bot_snapshot(running_containers: list[str] | None) -> dict[str, Any]:
    global _telegram_snapshot_cache
    now = time.monotonic()
    if _telegram_snapshot_cache is not None:
        cached_at, cached_value = _telegram_snapshot_cache
        if now - cached_at <= _SERVICE_SNAPSHOT_CACHE_TTL_SECONDS:
            return dict(cached_value)

    names = {name.strip() for name in (running_containers or []) if isinstance(name, str) and name.strip()}
    bot_running = "server-monitor-telegram-bot" in names
    proxy_running = "server-monitor-telegram-egress-proxy" in names

    if bot_running and proxy_running:
        status_level = 0
        status = "ok"
    elif bot_running:
        status_level = 1
        status = "warn"
    elif running_containers is None:
        status_level = None
        status = None
    else:
        status_level = 2
        status = "critical"

    result = {
        "telegram_bot_status_level": status_level,
        "telegram_bot_status": status,
        "telegram_bot_running": bot_running if running_containers is not None else None,
        "telegram_egress_proxy_running": proxy_running if running_containers is not None else None,
    }
    _telegram_snapshot_cache = (now, result)
    return dict(result)


_WIFI_STATION_LINE = re.compile(r"^\s*Station\s+([0-9a-fA-F:]{17})")


def _parse_wifi_associated_clients(output: str | None, *, max_clients: int = 32) -> list[dict[str, Any]]:
    """Parse `iw dev <iface> station dump` into MAC + optional signal (dBm)."""
    if not output:
        return []
    clients: list[dict[str, Any]] = []
    current_mac: str | None = None
    current_signal: int | None = None

    def flush() -> None:
        nonlocal current_mac, current_signal
        if current_mac is not None and len(clients) < max_clients:
            entry: dict[str, Any] = {"mac": current_mac}
            if current_signal is not None:
                entry["signal_dbm"] = current_signal
            clients.append(entry)
        current_mac = None
        current_signal = None

    for line in output.splitlines():
        m = _WIFI_STATION_LINE.match(line)
        if m:
            flush()
            current_mac = m.group(1).lower()
            current_signal = None
            continue
        if current_mac is None:
            continue
        stripped = line.strip()
        if stripped.startswith("signal") and "dBm" in stripped:
            sig_match = re.search(r"(-?\d+)\s*dBm", stripped)
            if sig_match:
                try:
                    current_signal = int(sig_match.group(1))
                except ValueError:
                    pass
    flush()
    return clients


def _collect_wifi_snapshot() -> dict[str, Any]:
    global _wifi_snapshot_cache
    now = time.monotonic()
    if _wifi_snapshot_cache is not None:
        cached_at, cached_value = _wifi_snapshot_cache
        if now - cached_at <= _SERVICE_SNAPSHOT_CACHE_TTL_SECONDS:
            return dict(cached_value)

    interface = _detect_hotspot_interface()
    ssid = _detect_hotspot_ssid()
    operstate = None
    if interface:
        operstate_text = _read_host_text(f"/sys/class/net/{interface}/operstate")
        operstate = operstate_text.strip() if isinstance(operstate_text, str) and operstate_text.strip() else None
    hostapd_socket_present = interface is not None and _host_file_exists(f"/run/hostapd/{interface}")
    iw_out = (
        _run_host_network_command("iw", ["dev", interface, "station", "dump"], timeout=6) if interface else None
    )
    wifi_associated_clients = _parse_wifi_associated_clients(iw_out, max_clients=32)
    client_count = len(wifi_associated_clients) if iw_out is not None else None
    dhcp_leases = _count_host_file_lines("/var/lib/misc/dnsmasq.leases")

    if interface and ssid and operstate == "up" and hostapd_socket_present:
        status_level = 0
        status = "ok"
    elif interface or ssid:
        status_level = 2 if operstate not in (None, "up") or not hostapd_socket_present else 1
        status = "critical" if status_level == 2 else "warn"
    else:
        status_level = None
        status = None

    result = {
        "wifi_status_level": status_level,
        "wifi_status": status,
        "wifi_ssid": ssid,
        "wifi_interface": interface,
        "wifi_client_count": client_count,
        "wifi_associated_clients": wifi_associated_clients,
        "wifi_dhcp_lease_count": dhcp_leases,
        "wifi_operstate": operstate,
        "wifi_hostapd_socket_present": hostapd_socket_present if interface else None,
    }
    _wifi_snapshot_cache = (now, result)
    return dict(result)


def _vpn_interface_name(active_backend: str | None) -> str | None:
    if not active_backend:
        return None
    token = active_backend.strip().lower()
    if token == "xray":
        return None
    return token


def _read_active_vpn_backend() -> str | None:
    payload = _read_host_text("/usr/local/etc/vpn-backend/active-backend")
    if not isinstance(payload, str):
        return None
    token = payload.strip()
    return token or None


def _collect_vpn_snapshot() -> dict[str, Any]:
    global _vpn_snapshot_cache
    now = time.monotonic()
    if _vpn_snapshot_cache is not None:
        cached_at, cached_value = _vpn_snapshot_cache
        if now - cached_at <= _SERVICE_SNAPSHOT_CACHE_TTL_SECONDS:
            return dict(cached_value)

    active_backend = _read_active_vpn_backend()
    hotspot_interface = _detect_hotspot_interface()
    if not active_backend and not hotspot_interface:
        result = {
            "vpn_status_level": None,
            "vpn_status": None,
            "vpn_active_backend": None,
            "vpn_hotspot_public_ip": None,
            "vpn_hotspot_probe_service": None,
            "vpn_hotspot_country_code": None,
            "vpn_expected_public_ip": None,
            "vpn_expected_probe_service": None,
            "vpn_expected_country_code": None,
            "vpn_default_public_ip": None,
            "vpn_default_probe_service": None,
            "vpn_default_country_code": None,
        }
        _vpn_snapshot_cache = (now, result)
        return dict(result)

    hotspot_source_ip = _host_interface_ipv4(hotspot_interface)
    default_public_ip, default_probe_service = _probe_default_public_ip()

    expected_public_ip: str | None = None
    expected_probe_service: str | None = None
    if active_backend:
        if active_backend.strip().lower() == "xray":
            expected_public_ip, expected_probe_service = _probe_public_ip_via_proxy(_XRAY_PROXY_URL)
        else:
            interface = _vpn_interface_name(active_backend)
            if interface:
                expected_public_ip, expected_probe_service = _probe_public_ip_via_interface(interface)

    hotspot_public_ip: str | None = None
    hotspot_probe_service: str | None = None
    if hotspot_source_ip:
        hotspot_public_ip, hotspot_probe_service = _probe_public_ip_via_source_ip(hotspot_source_ip)

    hotspot_country_code = _probe_country_code(ip_address_value=hotspot_public_ip)[0] if hotspot_public_ip else None
    expected_country_code = _probe_country_code(ip_address_value=expected_public_ip)[0] if expected_public_ip else None
    default_country_code = _probe_country_code(ip_address_value=default_public_ip)[0] if default_public_ip else None

    if not active_backend:
        status_level = None
        status = None
    elif hotspot_public_ip and expected_public_ip:
        if hotspot_public_ip == expected_public_ip:
            status_level = 0
            status = "ok"
        else:
            status_level = 2
            status = "critical"
    elif hotspot_public_ip or expected_public_ip:
        status_level = 1
        status = "warn"
    else:
        status_level = 2
        status = "critical"

    result = {
        "vpn_status_level": status_level,
        "vpn_status": status,
        "vpn_active_backend": active_backend,
        "vpn_hotspot_public_ip": hotspot_public_ip,
        "vpn_hotspot_probe_service": hotspot_probe_service,
        "vpn_hotspot_country_code": hotspot_country_code,
        "vpn_expected_public_ip": expected_public_ip,
        "vpn_expected_probe_service": expected_probe_service,
        "vpn_expected_country_code": expected_country_code,
        "vpn_default_public_ip": default_public_ip,
        "vpn_default_probe_service": default_probe_service,
        "vpn_default_country_code": default_country_code,
    }
    _vpn_snapshot_cache = (now, result)
    return dict(result)


def _mounted_usage(mount_points: list[str]) -> list[dict[str, Any]]:
    usage = []
    for mount in mount_points:
        stats = _get_disk_usage(mount)
        if stats is None:
            continue
        usage.append(
            {
                "mount_point": mount,
                "total_gb": round(stats.total / (1024 ** 3), 2),
                "used_percent": round(stats.percent, 2),
            }
        )
    return usage


_CONTAINER_DIR_PATTERN = re.compile(r"^[a-f0-9]{64}$")


def _docker_container_roots() -> list[str]:
    host_target = _normalize_mount_path(settings.host_root_target) if settings.host_root_target else ""
    candidate_roots: list[str] = []
    if host_target and host_target != "/":
        candidate_roots.append(os.path.join(host_target, "var/lib/docker/containers"))
    candidate_roots.append("/var/lib/docker/containers")
    return candidate_roots


def _scan_docker_containers() -> tuple[int, list[str], dict[str, str]] | None:
    """One pass over /var/lib/docker/containers: (total dirs, running names, names by id).

    Memoised for a second so the count and the running list of one collection share a scan.
    """
    global _docker_scan_memo
    now = time.monotonic()
    if _docker_scan_memo is not None and now - _docker_scan_memo[0] <= _SHORT_MEMO_TTL_SECONDS:
        return _docker_scan_memo[1]
    result = _scan_docker_containers_uncached()
    _docker_scan_memo = (now, result)
    return result


def _scan_docker_containers_uncached() -> tuple[int, list[str], dict[str, str]] | None:
    for root in _docker_container_roots():
        try:
            entries = os.listdir(root)
        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
            continue

        total = 0
        running: list[str] = []
        names_by_id: dict[str, str] = {}
        for entry in entries:
            if not _CONTAINER_DIR_PATTERN.match(entry):
                continue
            container_dir = os.path.join(root, entry)
            if not os.path.isdir(container_dir):
                continue
            total += 1
            config_path = os.path.join(container_dir, "config.v2.json")
            try:
                with open(config_path, "r", encoding="utf-8") as handle:
                    config = json.load(handle)
            except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
                continue

            name = config.get("Name") if isinstance(config, dict) else None
            if isinstance(name, str):
                name = name.strip().lstrip("/")
            if not name:
                name = entry[:12]
            names_by_id[entry] = name

            state = config.get("State") if isinstance(config, dict) else None
            if not isinstance(state, dict) or not bool(state.get("Running")):
                continue
            running.append(name)

        running.sort(key=str.lower)
        return total, running, names_by_id
    return None


def _count_docker_containers() -> int | None:
    scanned = _scan_docker_containers()
    if scanned is None:
        # Fallback: reuse the running-container discovery path (may be provided another way).
        running = _list_running_docker_containers()
        if isinstance(running, list):
            return len([name for name in running if isinstance(name, str) and name.strip()])
        return None
    total, running, _names = scanned
    return total if total else len(running)


def _list_running_docker_containers() -> list[str] | None:
    scanned = _scan_docker_containers()
    return scanned[1] if scanned is not None else None


def _candidate_paths_for_mount(mount: str) -> list[str]:
    candidates: list[str] = []
    host_target = _normalize_mount_path(settings.host_root_target) if settings.host_root_target else ""
    normalized_mount = _normalize_mount_path(mount)
    if host_target and host_target != "/":
        if normalized_mount == "/":
            candidates.append(host_target)
        else:
            suffix = normalized_mount.lstrip("/")
            candidate = os.path.join(host_target, suffix) if suffix else host_target
            candidates.append(_normalize_mount_path(candidate))
    candidates.append(normalized_mount)
    return candidates


def _get_disk_usage(mount: str):
    for candidate in _candidate_paths_for_mount(mount):
        try:
            return psutil.disk_usage(candidate)
        except (FileNotFoundError, PermissionError, OSError):
            continue
    return None


def _detect_warnings(payload: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    temp = payload.get("cpu_temperature_c")
    if isinstance(temp, (int, float)) and temp >= CPU_TEMP_WARN:
        warnings.append(f"High CPU temperature {temp:.1f}°C")
    ram_percent = payload.get("ram_used_percent")
    if isinstance(ram_percent, (int, float)) and ram_percent >= RAM_WARN_PERCENT:
        warnings.append(f"High RAM usage {ram_percent:.1f}%")
    disk_percent = payload.get("disk_usage_percent")
    if isinstance(disk_percent, (int, float)) and disk_percent >= DISK_WARN_PERCENT:
        warnings.append(f"Disk usage critical at {disk_percent:.1f}%")
    for volume in payload.get("mounted_usage") or []:
        percent = volume.get("used_percent")
        mount = volume.get("mount_point")
        if isinstance(percent, (int, float)) and percent >= DISK_WARN_PERCENT:
            warnings.append(f"{mount} usage critical at {percent:.1f}%")
    return warnings


def collect_metrics() -> dict[str, Any]:
    boot_time = psutil.boot_time()
    uptime_seconds = int(time.time() - boot_time)
    virtual_memory = psutil.virtual_memory()
    try:
        swap = psutil.swap_memory()
    except Exception:
        swap = None
    disk = _get_disk_usage("/") or psutil.disk_usage("/")
    load_one, load_five, load_fifteen = (None, None, None)
    try:
        load_one, load_five, load_fifteen = os.getloadavg()
    except (AttributeError, OSError):
        pass

    cpu_temp = _get_cpu_temperature()
    sensor_temps = _read_sensor_temperatures()
    fan = _collect_fan_snapshot()
    try:
        net_io = psutil.net_io_counters(pernic=True)
    except Exception:
        net_io = {}
    network_counters = [
        {"interface": name, "bytes_sent": stats.bytes_sent, "bytes_recv": stats.bytes_recv}
        for name, stats in net_io.items()
    ] if net_io else None

    disk_temps: list[dict[str, Any]] | None = None
    if sensor_temps:
        disk_temps = []
        for name, entries in sensor_temps.items():
            for entry in entries:
                label = getattr(entry, "label", None) or name
                disk_temps.append({"device": label, "temperature_c": getattr(entry, "current", None)})

    mount_points = _resolve_mount_points()
    running_containers = _list_running_docker_containers()
    docker_container_count = _count_docker_containers()
    payload: dict[str, Any] = {
        "reported_at": datetime.now(tz=timezone.utc).isoformat(),
        "hostname": socket.gethostname(),
        "monitor_version": settings.version,
        "cpu_temperature_c": cpu_temp,
        "fan_level": fan.get("fan_level"),
        "fan_max_level": fan.get("fan_max_level"),
        "fan_rpm": fan.get("fan_rpm"),
        "ram_used_percent": round(virtual_memory.percent, 2),
        "total_ram_gb": round(virtual_memory.total / (1024 ** 3), 2),
        "memory_available_gb": round(virtual_memory.available / (1024 ** 3), 2),
        "swap_used_percent": round(float(swap.percent), 2) if swap and swap.percent is not None else None,
        "docker_container_count": docker_container_count,
        "docker_running_containers": (running_containers if settings.expose_docker_running_containers else None),
        "disk_usage_percent": round(disk.percent, 2),
        "disk_total_gb": round(disk.total / (1024 ** 3), 2),
        "disk_available_gb": round(disk.free / (1024 ** 3), 2),
        "mounted_usage": _mounted_usage(mount_points),
        "configured_mounts": mount_points,
        "cpu_load": {"one": load_one, "five": load_five, "fifteen": load_fifteen},
        "network_counters": network_counters,
        "disk_temperatures": disk_temps,
        "os_version": _get_os_version(),
        "uptime_seconds": uptime_seconds,
    }
    top_processes = _collect_top_processes(limit=10)
    if top_processes:
        payload["top_processes"] = top_processes
    payload.update(_collect_ssh_snapshot())
    payload.update(_collect_telegram_bot_snapshot(running_containers))
    payload.update(_collect_wifi_snapshot())
    payload.update(_collect_vpn_snapshot())
    warnings = _detect_warnings(payload)
    if warnings:
        payload["warnings"] = warnings
    return payload


_collect_cache: tuple[float, dict[str, Any]] | None = None
_collect_lock: asyncio.Lock | None = None


def clear_collect_cache() -> None:
    global _collect_cache
    _collect_cache = None


async def collect_metrics_cached() -> dict[str, Any]:
    """Run ``collect_metrics`` in a worker thread (it blocks on psutil and subprocesses) and
    reuse the result for a few seconds so concurrent or rapid requests share one collection."""
    global _collect_cache, _collect_lock
    ttl = max(0.0, float(getattr(settings, "collect_cache_seconds", 5.0) or 0.0))
    now = time.monotonic()
    if _collect_cache is not None and ttl > 0 and now - _collect_cache[0] <= ttl:
        return dict(_collect_cache[1])
    if _collect_lock is None:
        _collect_lock = asyncio.Lock()
    async with _collect_lock:
        now = time.monotonic()
        if _collect_cache is not None and ttl > 0 and now - _collect_cache[0] <= ttl:
            return dict(_collect_cache[1])
        raw = await asyncio.to_thread(collect_metrics)
        _collect_cache = (time.monotonic(), raw)
        return dict(raw)


async def reboot_host() -> None:
    if not settings.reboot_command:
        if _try_sysrq_reboot():
            return
        raise RuntimeError("No reboot command configured and sysrq trigger unavailable")
    args = shlex.split(settings.reboot_command)
    host_root = settings.host_root_target or "/hostfs"

    def _candidate_commands() -> list[list[str]]:
        commands: list[list[str]] = []
        if args:
            commands.append(args)

        common = [
            "/sbin/shutdown",
            "/usr/sbin/shutdown",
            "/sbin/reboot",
            "/usr/sbin/reboot",
        ]

        for base in common:
            commands.append([base, *args[1:]])
            if host_root:
                commands.append([os.path.join(host_root, base.lstrip("/")), *args[1:]])

        chroot_bin = shutil.which("chroot")
        if chroot_bin and host_root:
            for base in common:
                commands.append([chroot_bin, host_root, base, *args[1:]])

        # De-duplicate by string form
        seen = set()
        unique: list[list[str]] = []
        for cmd in commands:
            key = " ".join(cmd)
            if key not in seen:
                seen.add(key)
                unique.append(cmd)
        return unique

    candidates = _candidate_commands()
    errors: list[str] = []

    for attempt_args in candidates:
        def _run():
            return subprocess.run(attempt_args, check=False, capture_output=True, text=True, timeout=10)

        try:
            result = await asyncio.to_thread(_run)
        except FileNotFoundError as exc:
            errors.append(f"{' '.join(attempt_args)}: {exc}")
            continue
        except subprocess.TimeoutExpired:
            errors.append(f"{' '.join(attempt_args)}: timed out")
            continue
        if result.returncode in (0, None):
            return
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        output = stderr or stdout or str(result.returncode)
        errors.append(f"{' '.join(attempt_args)}: {output}")

    if _try_sysrq_reboot():
        return

    detail = "; ".join(errors) if errors else "No executable reboot command found."
    raise RuntimeError(f"Reboot command failed. Attempts: {len(candidates)}. Details: {detail}")


def _try_sysrq_reboot() -> bool:
    """Fallback reboot mechanism using sysrq trigger (host must allow it)."""
    try:
        with open("/proc/sysrq-trigger", "w") as handle:
            handle.write("b")
        return True
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Sysrq reboot trigger failed: %s", exc)
        return False


# Decide once at import time whether the host process table is reachable through the bind mount.
_configure_procfs()
