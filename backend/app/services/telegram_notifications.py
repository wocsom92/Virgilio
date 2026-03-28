from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Sequence

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.models.monitors import MonitoredBackend, QuickStatusItem, TelegramSettings as TelegramSettingsModel
from backend.app.schemas.backend import BackendWithLatestSnapshot
from backend.app.schemas.common import MetricSnapshotRead
from backend.app.schemas.metrics import MetricSnapshotCreate
from backend.app.schemas.quick_status import QuickStatusTileRead
from backend.app.services.backend_queries import fetch_backends_with_latest_snapshots
from backend.app.services.metrics_service import build_stats_message
from backend.app.services.monitor_client import MonitorClientError, fetch_metrics
from backend.app.services.notification_center import record_notification_event
from backend.app.services.quick_status import (
    QuickStatusTransition,
    build_quick_status_tiles,
    list_quick_status_items,
)
from backend.app.services.telegram_settings import get_or_create_settings
from backend.app.services.telegram_service import TelegramError, send_message


logger = logging.getLogger(__name__)
_MARKDOWN_SPECIAL_CHARS = set("_*[]()~`\\")
_ALERT_STATUSES = {"warn", "critical"}
_PROCESS_ALERT_RESOURCE_BY_METRIC = {
    "cpu_load_one": "cpu",
    "cpu_load_five": "cpu",
    "cpu_load_fifteen": "cpu",
    "ram_used_percent": "memory",
    "memory_available_gb": "memory",
}


@dataclass(slots=True)
class QuickStatusNotificationEvent:
    item: QuickStatusItem
    backend_name: str
    label: str
    previous_status: str
    current_status: str
    display_value: str


def _escape_markdown(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    return "".join(f"\\{char}" if char in _MARKDOWN_SPECIAL_CHARS else char for char in text)


def _status_label(status: str) -> str:
    if status == "critical":
        return "error"
    if status == "warn":
        return "warning"
    if status in {"ok", "unknown", "info"}:
        return "normal"
    return status


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


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _cooldown_deadline(settings_model: TelegramSettingsModel, now: datetime) -> datetime:
    last_sent_at = _as_utc(settings_model.quick_status_last_notification_at)
    cooldown = max(0, int(settings_model.notification_cooldown_minutes or 0))
    if last_sent_at is None or cooldown == 0:
        return now
    return last_sent_at + timedelta(minutes=cooldown)


def _transition_requires_notification(previous_status: str | None, current_status: str) -> bool:
    if previous_status == current_status:
        return False
    return current_status in _ALERT_STATUSES or previous_status in _ALERT_STATUSES


async def fetch_backends_with_latest(
    session: AsyncSession,
    *,
    backend_id: int | None = None,
    backend_ids: Sequence[int] | None = None,
    backend_name: str | None = None,
) -> list[BackendWithLatestSnapshot]:
    return await fetch_backends_with_latest_snapshots(
        session,
        backend_id=backend_id,
        backend_ids=backend_ids,
        backend_name=backend_name,
        require_admin_ordering=True,
    )


async def find_backend_for_telegram(session: AsyncSession, token: str) -> MonitoredBackend | None:
    normalized = (token or "").strip()
    if not normalized:
        return None
    if normalized.isdigit():
        return await session.get(MonitoredBackend, int(normalized))
    result = await session.execute(
        select(MonitoredBackend).where(MonitoredBackend.name.ilike(normalized))
    )
    return result.scalars().first()


async def resolve_message_context(
    session: AsyncSession,
    chat_id: str | None,
    strict: bool,
) -> tuple[TelegramSettingsModel | None, str | None]:
    settings_model = await get_or_create_settings(session)
    if not settings_model.is_active:
        if strict:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Telegram integration disabled")
        return None, None
    if not settings_model.bot_token:
        if strict:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Telegram settings incomplete")
        return None, None

    target_chat = chat_id or settings_model.default_chat_id
    if not target_chat:
        if strict:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No chat configured")
        return None, None

    return settings_model, str(target_chat)


async def send_compiled_message(
    session: AsyncSession,
    builder: Callable[[Sequence[BackendWithLatestSnapshot]], str],
    chat_id: str | None = None,
    backend_id: int | None = None,
    backend_name: str | None = None,
) -> str:
    settings_model, target_chat = await resolve_message_context(session, chat_id, strict=True)
    backends = await fetch_backends_with_latest(session, backend_id=backend_id, backend_name=backend_name)
    if backend_id is not None or backend_name:
        if not backends:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backend not found")
        # Only the first match is needed when filtering by name to keep messages concise.
        backends = backends[:1]
    text = builder(backends)
    try:
        await _send_tracked_telegram_message(
            session,
            bot_token=settings_model.bot_token,
            target_chat=target_chat,
            text=text,
            category="compiled_message",
            severity="info",
            title="Telegram message",
        )
    except TelegramError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return text


async def _load_quick_status_tiles(session: AsyncSession) -> list[QuickStatusTileRead]:
    items = await list_quick_status_items(session)
    return await build_quick_status_tiles(session, items)


def _build_warn_message_from_tiles(
    tiles: Sequence[QuickStatusTileRead],
    snapshots_by_backend_name: dict[str, MetricSnapshotRead | None] | None = None,
) -> str:
    lines: list[str] = ["*Server Monitor Warnings*"]
    grouped = _group_alert_tiles_by_backend(tiles)
    if not grouped:
        lines.append("\nAll systems nominal ✅")
        return "\n".join(lines)

    for backend_name in sorted(grouped):
        lines.append(f"\n*{_escape_markdown(backend_name)}*")
        resource_kinds: set[str] = set()
        for tile in grouped[backend_name]:
            lines.append(_format_alert_tile_line(tile))
            resource_kind = _resource_kind_for_metric(tile.metric_key)
            if resource_kind:
                resource_kinds.add(resource_kind)
        snapshot = snapshots_by_backend_name.get(backend_name) if snapshots_by_backend_name else None
        lines.extend(_build_top_process_lines(snapshot, resource_kinds))
    return "\n".join(lines)


def _group_alert_tiles_by_backend(tiles: Sequence[QuickStatusTileRead]) -> dict[str, list[QuickStatusTileRead]]:
    grouped: dict[str, list[QuickStatusTileRead]] = {}
    for tile in tiles:
        if tile.status not in _ALERT_STATUSES:
            continue
        grouped.setdefault(tile.backend_name, []).append(tile)
    return grouped


def _format_alert_tile_line(tile: QuickStatusTileRead) -> str:
    icon = "🚨" if tile.status == "critical" else "⚠️"
    return (
        f"{icon} {_escape_markdown(tile.label)} "
        f"({_escape_markdown(_status_label(tile.status))}) "
        f"at {_escape_markdown(tile.display_value)}"
    )


def _resource_kind_for_metric(metric_key: str | None) -> str | None:
    if not metric_key:
        return None
    return _PROCESS_ALERT_RESOURCE_BY_METRIC.get(metric_key)


def _extract_top_processes(snapshot: MetricSnapshotRead | None, resource_kind: str) -> list[dict]:
    if snapshot is None or not isinstance(snapshot.raw_payload, dict):
        return []
    raw_payload = snapshot.raw_payload
    top_processes = raw_payload.get("top_processes")
    if not isinstance(top_processes, dict):
        return []
    entries = top_processes.get(resource_kind)
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)][:10]


