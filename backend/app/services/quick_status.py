from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable
import asyncio

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.config import settings
from backend.app.models.monitors import MetricSnapshot, QuickStatusItem
from backend.app.schemas.quick_status import (
    QuickStatusItemCreate,
    QuickStatusTileRead,
    is_supported_quick_status_metric,
)
from backend.app.services.monitor_client import MonitorClientError, fetch_ping


_PERCENT_METRICS = {"disk_usage_percent", "ram_used_percent", "swap_used_percent", "mount_used_percent"}
_REVERSE_THRESHOLD_METRICS = {"last_restart", "memory_available_gb", "mount_available_gb", "ssh_last_unsuccessful_attempt"}
_PING_METRICS = {"ping_result", "ping_delay_ms"}
_INFO_ONLY_METRICS = {"swap_used_percent"}
_ALERT_STATUSES = {"warn", "critical"}


@dataclass(slots=True)
class PingCheckResult:
    checked_at: datetime
    success: bool
    latency_ms: float | None


@dataclass(slots=True)
class QuickStatusTransition:
    item_id: int
    backend_id: int
    backend_name: str
    label: str
    metric_key: str
    previous_status: str
    current_status: str
    display_value: str


@dataclass(slots=True)
class QuickStatusEvaluation:
    value: float | None
    display_value: str
    status: str


_PING_CACHE: dict[int, PingCheckResult] = {}
_PING_LOCK = asyncio.Lock()


def _quick_status_item_sort_key(item: QuickStatusItem) -> tuple[int, str, int, int, int]:
    backend = getattr(item, "backend", None)
    backend_order = backend.display_order if backend is not None else 0
    backend_name = backend.name if backend is not None else ""
    return (
        backend_order,
        backend_name.casefold(),
        item.backend_id,
        item.display_order,
        item.id,
    )


def _extract_mount_used_percent(snapshot: MetricSnapshot, mount_path: str | None) -> float | None:
    if not mount_path:
        return None
    mounts = snapshot.mounted_usage or []
    if not isinstance(mounts, list):
        return None
    for entry in mounts:
        if isinstance(entry, dict) and entry.get("mount_point") == mount_path:
            value = entry.get("used_percent")
            return float(value) if isinstance(value, (int, float)) else None
    return None


def _extract_mount_available_gb(snapshot: MetricSnapshot, mount_path: str | None) -> float | None:
    if not mount_path:
        return None
    mounts = snapshot.mounted_usage or []
    if not isinstance(mounts, list):
        mounts = []
    for entry in mounts:
        if not isinstance(entry, dict) or entry.get("mount_point") != mount_path:
            continue
        total_gb = entry.get("total_gb")
        used_percent = entry.get("used_percent")
        if not isinstance(total_gb, (int, float)) or not isinstance(used_percent, (int, float)):
            return None
        free_gb = float(total_gb) * max(0.0, min(100.0, 100.0 - float(used_percent))) / 100.0
        return free_gb
    if mount_path == "/":
        return _extract_raw_payload_value(snapshot, "disk_available_gb")
    return None


def _extract_cpu_load_one(snapshot: MetricSnapshot) -> float | None:
    payload = snapshot.cpu_load or {}
    if isinstance(payload, dict):
        value = payload.get("one")
        return float(value) if isinstance(value, (int, float)) else None
    return None


def _extract_cpu_load_five(snapshot: MetricSnapshot) -> float | None:
    payload = snapshot.cpu_load or {}
    if isinstance(payload, dict):
        value = payload.get("five")
        return float(value) if isinstance(value, (int, float)) else None
    return None


def _extract_cpu_load_fifteen(snapshot: MetricSnapshot) -> float | None:
    payload = snapshot.cpu_load or {}
    if isinstance(payload, dict):
        value = payload.get("fifteen")
        return float(value) if isinstance(value, (int, float)) else None
    return None


def _extract_uptime_hours(snapshot: MetricSnapshot) -> float | None:
    if snapshot.uptime_seconds is None:
        return None
    return float(snapshot.uptime_seconds) / 3600


def _extract_running_container_count(snapshot: MetricSnapshot) -> float | None:
    if snapshot.docker_container_count is not None:
        return float(snapshot.docker_container_count)
    containers = snapshot.docker_running_containers
    if not isinstance(containers, list):
        return None
    names = [name for name in containers if isinstance(name, str) and name.strip()]
    return float(len(names))


def _extract_raw_payload_value(snapshot: MetricSnapshot, key: str) -> float | None:
    payload = snapshot.raw_payload if isinstance(snapshot.raw_payload, dict) else {}
    raw = payload.get(key)
    return float(raw) if isinstance(raw, (int, float)) else None


