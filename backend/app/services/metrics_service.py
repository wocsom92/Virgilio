from datetime import timezone
from typing import Sequence

from backend.app.models.monitors import MetricSnapshot
from backend.app.schemas.backend import BackendWithLatestSnapshot
from backend.app.schemas.common import MetricSnapshotRead
from backend.app.schemas.metrics import MetricSnapshotCreate


_MARKDOWN_SPECIAL_CHARS = set("_*[]()~`\\")


def _escape_markdown(text: str) -> str:
    """Escape Telegram Markdown control characters in dynamic content."""
    if not isinstance(text, str):
        text = str(text)
    return "".join(f"\\{char}" if char in _MARKDOWN_SPECIAL_CHARS else char for char in text)


def build_snapshot_model(backend_id: int, payload: MetricSnapshotCreate) -> MetricSnapshot:
    """Convert an incoming payload into a MetricSnapshot ORM instance."""
    snapshot = MetricSnapshot(
        backend_id=backend_id,
        reported_at=payload.reported_at.astimezone(timezone.utc),
        cpu_temperature_c=payload.cpu_temperature_c,
        ram_used_percent=payload.ram_used_percent,
        total_ram_gb=payload.total_ram_gb,
        memory_available_gb=payload.memory_available_gb,
        swap_used_percent=payload.swap_used_percent,
        docker_container_count=payload.docker_container_count,
        docker_running_containers=payload.docker_running_containers,
        disk_usage_percent=payload.disk_usage_percent,
        mounted_usage=[volume.model_dump() for volume in payload.mounted_usage] if payload.mounted_usage else None,
        cpu_load=payload.cpu_load.model_dump() if payload.cpu_load else None,
        network_counters=[counter.model_dump() for counter in payload.network_counters] if payload.network_counters else None,
        disk_temperatures=[temp.model_dump() for temp in payload.disk_temperatures] if payload.disk_temperatures else None,
        backend_version=payload.monitor_version,
        os_version=payload.os_version,
        uptime_seconds=payload.uptime_seconds,
        warnings=payload.warnings,
        raw_payload=payload.raw_payload or payload.model_dump(),
    )
    return snapshot


def _format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "unknown"
    minutes, sec = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    days, hour = divmod(hours, 24)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hour:
        parts.append(f"{hour}h")
    if minute:
        parts.append(f"{minute}m")
    parts.append(f"{sec}s")
    return " ".join(parts)


def _format_binary_size_from_gib(value_gib: float | None) -> str:
    if value_gib is None:
        return "unknown"
    if value_gib >= 1024:
        return f"{round(value_gib / 1024):.0f} TiB"
    if value_gib >= 1:
        return f"{round(value_gib):.0f} GiB"
    value_mib = value_gib * 1024
    if value_mib >= 1:
        return f"{round(value_mib):.0f} MiB"
    return f"{round(value_mib * 1024):.0f} KiB"


def _format_snapshot(snapshot: MetricSnapshotRead | None) -> str:
    if snapshot is None:
        return "_No recent data_"

    lines: list[str] = []
    if snapshot.cpu_temperature_c is not None:
        lines.append(f"• CPU temp: {snapshot.cpu_temperature_c:.1f} °C")
    if snapshot.ram_used_percent is not None:
        lines.append(f"• RAM: {snapshot.ram_used_percent:.1f}% used")
    if snapshot.memory_available_gb is not None:
        lines.append(f"• RAM available: {_format_binary_size_from_gib(snapshot.memory_available_gb)}")
    if snapshot.swap_used_percent is not None:
        lines.append(f"• Swap: {snapshot.swap_used_percent:.1f}% used")
    if snapshot.docker_container_count is not None:
        lines.append(f"• Docker containers: {snapshot.docker_container_count}")
    if snapshot.docker_running_containers:
        lines.append(f"• Running containers: {', '.join(_escape_markdown(name) for name in snapshot.docker_running_containers)}")
    if snapshot.disk_usage_percent is not None:
        lines.append(f"• Root disk: {snapshot.disk_usage_percent:.1f}% used")
    if snapshot.mounted_usage:
        for volume in snapshot.mounted_usage:
            point = _escape_markdown(volume.mount_point)
            percent = f"{volume.used_percent:.1f}%" if volume.used_percent is not None else "n/a"
            lines.append(f"• Mount {point}: {percent}")
    if snapshot.cpu_load:
        parts = []
        if snapshot.cpu_load.one is not None:
            parts.append(f"{snapshot.cpu_load.one:.2f}")
        if snapshot.cpu_load.five is not None:
            parts.append(f"{snapshot.cpu_load.five:.2f}")
        if snapshot.cpu_load.fifteen is not None:
            parts.append(f"{snapshot.cpu_load.fifteen:.2f}")
        if parts:
            lines.append(f"• Load avg: {', '.join(parts)}")
    if snapshot.monitor_version:
        lines.append(f"• Monitor: {_escape_markdown(snapshot.monitor_version)}")
    if snapshot.os_version:
        lines.append(f"• OS: {_escape_markdown(snapshot.os_version)}")
    if snapshot.uptime_seconds:
        lines.append(f"• Uptime: {_format_duration(snapshot.uptime_seconds)}")
    timestamp = snapshot.reported_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"_Reported at {timestamp}_")
    return "\n".join(lines)


def build_stats_message(backends: Sequence[BackendWithLatestSnapshot]) -> str:
    """Create a comprehensive /stats message."""
    lines: list[str] = ["*Server Monitor Stats*"]
    for backend in backends:
        lines.append(f"\n*{_escape_markdown(backend.name)}*")
        lines.append(_format_snapshot(backend.latest_snapshot))
    return "\n".join(lines)


def build_warn_message(backends: Sequence[BackendWithLatestSnapshot]) -> str:
    """Create the warning-only /warn message."""
    lines: list[str] = ["*Server Monitor Warnings*"]
    any_warning = False
    for backend in backends:
        snapshot = backend.latest_snapshot
        warnings = snapshot.warnings if snapshot else None
        if warnings:
            any_warning = True
            lines.append(f"\n*{_escape_markdown(backend.name)}*")
            for warning in warnings:
                lines.append(f"⚠️ {_escape_markdown(warning)}")
    if not any_warning:
        lines.append("\nAll systems nominal ✅")
    return "\n".join(lines)