def _extract_top_processes_from_payload(payload: dict[str, Any], resource_kind: str) -> list[dict[str, Any]]:
    top_processes = payload.get("top_processes")
    if not isinstance(top_processes, dict):
        return []
    entries = top_processes.get(resource_kind)
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)][:10]


def _format_top_process_line(entry: dict) -> str:
    name = _escape_markdown(str(entry.get("name") or "unknown"))
    pid = entry.get("pid")
    cpu_percent = entry.get("cpu_percent")
    memory_percent = entry.get("memory_percent")
    details: list[str] = []
    if isinstance(cpu_percent, (int, float)):
        details.append(f"CPU {cpu_percent:.1f}%")
    if isinstance(memory_percent, (int, float)):
        details.append(f"MEM {memory_percent:.1f}%")
    details_text = _escape_markdown(", ".join(details) or "usage unavailable")
    pid_text = f"pid {pid}" if isinstance(pid, int) else "pid ?"
    return f"• {name} ({_escape_markdown(pid_text)}): {details_text}"


def _build_top_process_lines(snapshot: MetricSnapshotRead | None, resource_kinds: set[str]) -> list[str]:
    lines: list[str] = []
    for resource_kind in ("cpu", "memory"):
        if resource_kind not in resource_kinds:
            continue
        entries = _extract_top_processes(snapshot, resource_kind)
        if not entries:
            continue
        resource_label = "CPU" if resource_kind == "cpu" else "Memory"
        lines.append(f"*Top {resource_label} Processes:*")
        lines.extend(_format_top_process_line(entry) for entry in entries)
    return lines