def _metric_value(snapshot: MetricSnapshot, metric_key: str, mount_path: str | None) -> float | None:
    if metric_key == "disk_usage_percent":
        return float(snapshot.disk_usage_percent) if snapshot.disk_usage_percent is not None else None
    if metric_key == "ram_used_percent":
        return float(snapshot.ram_used_percent) if snapshot.ram_used_percent is not None else None
    if metric_key == "cpu_temperature_c":
        return float(snapshot.cpu_temperature_c) if snapshot.cpu_temperature_c is not None else None
    if metric_key == "memory_available_gb":
        return float(snapshot.memory_available_gb) if snapshot.memory_available_gb is not None else None
    if metric_key == "swap_used_percent":
        return float(snapshot.swap_used_percent) if snapshot.swap_used_percent is not None else None
    if metric_key == "docker_container_count":
        return _extract_running_container_count(snapshot)
    if metric_key == "cpu_load_one":
        return _extract_cpu_load_one(snapshot)
    if metric_key == "cpu_load_five":
        return _extract_cpu_load_five(snapshot)
    if metric_key == "cpu_load_fifteen":
        return _extract_cpu_load_fifteen(snapshot)
    if metric_key == "mount_used_percent":
        return _extract_mount_used_percent(snapshot, mount_path)
    if metric_key == "mount_available_gb":
        return _extract_mount_available_gb(snapshot, mount_path)
    if metric_key == "last_restart":
        return _extract_uptime_hours(snapshot)
    if metric_key == "ssh_last_successful_login":
        return _extract_raw_payload_value(snapshot, "ssh_last_successful_login_seconds")
    if metric_key == "ssh_last_unsuccessful_attempt":
        seconds = _extract_raw_payload_value(snapshot, "ssh_last_unsuccessful_attempt_seconds")
        return (seconds / 3600) if seconds is not None else None
    if metric_key == "ssh_status":
        return _extract_raw_payload_value(snapshot, "ssh_status_level")
    return None


def _format_value(metric_key: str, value: float | None) -> str:
    if value is None:
        return "—"
    if metric_key in _PERCENT_METRICS:
        return f"{value:.0f}%"
    if metric_key == "cpu_temperature_c":
        return f"{value:.1f} °C"
    if metric_key in {"memory_available_gb", "mount_available_gb"}:
        return _format_binary_size_from_gib(value)
    if metric_key == "docker_container_count":
        return f"{int(round(value))}"
    if metric_key == "last_restart":
        return _format_uptime_hours(value)
    if metric_key == "ping_delay_ms":
        return f"{value:.0f}ms"
    if metric_key in {"ssh_last_successful_login", "ssh_last_unsuccessful_attempt"}:
        hours = value / 3600 if metric_key == "ssh_last_successful_login" else value
        return _format_uptime_hours(hours)
    if metric_key == "ssh_status":
        level = int(round(value))
        if level <= 0:
            return "OK"
        if level == 1:
            return "WARN"
        return "CRIT"
    return f"{value:.2f}"


def _format_binary_size_from_gib(value_gib: float) -> str:
    if value_gib >= 1024:
        return f"{round(value_gib / 1024):.0f} TiB"
    if value_gib >= 1:
        return f"{round(value_gib):.0f} GiB"
    value_mib = value_gib * 1024
    if value_mib >= 1:
        return f"{round(value_mib):.0f} MiB"
    return f"{round(value_mib * 1024):.0f} KiB"


def _build_detail_lines(item: QuickStatusItem, snapshot: MetricSnapshot | None) -> list[str] | None:
    if snapshot is None or item.metric_key != "ssh_status":
        return None
    payload = snapshot.raw_payload if isinstance(snapshot.raw_payload, dict) else {}
    pubkey_enabled = payload.get("ssh_pubkey_auth_enabled")
    root_password_disabled = payload.get("ssh_root_password_login_disabled")
    password_auth_disabled = payload.get("ssh_password_auth_disabled")
    kbd_interactive_disabled = payload.get("ssh_kbd_interactive_auth_disabled")
    permit_root_mode = payload.get("ssh_permit_root_login_mode")

    def _flag(value: object, ok_text: str, fix_text: str) -> str:
        if value is True:
            return ok_text
        return fix_text

    lines = [
        _flag(pubkey_enabled, "Public key authentication is enabled.", "Set `PubkeyAuthentication yes`."),
        _flag(
            password_auth_disabled,
            "Password authentication is disabled.",
            "Set `PasswordAuthentication no`.",
        ),
        _flag(
            kbd_interactive_disabled,
            "Keyboard-interactive authentication is disabled.",
            "Set `KbdInteractiveAuthentication no` (or `ChallengeResponseAuthentication no`).",
        ),
        _flag(
            root_password_disabled,
            "Root password login is blocked.",
            "Set `PermitRootLogin prohibit-password` or `PermitRootLogin no`.",
        ),
    ]
    if isinstance(permit_root_mode, str) and permit_root_mode:
        lines.append(f"Current `PermitRootLogin`: `{permit_root_mode}`.")
    return lines


