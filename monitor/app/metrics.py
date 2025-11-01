from __future__ import annotations

import asyncio
import logging
import os
import platform
import socket
import time
from datetime import datetime, timezone
from typing import Any
import subprocess
import shlex
import shutil

import psutil

from monitor.app.config import settings


CPU_TEMP_WARN = 80.0
RAM_WARN_PERCENT = 90.0
DISK_WARN_PERCENT = 90.0

logger = logging.getLogger(__name__)


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
        "backend_version": settings.version,
        "cpu_temperature_c": cpu_temp,
        "ram_used_percent": round(virtual_memory.percent, 2),
        "total_ram_gb": round(virtual_memory.total / (1024 ** 3), 2),
        "disk_usage_percent": round(disk.percent, 2),
        "mounted_usage": _mounted_usage(mount_points),
        "configured_mounts": mount_points,
        "cpu_load": {"one": load_one, "five": load_five, "fifteen": load_fifteen},
        "network_counters": network_counters,
        "disk_temperatures": disk_temps,
        "os_version": platform.platform(),
        "uptime_seconds": uptime_seconds,
    }
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

        result = await asyncio.to_thread(_run)
        if result.returncode in (0, None):
            return
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        output = stderr or stdout or str(result.returncode)
        errors.append(f\"{' '.join(attempt_args)}: {output}\")

    if _try_sysrq_reboot():
        return

    detail = \"; \".join(errors) if errors else \"No executable reboot command found.\"
    raise RuntimeError(f\"Reboot command failed. Attempts: {len(candidates)}. Details: {detail}\")


def _try_sysrq_reboot() -> bool:
    """Fallback reboot mechanism using sysrq trigger (host must allow it)."""
    try:
        with open("/proc/sysrq-trigger", "w") as handle:
            handle.write("b")
        return True
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Sysrq reboot trigger failed: %s", exc)
        return False