def _build_ssh_failure_lines(snapshot: MetricSnapshotRead | None) -> list[str]:
    if snapshot is None or not isinstance(snapshot.raw_payload, dict):
        return []
    raw_payload = snapshot.raw_payload
    method = raw_payload.get("ssh_last_failure_auth_method")
    username = raw_payload.get("ssh_last_failure_username")
    source_ip = raw_payload.get("ssh_last_failure_source_ip")
    port = raw_payload.get("ssh_last_failure_port")
    raw_line = raw_payload.get("ssh_last_failure_line")

    if not any(value not in (None, "") for value in (method, username, source_ip, port, raw_line)):
        return []

    lines = ["*SSH Failure Details:*"]
    if isinstance(method, str) and method.strip():
        lines.append(f"• Method: {_escape_markdown(method.strip())}")
    if isinstance(username, str) and username.strip():
        lines.append(f"• User: {_escape_markdown(username.strip())}")
    if isinstance(source_ip, str) and source_ip.strip():
        lines.append(f"• Source: {_escape_markdown(source_ip.strip())}")
    if isinstance(port, int):
        lines.append(f"• Port: {_escape_markdown(str(port))}")
    if isinstance(raw_line, str) and raw_line.strip():
        lines.append(f"• Log: {_escape_markdown(raw_line.strip())}")
    return lines


def _format_top_process_lines_from_payload(payload: dict[str, Any], resource_kind: str) -> list[str]:
    entries = _extract_top_processes_from_payload(payload, resource_kind)
    if not entries:
        return []
    resource_label = "CPU" if resource_kind == "cpu" else "Memory"
    return [f"*Top {resource_label} Processes:*", *(_format_top_process_line(entry) for entry in entries)]


def _build_cpu_message(backend_name: str, payload: MetricSnapshotCreate) -> str:
    lines = [f"*CPU Usage: {_escape_markdown(backend_name)}*"]
    load = payload.cpu_load
    load_parts: list[str] = []
    if load and load.one is not None:
        load_parts.append(f"1m {load.one:.2f}")
    if load and load.five is not None:
        load_parts.append(f"5m {load.five:.2f}")
    if load and load.fifteen is not None:
        load_parts.append(f"15m {load.fifteen:.2f}")
    if load_parts:
        lines.append(f"• Load avg: {_escape_markdown(', '.join(load_parts))}")
    if payload.cpu_temperature_c is not None:
        lines.append(f"• CPU temp: {payload.cpu_temperature_c:.1f} °C")
    timestamp = payload.reported_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines.append(f"_Reported at {timestamp}_")
    lines.extend(_format_top_process_lines_from_payload(payload.raw_payload or {}, "cpu"))
    return "\n".join(lines)


def _build_memory_message(backend_name: str, payload: MetricSnapshotCreate) -> str:
    lines = [f"*Memory Usage: {_escape_markdown(backend_name)}*"]
    if payload.ram_used_percent is not None:
        lines.append(f"• RAM used: {payload.ram_used_percent:.1f}%")
    if payload.total_ram_gb is not None:
        lines.append(f"• Total RAM: {_format_binary_size_from_gib(payload.total_ram_gb)}")
    if payload.memory_available_gb is not None:
        lines.append(f"• RAM available: {_format_binary_size_from_gib(payload.memory_available_gb)}")
    if payload.swap_used_percent is not None:
        lines.append(f"• Swap used: {payload.swap_used_percent:.1f}%")
    timestamp = payload.reported_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines.append(f"_Reported at {timestamp}_")
    lines.extend(_format_top_process_lines_from_payload(payload.raw_payload or {}, "memory"))
    return "\n".join(lines)


async def _send_tracked_telegram_message(
    session: AsyncSession,
    *,
    bot_token: str,
    target_chat: str,
    text: str,
    category: str,
    severity: str,
    title: str,
    backend_id: int | None = None,
    backend_name: str | None = None,
) -> None:
    try:
        await send_message(bot_token, target_chat, text)
    except TelegramError as exc:
        await record_notification_event(
            session,
            category=category,
            severity=severity,
            title=title,
            body=text,
            backend_id=backend_id,
            backend_name=backend_name,
            delivery_status="failed",
            target=target_chat,
            error_message=str(exc),
        )
        raise

    await record_notification_event(
        session,
        category=category,
        severity=severity,
        title=title,
        body=text,
        backend_id=backend_id,
        backend_name=backend_name,
        delivery_status="sent",
        target=target_chat,
    )