def _format_uptime_hours(value: float) -> str:
    total_minutes = int(round(value * 60))
    days, rem_minutes = divmod(total_minutes, 1440)
    hours, minutes = divmod(rem_minutes, 60)
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return f"{minutes}m"


def _format_elapsed_seconds(value: float) -> str:
    total = max(0, int(round(value)))
    days, rem = divmod(total, 86_400)
    hours, rem = divmod(rem, 3_600)
    minutes, seconds = divmod(rem, 60)
    return f"{days:02d}:{hours:02d}:{minutes:02d}:{seconds:02d}"


def _resolve_status(value: float | None, warning_threshold: float, critical_threshold: float, metric_key: str) -> str:
    if value is None:
        return "unknown"
    if metric_key in _INFO_ONLY_METRICS:
        return "info"
    if metric_key == "docker_container_count":
        min_allowed = min(warning_threshold, critical_threshold)
        max_allowed = max(warning_threshold, critical_threshold)
        if value < min_allowed or value > max_allowed:
            return "critical"
        return "ok"
    if metric_key == "ssh_last_successful_login":
        return "ok"
    if metric_key == "ssh_status":
        if value >= 2:
            return "critical"
        if value >= 1:
            return "warn"
        return "ok"
    if metric_key in _REVERSE_THRESHOLD_METRICS:
        if value <= critical_threshold:
            return "critical"
        if value <= warning_threshold:
            return "warn"
        return "ok"
    if value >= critical_threshold:
        return "critical"
    if value >= warning_threshold:
        return "warn"
    return "ok"


def evaluate_quick_status_item(item: QuickStatusItem, snapshot: MetricSnapshot | None) -> QuickStatusEvaluation:
    if snapshot is None:
        return QuickStatusEvaluation(value=None, display_value="—", status="unknown")
    value = _metric_value(snapshot, item.metric_key, item.mount_path)
    return QuickStatusEvaluation(
        value=value,
        display_value=_format_value(item.metric_key, value),
        status=_resolve_status(value, item.warning_threshold, item.critical_threshold, item.metric_key),
    )


async def _check_ping(item: QuickStatusItem) -> PingCheckResult | None:
    if not item.ping_endpoint:
        return None
    backend = getattr(item, "backend", None)
    if backend is None:
        return None
    interval = max(5, int(item.ping_interval_seconds or 60))
    now = datetime.now(tz=timezone.utc)
    async with _PING_LOCK:
        cached = _PING_CACHE.get(item.id)
        if cached and now - cached.checked_at < timedelta(seconds=interval):
            return cached
    timeout_seconds = max(1, int(settings.monitor_request_timeout_seconds or 1))
    timeout_seconds = min(30, timeout_seconds)
    try:
        payload = await fetch_ping(
            backend.base_url,
            backend.api_token,
            item.ping_endpoint,
            timeout_seconds,
        )
    except (MonitorClientError, Exception):
        payload = None

    if payload is None:
        success = False
        latency_ms = None
    else:
        success = bool(payload.get("success"))
        latency = payload.get("latency_ms")
        latency_ms = float(latency) if isinstance(latency, (int, float)) else None
    result = PingCheckResult(checked_at=now, success=success, latency_ms=latency_ms)
    async with _PING_LOCK:
        _PING_CACHE[item.id] = result
    return result


async def list_quick_status_items(session: AsyncSession) -> list[QuickStatusItem]:
    result = await session.execute(
        select(QuickStatusItem)
        .options(selectinload(QuickStatusItem.backend))
        .order_by(QuickStatusItem.display_order, QuickStatusItem.id)
    )
    return [item for item in result.scalars() if is_supported_quick_status_metric(getattr(item, "metric_key", None))]


async def list_quick_status_items_for_backend(session: AsyncSession, backend_id: int) -> list[QuickStatusItem]:
    result = await session.execute(
        select(QuickStatusItem)
        .where(QuickStatusItem.backend_id == backend_id)
        .order_by(QuickStatusItem.display_order, QuickStatusItem.id)
    )
    return [item for item in result.scalars() if is_supported_quick_status_metric(getattr(item, "metric_key", None))]


