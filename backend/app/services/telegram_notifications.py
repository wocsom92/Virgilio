from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Sequence

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.models.monitors import MonitoredBackend, QuickStatusItem, TelegramSettings as TelegramSettingsModel
from backend.app.schemas.backend import BackendWithLatestSnapshot, MonitoredBackendRead
from backend.app.schemas.common import MetricSnapshotRead
from backend.app.schemas.quick_status import QuickStatusTileRead
from backend.app.services.metrics_service import build_stats_message
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
    backend_name: str | None = None,
) -> list[BackendWithLatestSnapshot]:
    query = select(MonitoredBackend).options(selectinload(MonitoredBackend.snapshots))
    if backend_id is not None:
        query = query.where(MonitoredBackend.id == backend_id)
    if backend_name:
        query = query.where(MonitoredBackend.name.ilike(f"%{backend_name}%"))

    result = await session.execute(query)
    backends: list[BackendWithLatestSnapshot] = []
    for backend in result.scalars():
        latest_snapshot = backend.snapshots[-1] if backend.snapshots else None
        base = MonitoredBackendRead.model_validate(backend)
        backends.append(
            BackendWithLatestSnapshot(
                **base.model_dump(),
                latest_snapshot=MetricSnapshotRead.model_validate(latest_snapshot) if latest_snapshot else None,
            )
        )
    return backends


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
        await send_message(settings_model.bot_token, target_chat, text)
    except TelegramError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return text


async def _load_quick_status_tiles(session: AsyncSession) -> list[QuickStatusTileRead]:
    items = await list_quick_status_items(session)
    return await build_quick_status_tiles(session, items)


def _build_warn_message_from_tiles(tiles: Sequence[QuickStatusTileRead]) -> str:
    lines: list[str] = ["*Server Monitor Warnings*"]
    alert_tiles = [tile for tile in tiles if tile.status in {"warn", "critical"}]
    if not alert_tiles:
        lines.append("\nAll systems nominal ✅")
        return "\n".join(lines)

    grouped: dict[str, list[QuickStatusTileRead]] = {}
    for tile in alert_tiles:
        grouped.setdefault(tile.backend_name, []).append(tile)

    for backend_name in sorted(grouped):
        lines.append(f"\n*{_escape_markdown(backend_name)}*")
        for tile in grouped[backend_name]:
            icon = "🚨" if tile.status == "critical" else "⚠️"
            lines.append(
                f"{icon} {_escape_markdown(tile.label)} "
                f"({_escape_markdown(_status_label(tile.status))}) "
                f"at {_escape_markdown(tile.display_value)}"
            )
    return "\n".join(lines)


def _build_transition_message(transitions: Sequence[QuickStatusTransition]) -> str:
    lines: list[str] = ["*Server Monitor Alert*"]
    for transition in transitions:
        if transition.current_status in _ALERT_STATUSES:
            icon = "🚨" if transition.current_status == "critical" else "⚠️"
            lines.append(
                f"\n*{_escape_markdown(transition.backend_name)}*"
                f"\n{icon} {_escape_markdown(transition.label)} changed "
                f"from {_escape_markdown(_status_label(transition.previous_status))} "
                f"to {_escape_markdown(_status_label(transition.current_status))} "
                f"({_escape_markdown(transition.display_value)})"
            )
        else:
            lines.append(
                f"\n*{_escape_markdown(transition.backend_name)}*"
                f"\n✅ {_escape_markdown(transition.label)} cleared "
                f"(was {_escape_markdown(_status_label(transition.previous_status))}, "
                f"now {_escape_markdown(transition.display_value)})"
            )
    return "\n".join(lines)


async def queue_quick_status_notifications(
    session: AsyncSession,
    items: Sequence[QuickStatusItem],
) -> None:
    if not items:
        return
    settings_model = await get_or_create_settings(session)
    now = datetime.now(tz=timezone.utc)
    due_at = max(now, _cooldown_deadline(settings_model, now))

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
        if (
            item.pending_notification_status == current_status
            and item.pending_notification_due_at is not None
        ):
            continue
        item.pending_notification_status = current_status
        item.pending_notification_due_at = due_at
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

    result = await session.execute(
        select(QuickStatusItem)
        .options(selectinload(QuickStatusItem.backend))
        .where(
            QuickStatusItem.pending_notification_status.is_not(None),
            QuickStatusItem.pending_notification_due_at.is_not(None),
            QuickStatusItem.pending_notification_due_at <= now,
        )
        .order_by(QuickStatusItem.pending_notification_due_at, QuickStatusItem.display_order, QuickStatusItem.id)
    )
    items = list(result.scalars())
    if not items:
        return None

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
    text = _build_transition_message(transitions)
    try:
        await send_message(settings_model_ctx.bot_token, target_chat, text)
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
    text = _build_transition_message(transitions)
    try:
        await send_message(settings_model.bot_token, target_chat, text)
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
    return await send_compiled_message(
        session,
        build_stats_message,
        chat_id=chat_id,
        backend_id=backend_id,
        backend_name=backend_name,
    )


async def send_warn_message(session: AsyncSession, chat_id: str | None = None) -> str:
    settings_model, target_chat = await resolve_message_context(session, chat_id, strict=True)
    tiles = await _load_quick_status_tiles(session)
    text = _build_warn_message_from_tiles(tiles)
    try:
        await send_message(settings_model.bot_token, target_chat, text)
    except TelegramError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return text