def _build_stats_message_with_alerts(
    backends: Sequence[BackendWithLatestSnapshot],
    tiles: Sequence[QuickStatusTileRead],
) -> str:
    base = build_stats_message(backends)
    grouped = _group_alert_tiles_by_backend(tiles)
    if not grouped:
        return base

    lines = base.split("\n")
    for backend in backends:
        backend_lines: list[str] = []
        resource_kinds: set[str] = set()
        for tile in grouped.get(backend.name, []):
            backend_lines.append(_format_alert_tile_line(tile))
            resource_kind = _resource_kind_for_metric(tile.metric_key)
            if resource_kind:
                resource_kinds.add(resource_kind)
        backend_lines.extend(_build_top_process_lines(backend.latest_snapshot, resource_kinds))
        if not backend_lines:
            continue
        marker = f"\n*{_escape_markdown(backend.name)}*"
        try:
            index = lines.index(marker)
        except ValueError:
            continue
        insert_at = index + 2 if index + 1 < len(lines) else len(lines)
        lines[insert_at:insert_at] = ["", "*Warnings / Errors:*", *backend_lines]
    return "\n".join(lines)


def _build_transition_message(
    transitions: Sequence[QuickStatusTransition],
    snapshots_by_backend_name: dict[str, MetricSnapshotRead | None] | None = None,
) -> str:
    lines: list[str] = ["*Server Monitor Alert*"]
    grouped: dict[str, list[QuickStatusTransition]] = {}
    for transition in transitions:
        grouped.setdefault(transition.backend_name, []).append(transition)

    for backend_name, backend_transitions in grouped.items():
        lines.append(f"\n*{_escape_markdown(backend_name)}*")
        resource_kinds: set[str] = set()
        include_ssh_failure_details = False
        for transition in backend_transitions:
            if transition.current_status in _ALERT_STATUSES:
                icon = "🚨" if transition.current_status == "critical" else "⚠️"
                lines.append(
                    f"{icon} {_escape_markdown(transition.label)} changed "
                    f"from {_escape_markdown(_status_label(transition.previous_status))} "
                    f"to {_escape_markdown(_status_label(transition.current_status))} "
                    f"({_escape_markdown(transition.display_value)})"
                )
                resource_kind = _resource_kind_for_metric(transition.metric_key)
                if resource_kind:
                    resource_kinds.add(resource_kind)
                if transition.metric_key == "ssh_last_unsuccessful_attempt":
                    include_ssh_failure_details = True
            else:
                lines.append(
                    f"✅ {_escape_markdown(transition.label)} cleared "
                    f"(was {_escape_markdown(_status_label(transition.previous_status))}, "
                    f"now {_escape_markdown(transition.display_value)})"
                )
        snapshot = snapshots_by_backend_name.get(backend_name) if snapshots_by_backend_name else None
        lines.extend(_build_top_process_lines(snapshot, resource_kinds))
        if include_ssh_failure_details:
            lines.extend(_build_ssh_failure_lines(snapshot))
    return "\n".join(lines)


async def _record_quick_status_inbox_event(
    session: AsyncSession,
    *,
    item: QuickStatusItem,
    backend_name: str,
    label: str,
    previous_status: str,
    current_status: str,
    display_value: str,
    detail_lines: list[QuickStatusDetailLine] | None = None,
) -> None:
    if current_status in _ALERT_STATUSES:
        severity = current_status
        title = f"Quick status alert: {backend_name}"
        body = (
            f"{label} changed from {_status_label(previous_status)} "
            f"to {_status_label(current_status)} ({display_value})"
        )
    else:
        severity = "info"
        title = f"Quick status cleared: {backend_name}"
        body = (
            f"{label} cleared "
            f"(was {_status_label(previous_status)}, now {display_value})"
        )
    if item.metric_key == "ssh_last_unsuccessful_attempt" and detail_lines:
        body = "\n".join([body, "", "SSH Failure Details:", *(line.text for line in detail_lines)])
    await record_notification_event(
        session,
        channel="local",
        category="quick_status",
        severity=severity,
        title=title,
        body=body,
        backend_id=item.backend_id,
        backend_name=backend_name,
        delivery_status="local",
    )