async def build_quick_status_tiles(
    session: AsyncSession,
    items: Iterable[QuickStatusItem],
) -> list[QuickStatusTileRead]:
    items_list = sorted(
        [item for item in items if is_supported_quick_status_metric(getattr(item, "metric_key", None))],
        key=_quick_status_item_sort_key,
    )
    if not items_list:
        return []

    backend_ids = {item.backend_id for item in items_list}
    latest_snapshot_sq = (
        select(
            MetricSnapshot.backend_id.label("backend_id"),
            func.max(MetricSnapshot.id).label("snapshot_id"),
        )
        .where(MetricSnapshot.backend_id.in_(backend_ids))
        .group_by(MetricSnapshot.backend_id)
        .subquery()
    )
    result = await session.execute(
        select(MetricSnapshot).join(
            latest_snapshot_sq,
            MetricSnapshot.id == latest_snapshot_sq.c.snapshot_id,
        )
    )
    snapshots = {snap.backend_id: snap for snap in result.scalars()}

    tiles: list[QuickStatusTileRead] = []
    for item in items_list:
        backend = getattr(item, "backend", None)
        snapshot = snapshots.get(item.backend_id)
        ping_result = await _check_ping(item) if item.metric_key in _PING_METRICS else None
        value = _metric_value(snapshot, item.metric_key, item.mount_path) if snapshot else None
        status = _resolve_status(value, item.warning_threshold, item.critical_threshold, item.metric_key)
        display_value = _format_value(item.metric_key, value)
        reported_at = snapshot.reported_at if snapshot else None
        if item.metric_key in _PING_METRICS:
            if ping_result is None:
                status = "unknown"
                display_value = "—"
                value = None
                reported_at = None
            else:
                reported_at = ping_result.checked_at
                if item.metric_key == "ping_result":
                    status = "ok" if ping_result.success else "critical"
                    display_value = "OK" if ping_result.success else "NOK"
                    value = 1.0 if ping_result.success else 0.0
                else:
                    if ping_result.success and ping_result.latency_ms is not None:
                        value = ping_result.latency_ms
                        display_value = _format_value(item.metric_key, value)
                        status = _resolve_status(value, item.warning_threshold, item.critical_threshold, item.metric_key)
                    else:
                        value = None
                        display_value = "timeout"
                        status = "critical"
        tiles.append(
            QuickStatusTileRead(
                id=item.id,
                backend_id=item.backend_id,
                backend_display_order=backend.display_order if backend else 0,
                backend_name=backend.name if backend else "Unknown",
                label=item.label,
                metric_key=item.metric_key,
                value=value,
                display_value=display_value,
                status=status,
                reported_at=reported_at,
                details=_build_detail_lines(item, snapshot),
            )
        )
    return tiles


def detect_quick_status_transitions_for_snapshot(
    items: Iterable[QuickStatusItem],
    previous_snapshot: MetricSnapshot | None,
    current_snapshot: MetricSnapshot,
    backend_name: str,
) -> list[QuickStatusTransition]:
    transitions: list[QuickStatusTransition] = []
    for item in items:
        if not is_supported_quick_status_metric(getattr(item, "metric_key", None)):
            continue
        if item.metric_key in _PING_METRICS:
            continue
        previous_value = _metric_value(previous_snapshot, item.metric_key, item.mount_path) if previous_snapshot else None
        previous_status = _resolve_status(
            previous_value,
            item.warning_threshold,
            item.critical_threshold,
            item.metric_key,
        )
        current_value = _metric_value(current_snapshot, item.metric_key, item.mount_path)
        current_status = _resolve_status(
            current_value,
            item.warning_threshold,
            item.critical_threshold,
            item.metric_key,
        )
        if current_status in _ALERT_STATUSES and current_status != previous_status:
            transitions.append(
                QuickStatusTransition(
                    item_id=item.id,
                    backend_id=item.backend_id,
                    backend_name=backend_name,
                    label=item.label,
                    metric_key=item.metric_key,
                    previous_status=previous_status,
                    current_status=current_status,
                    display_value=_format_value(item.metric_key, current_value),
                )
            )
    return transitions


async def create_quick_status_item(session: AsyncSession, payload: QuickStatusItemCreate) -> QuickStatusItem:
    item = QuickStatusItem(
        backend_id=payload.backend_id,
        label=payload.label,
        metric_key=payload.metric_key,
        mount_path=payload.mount_path,
        warning_threshold=payload.warning_threshold,
        critical_threshold=payload.critical_threshold,
        ping_endpoint=payload.ping_endpoint,
        ping_interval_seconds=payload.ping_interval_seconds,
        display_order=payload.display_order,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


async def update_quick_status_item(
    session: AsyncSession,
    item: QuickStatusItem,
    payload: QuickStatusItemCreate,
) -> QuickStatusItem:
    item.backend_id = payload.backend_id
    item.label = payload.label
    item.metric_key = payload.metric_key
    item.mount_path = payload.mount_path
    item.warning_threshold = payload.warning_threshold
    item.critical_threshold = payload.critical_threshold
    item.ping_endpoint = payload.ping_endpoint
    item.ping_interval_seconds = payload.ping_interval_seconds
    item.display_order = payload.display_order
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item
