from __future__ import annotations

import asyncio
import glob
import gzip
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
_SSH_SNAPSHOT_CACHE_TTL_SECONDS = 5.0
_SSH_SETTINGS_CACHE_TTL_SECONDS = 30.0
_MOUNT_POINTS_CACHE_TTL_SECONDS = 30.0
_OS_VERSION_CACHE_TTL_SECONDS = 300.0
_top_processes_cache: tuple[float, dict[str, list[dict[str, Any]]] | None] | None = None
_ssh_snapshot_cache: tuple[float, dict[str, Any]] | None = None
_ssh_settings_cache: tuple[float, tuple[bool, bool, bool, str, str, str, str, str]] | None = None
_mount_points_cache: tuple[float, list[str]] | None = None
_os_version_cache: tuple[float, str] | None = None

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
_SSH_SUCCESS_DETAILS_RE = re.compile(
    r"Accepted\s+(?P<method>[A-Za-z0-9-]+)\s+for\s+(?P<username>\S+)\s+from\s+"
    r"(?P<source_ip>\S+)(?:\s+port\s+(?P<port>\d+))?",
    re.IGNORECASE,
)


def _safe_percent(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def _collect_top_processes(limit: int = 10) -> dict[str, list[dict[str, Any]]] | None:
    global _top_processes_cache
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

    for process in processes:
        try:
            process.cpu_percent(interval=None)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    time.sleep(0.05)

    entries: list[dict[str, Any]] = []
    for process in processes:
        try:
            info = process.info
            pid = info.get("pid")
            name = (info.get("name") or "").strip() or f"pid-{pid}"
            entries.append(
                {
                    "pid": pid,
                    "name": name[:80],
                    "cpu_percent": _safe_percent(process.cpu_percent(interval=None)),
                    "memory_percent": _safe_percent(info.get("memory_percent")),
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

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

    result = {
        "cpu": cpu_entries,
        "memory": memory_entries,
    }
    _top_processes_cache = (now, result)
    return result


def _get_cpu_temperature() -> float | None:
    try:
        temps = psutil.sensors_temperatures()
    except (AttributeError, NotImplementedError):
        return None
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


def _read_text_tail(path: str, max_bytes: int = 512_000) -> str:
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
    return unique[:20]


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


def _count_docker_containers() -> int | None:
    host_target = _normalize_mount_path(settings.host_root_target) if settings.host_root_target else ""
    candidate_roots: list[str] = []
    if host_target and host_target != "/":
        candidate_roots.append(os.path.join(host_target, "var/lib/docker/containers"))
    candidate_roots.append("/var/lib/docker/containers")

    container_name_pattern = re.compile(r"^[a-f0-9]{64}$")
    for root in candidate_roots:
        try:
            entries = os.listdir(root)
        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
            continue
        count = 0
        for entry in entries:
            if not container_name_pattern.match(entry):
                continue
            full_path = os.path.join(root, entry)
            if os.path.isdir(full_path):
                count += 1
        return count

    # Fallback: if direct container directory counting is unavailable on this host,
    # reuse the running-container discovery path and return its count.
    running = _list_running_docker_containers()
    if isinstance(running, list):
        names = [name for name in running if isinstance(name, str) and name.strip()]
        return len(names)
    return None


def _list_running_docker_containers() -> list[str] | None:
    host_target = _normalize_mount_path(settings.host_root_target) if settings.host_root_target else ""
    candidate_roots: list[str] = []
    if host_target and host_target != "/":
        candidate_roots.append(os.path.join(host_target, "var/lib/docker/containers"))
    candidate_roots.append("/var/lib/docker/containers")

    container_name_pattern = re.compile(r"^[a-f0-9]{64}$")
    for root in candidate_roots:
        try:
            entries = os.listdir(root)
        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
            continue

        running: list[str] = []
        for entry in entries:
            if not container_name_pattern.match(entry):
                continue
            container_dir = os.path.join(root, entry)
            if not os.path.isdir(container_dir):
                continue
            config_path = os.path.join(container_dir, "config.v2.json")
            try:
                with open(config_path, "r", encoding="utf-8") as handle:
                    config = json.load(handle)
            except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
                continue

            state = config.get("State") if isinstance(config, dict) else None
            if not isinstance(state, dict) or not bool(state.get("Running")):
                continue

            name = config.get("Name")
            if isinstance(name, str):
                name = name.strip().lstrip("/")
            if not name:
                name = entry[:12]
            running.append(name)

        running.sort(key=str.lower)
        return running
    return None


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
    try:
        net_io = psutil.net_io_counters(pernic=True)
    except Exception:
        net_io = {}
    network_counters = [
        {"interface": name, "bytes_sent": stats.bytes_sent, "bytes_recv": stats.bytes_recv}
        for name, stats in net_io.items()
    ] if net_io else None

    disk_temps: list[dict[str, Any]] | None = None
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            disk_temps = []
            for name, entries in temps.items():
                for entry in entries:
                    label = entry.label or name
                    disk_temps.append({"device": label, "temperature_c": getattr(entry, "current", None)})
    except (AttributeError, NotImplementedError):
        disk_temps = None

    mount_points = _resolve_mount_points()
    payload: dict[str, Any] = {
        "reported_at": datetime.now(tz=timezone.utc).isoformat(),
        "hostname": socket.gethostname(),
        "monitor_version": settings.version,
        "cpu_temperature_c": cpu_temp,
        "ram_used_percent": round(virtual_memory.percent, 2),
        "total_ram_gb": round(virtual_memory.total / (1024 ** 3), 2),
        "memory_available_gb": round(virtual_memory.available / (1024 ** 3), 2),
        "swap_used_percent": round(float(swap.percent), 2) if swap and swap.percent is not None else None,
        "docker_container_count": _count_docker_containers(),
        "docker_running_containers": (
            _list_running_docker_containers() if settings.expose_docker_running_containers else None
        ),
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
    warnings = _detect_warnings(payload)
    if warnings:
        payload["warnings"] = warnings
    return payload


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