async def queue_quick_status_notifications(
    session: AsyncSession,
    items: Sequence[QuickStatusItem],
) -> None:
    if not items:
        return
    settings_model = await get_or_create_settings(session)
    now = datetime.now(tz=timezone.utc)
    batch_window_seconds = max(0, int(settings_model.notification_batch_window_seconds or 0))
    due_at = now + timedelta(seconds=batch_window_seconds)

    # Use the latest persisted snapshots to evaluate the current tile state after ingest.
    tiles = await build_quick_status_tiles(session, items)
    tiles_by_id = {tile.id: tile for tile in tiles}

    changed = False
    for item in items:
        if item.metric_key in {"ping_result", "ping_delay_ms"}:
            continue
        tile = tiles_by_id.get(item.id)
        if tile is None:
            continue
        current_status = tile.status
        previous_status = item.last_notified_status
        if not _transition_requires_notification(previous_status, current_status):
            if item.pending_notification_status is not None or item.pending_notification_due_at is not None:
                item.pending_notification_status = None
                item.pending_notification_due_at = None
                session.add(item)
                changed = True
            continue
        if item.pending_notification_status == current_status and item.pending_notification_due_at is not None:
            continue
        if item.pending_notification_due_at is not None:
            item.pending_notification_status = current_status
            await _record_quick_status_inbox_event(
                session,
                item=item,
                backend_name=tile.backend_name,
                label=tile.label,
                previous_status=previous_status or "unknown",
                current_status=current_status,
                display_value=tile.display_value,
                detail_lines=tile.details,
            )
        else:
            item.pending_notification_status = current_status
            item.pending_notification_due_at = due_at
            await _record_quick_status_inbox_event(
                session,
                item=item,
                backend_name=tile.backend_name,
                label=tile.label,
                previous_status=previous_status or "unknown",
                current_status=current_status,
                display_value=tile.display_value,
                detail_lines=tile.details,
            )
        session.add(item)
        changed = True

    if changed:
        await session.commit()


async def dispatch_due_quick_status_notifications(
    session: AsyncSession,
    *,
    chat_id: str | None = None,
) -> str | None:
    settings_model = await get_or_create_settings(session)
    now = datetime.now(tz=timezone.utc)
    if now < _cooldown_deadline(settings_model, now):
        return None

    due_result = await session.execute(
        select(QuickStatusItem.id)
        .where(
            QuickStatusItem.pending_notification_status.is_not(None),
            QuickStatusItem.pending_notification_due_at.is_not(None),
            QuickStatusItem.pending_notification_due_at <= now,
        )
        .limit(1)
    )
    if due_result.scalar_one_or_none() is None:
        return None

    result = await session.execute(
        select(QuickStatusItem)
        .options(selectinload(QuickStatusItem.backend))
        .where(
            QuickStatusItem.pending_notification_status.is_not(None),
            QuickStatusItem.pending_notification_due_at.is_not(None),
        )
        .order_by(QuickStatusItem.pending_notification_due_at, QuickStatusItem.display_order, QuickStatusItem.id)
    )
    items = list(result.scalars())

    tiles = await build_quick_status_tiles(session, items)
    tiles_by_id = {tile.id: tile for tile in tiles}
    events: list[QuickStatusNotificationEvent] = []
    changed = False
    for item in items:
        tile = tiles_by_id.get(item.id)
        if tile is None:
            item.pending_notification_status = None
            item.pending_notification_due_at = None
            session.add(item)
            changed = True
            continue
        current_status = tile.status
        previous_status = item.last_notified_status or "unknown"
        if not _transition_requires_notification(item.last_notified_status, current_status):
            item.pending_notification_status = None
            item.pending_notification_due_at = None
            session.add(item)
            changed = True
            continue
        if item.pending_notification_status != current_status:
            item.pending_notification_status = current_status
            item.pending_notification_due_at = now
            session.add(item)
            changed = True
        events.append(
            QuickStatusNotificationEvent(
                item=item,
                backend_name=tile.backend_name,
                label=tile.label,
                previous_status=previous_status,
                current_status=current_status,
                display_value=tile.display_value,
            )
        )

    if changed and not events:
        await session.commit()
        return None

    if not events:
        return None

    settings_model_ctx, target_chat = await resolve_message_context(session, chat_id, strict=False)
    if not settings_model_ctx or not target_chat:
        return None

    transitions = [
        QuickStatusTransition(
            item_id=event.item.id,
            backend_id=event.item.backend_id,
            backend_name=event.backend_name,
            label=event.label,
            metric_key=event.item.metric_key,
            previous_status=event.previous_status,
            current_status=event.current_status,
            display_value=event.display_value,
        )
        for event in events
    ]
    backends = await fetch_backends_with_latest(
        session,
        backend_ids=sorted({event.item.backend_id for event in events}),
    )
    snapshots_by_backend_name = {backend.name: backend.latest_snapshot for backend in backends}
    text = _build_transition_message(transitions, snapshots_by_backend_name)
    try:
        severity = "critical" if any(event.current_status == "critical" for event in events) else "warn"
        title = (
            f"Quick status alert: {events[0].backend_name}"
            if len(events) == 1
            else f"Quick status alerts ({len(events)})"
        )
        await _send_tracked_telegram_message(
            session,
            bot_token=settings_model_ctx.bot_token,
            target_chat=target_chat,
            text=text,
            category="quick_status",
            severity=severity,
            title=title,
            backend_id=events[0].item.backend_id if len(events) == 1 else None,
            backend_name=events[0].backend_name if len(events) == 1 else None,
        )
    except TelegramError as exc:
        logger.warning("Failed to send Telegram warning notification: %s", exc)
        return None

    sent_at = datetime.now(tz=timezone.utc)
    settings_model.quick_status_last_notification_at = sent_at
    session.add(settings_model)
    for event in events:
        event.item.last_notified_status = event.current_status
        event.item.pending_notification_status = None
        event.item.pending_notification_due_at = None
        session.add(event.item)
    await session.commit()
    return text


async def try_send_quick_status_transition_notification(
    session: AsyncSession,
    transitions: Sequence[QuickStatusTransition],
    chat_id: str | None = None,
) -> str | None:
    if not transitions:
        return None
    settings_model, target_chat = await resolve_message_context(session, chat_id, strict=False)
    if not settings_model or not target_chat:
        return None
    backends = await fetch_backends_with_latest(
        session,
        backend_ids=sorted({transition.backend_id for transition in transitions}),
    )
    snapshots_by_backend_name = {backend.name: backend.latest_snapshot for backend in backends}
    text = _build_transition_message(transitions, snapshots_by_backend_name)
    try:
        severity = "critical" if any(transition.current_status == "critical" for transition in transitions) else "warn"
        await _send_tracked_telegram_message(
            session,
            bot_token=settings_model.bot_token,
            target_chat=target_chat,
            text=text,
            category="quick_status",
            severity=severity,
            title="Quick status alert",
        )
    except TelegramError as exc:
        logger.warning("Failed to send Telegram warning notification: %s", exc)
        return None
    return text


async def send_stats_message(
    session: AsyncSession,
    chat_id: str | None = None,
    backend_id: int | None = None,
    backend_name: str | None = None,
) -> str:
    settings_model, target_chat = await resolve_message_context(session, chat_id, strict=True)
    backends = await fetch_backends_with_latest(session, backend_id=backend_id, backend_name=backend_name)
    if backend_id is not None or backend_name:
        if not backends:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backend not found")
        backends = backends[:1]
    tiles = await _load_quick_status_tiles(session)
    if backend_id is not None:
        tiles = [tile for tile in tiles if tile.backend_id == backend_id]
    elif backend_name:
        target_name = backends[0].name if backends else backend_name
        tiles = [tile for tile in tiles if tile.backend_name == target_name]
    text = _build_stats_message_with_alerts(backends, tiles)
    try:
        await _send_tracked_telegram_message(
            session,
            bot_token=settings_model.bot_token,
            target_chat=target_chat,
            text=text,
            category="stats",
            severity="info",
            title="Server stats",
            backend_id=backends[0].id if len(backends) == 1 else None,
            backend_name=backends[0].name if len(backends) == 1 else None,
        )
    except TelegramError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return text


async def send_warn_message(session: AsyncSession, chat_id: str | None = None) -> str:
    settings_model, target_chat = await resolve_message_context(session, chat_id, strict=True)
    tiles = await _load_quick_status_tiles(session)
    backends = await fetch_backends_with_latest(session)
    snapshots_by_backend_name = {backend.name: backend.latest_snapshot for backend in backends}
    text = _build_warn_message_from_tiles(tiles, snapshots_by_backend_name)
    try:
        await _send_tracked_telegram_message(
            session,
            bot_token=settings_model.bot_token,
            target_chat=target_chat,
            text=text,
            category="warnings",
            severity="warn",
            title="Server warnings",
        )
    except TelegramError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return text


async def _send_live_usage_message(
    session: AsyncSession,
    *,
    chat_id: str | None,
    backend_name: str | None,
    builder: Callable[[str, MetricSnapshotCreate], str],
) -> str:
    if not backend_name or not backend_name.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Usage: specify a server name")

    backend = await find_backend_for_telegram(session, backend_name.strip())
    if not backend:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backend not found")

    settings_model, target_chat = await resolve_message_context(session, chat_id, strict=True)
    try:
        data = await fetch_metrics(backend.base_url, backend.api_token)
    except MonitorClientError as exc:
        mapped_status = exc.status_code if isinstance(getattr(exc, "status_code", None), int) else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=mapped_status, detail=str(exc)) from exc

    metrics_payload = data.get("metrics") if isinstance(data, dict) else None
    if not isinstance(metrics_payload, dict):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Monitor payload missing 'metrics'")

    payload = MetricSnapshotCreate.model_validate({**metrics_payload, "raw_payload": metrics_payload})
    text = builder(backend.name, payload)
    try:
        category = "cpu_usage" if builder is _build_cpu_message else "memory_usage"
        title = f"{'CPU' if category == 'cpu_usage' else 'Memory'} usage: {backend.name}"
        await _send_tracked_telegram_message(
            session,
            bot_token=settings_model.bot_token,
            target_chat=target_chat,
            text=text,
            category=category,
            severity="info",
            title=title,
            backend_id=backend.id,
            backend_name=backend.name,
        )
    except TelegramError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return text


async def send_cpu_message(session: AsyncSession, chat_id: str | None = None, backend_name: str | None = None) -> str:
    return await _send_live_usage_message(
        session,
        chat_id=chat_id,
        backend_name=backend_name,
        builder=_build_cpu_message,
    )


async def send_memory_message(
    session: AsyncSession,
    chat_id: str | None = None,
    backend_name: str | None = None,
) -> str:
    return await _send_live_usage_message(
        session,
        chat_id=chat_id,
        backend_name=backend_name,
        builder=_build_memory_message,
    )


def _ssh_success_age_seconds_from_payload(payload: MetricSnapshotCreate) -> float | None:
    if not isinstance(payload.raw_payload, dict):
        return None
    value = payload.raw_payload.get("ssh_last_successful_login_seconds")
    return float(value) if isinstance(value, (int, float)) else None


def _build_ssh_success_lines(payload: MetricSnapshotCreate) -> list[str]:
    raw_payload = payload.raw_payload if isinstance(payload.raw_payload, dict) else {}
    method = raw_payload.get("ssh_last_successful_auth_method")
    username = raw_payload.get("ssh_last_successful_username")
    source_ip = raw_payload.get("ssh_last_successful_source_ip")
    port = raw_payload.get("ssh_last_successful_port")

    if not any(value not in (None, "") for value in (method, username, source_ip, port)):
        return []

    lines = ["*SSH Login Details:*"]
    if isinstance(method, str) and method.strip():
        lines.append(f"• Method: {_escape_markdown(method.strip())}")
    if isinstance(username, str) and username.strip():
        lines.append(f"• User: {_escape_markdown(username.strip())}")
    if isinstance(source_ip, str) and source_ip.strip():
        lines.append(f"• Source: {_escape_markdown(source_ip.strip())}")
    if isinstance(port, int):
        lines.append(f"• Port: {_escape_markdown(str(port))}")
    return lines


def _ssh_failure_age_seconds_from_payload(payload: MetricSnapshotCreate) -> float | None:
    if not isinstance(payload.raw_payload, dict):
        return None
    value = payload.raw_payload.get("ssh_last_unsuccessful_attempt_seconds")
    return float(value) if isinstance(value, (int, float)) else None


def _build_ssh_failure_lines_from_payload(payload: MetricSnapshotCreate) -> list[str]:
    raw_payload = payload.raw_payload if isinstance(payload.raw_payload, dict) else {}
    method = raw_payload.get("ssh_last_failure_auth_method")
    username = raw_payload.get("ssh_last_failure_username")
    source_ip = raw_payload.get("ssh_last_failure_source_ip")
    port = raw_payload.get("ssh_last_failure_port")
    raw_line = raw_payload.get("ssh_last_failure_line")

    if not any(value not in (None, "") for value in (method, username, source_ip, port, raw_line)):
        return []

    lines = ["*SSH Failure Details:*"]
    if isinstance(method, str) and method.strip():
        lines.append(f"• Method: {_escape_markdown(method.strip())}")
    if isinstance(username, str) and username.strip():
        lines.append(f"• User: {_escape_markdown(username.strip())}")
    if isinstance(source_ip, str) and source_ip.strip():
        lines.append(f"• Source: {_escape_markdown(source_ip.strip())}")
    if isinstance(port, int):
        lines.append(f"• Port: {_escape_markdown(str(port))}")
    if isinstance(raw_line, str) and raw_line.strip():
        lines.append(f"• Log: {_escape_markdown(raw_line.strip())}")
    return lines


def _ssh_success_age_seconds_from_snapshot(snapshot: Any) -> float | None:
    raw_payload = snapshot.raw_payload if snapshot is not None and isinstance(getattr(snapshot, "raw_payload", None), dict) else {}
    value = raw_payload.get("ssh_last_successful_login_seconds")
    return float(value) if isinstance(value, (int, float)) else None


def _ssh_failure_age_seconds_from_snapshot(snapshot: Any) -> float | None:
    raw_payload = snapshot.raw_payload if snapshot is not None and isinstance(getattr(snapshot, "raw_payload", None), dict) else {}
    value = raw_payload.get("ssh_last_unsuccessful_attempt_seconds")
    return float(value) if isinstance(value, (int, float)) else None


def _is_new_successful_ssh_login(previous_snapshot: Any, payload: MetricSnapshotCreate) -> bool:
    previous_age = _ssh_success_age_seconds_from_snapshot(previous_snapshot)
    current_age = _ssh_success_age_seconds_from_payload(payload)
    if previous_age is None or current_age is None:
        return False
    previous_reported_at = getattr(previous_snapshot, "reported_at", None)
    if previous_reported_at is None:
        return False
    elapsed_seconds = max(
        0.0,
        (payload.reported_at.astimezone(timezone.utc) - previous_reported_at.astimezone(timezone.utc)).total_seconds(),
    )
    tolerance_seconds = max(15.0, elapsed_seconds * 0.5)
    return current_age + tolerance_seconds < previous_age


def _is_new_unsuccessful_ssh_login(previous_snapshot: Any, payload: MetricSnapshotCreate) -> bool:
    previous_age = _ssh_failure_age_seconds_from_snapshot(previous_snapshot)
    current_age = _ssh_failure_age_seconds_from_payload(payload)
    if previous_age is None or current_age is None:
        return False
    previous_reported_at = getattr(previous_snapshot, "reported_at", None)
    if previous_reported_at is None:
        return False
    elapsed_seconds = max(
        0.0,
        (payload.reported_at.astimezone(timezone.utc) - previous_reported_at.astimezone(timezone.utc)).total_seconds(),
    )
    tolerance_seconds = max(15.0, elapsed_seconds * 0.5)
    return current_age + tolerance_seconds < previous_age


async def maybe_send_unsuccessful_ssh_login_notification(
    session: AsyncSession,
    *,
    backend: MonitoredBackend,
    previous_snapshot: Any,
    payload: MetricSnapshotCreate,
) -> str | None:
    if not _is_new_unsuccessful_ssh_login(previous_snapshot, payload):
        return None

    settings_model, target_chat = await resolve_message_context(session, None, strict=False)
    if not settings_model or not target_chat:
        return None

    failure_age_seconds = _ssh_failure_age_seconds_from_payload(payload)
    failure_timestamp = payload.reported_at.astimezone(timezone.utc)
    if failure_age_seconds is not None:
        failure_timestamp = failure_timestamp - timedelta(seconds=max(0.0, failure_age_seconds))
    failure_timestamp_text = failure_timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
    text = (
        f"*SSH Login Failure Detected*\n\n"
        f"*{_escape_markdown(backend.name)}*\n"
        f"🚨 Unsuccessful SSH login detected at {_escape_markdown(failure_timestamp_text)}."
    )
    extra_lines = _build_ssh_failure_lines_from_payload(payload)
    if extra_lines:
        text = "\n".join([text, *extra_lines])
    try:
        await _send_tracked_telegram_message(
            session,
            bot_token=settings_model.bot_token,
            target_chat=target_chat,
            text=text,
            category="ssh_login_failure",
            severity="warn",
            title=f"SSH login failure: {backend.name}",
            backend_id=backend.id,
            backend_name=backend.name,
        )
    except TelegramError as exc:
        logger.warning("Failed to send SSH login failure notification: %s", exc)
        return None
    return text


async def maybe_send_successful_ssh_login_notification(
    session: AsyncSession,
    *,
    backend: MonitoredBackend,
    previous_snapshot: Any,
    payload: MetricSnapshotCreate,
) -> str | None:
    if not _is_new_successful_ssh_login(previous_snapshot, payload):
        return None

    settings_model, target_chat = await resolve_message_context(session, None, strict=False)
    if not settings_model or not target_chat:
        return None

    login_timestamp = payload.reported_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    text = (
        f"*SSH Login Detected*\n\n"
        f"*{_escape_markdown(backend.name)}*\n"
        f"✅ Successful SSH login detected at {_escape_markdown(login_timestamp)}."
    )
    extra_lines = _build_ssh_success_lines(payload)
    if extra_lines:
        text = "\n".join([text, *extra_lines])
    try:
        await _send_tracked_telegram_message(
            session,
            bot_token=settings_model.bot_token,
            target_chat=target_chat,
            text=text,
            category="ssh_login_success",
            severity="info",
            title=f"Successful SSH login: {backend.name}",
            backend_id=backend.id,
            backend_name=backend.name,
        )
    except TelegramError as exc:
        logger.warning("Failed to send SSH login notification: %s", exc)
        return None
    return text
