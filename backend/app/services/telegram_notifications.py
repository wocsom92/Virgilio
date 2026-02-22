from __future__ import annotations

import logging
from typing import Callable, Sequence

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.models.monitors import MonitoredBackend, TelegramSettings as TelegramSettingsModel
from backend.app.schemas.backend import BackendWithLatestSnapshot, MonitoredBackendRead
from backend.app.schemas.common import MetricSnapshotRead
from backend.app.schemas.quick_status import QuickStatusTileRead
from backend.app.services.metrics_service import build_stats_message
from backend.app.services.quick_status import QuickStatusTransition, build_quick_status_tiles, list_quick_status_items
from backend.app.services.telegram_settings import get_or_create_settings
from backend.app.services.telegram_service import TelegramError, send_message


logger = logging.getLogger(__name__)
_MARKDOWN_SPECIAL_CHARS = set("_*[]()~`\\")


def _escape_markdown(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    return "".join(f"\\{char}" if char in _MARKDOWN_SPECIAL_CHARS else char for char in text)


def _status_label(status: str) -> str:
    if status == "critical":
        return "error"
    if status == "warn":
        return "warning"
    return status


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
        icon = "🚨" if transition.current_status == "critical" else "⚠️"
        lines.append(
            f"\n*{_escape_markdown(transition.backend_name)}*"
            f"\n{icon} {_escape_markdown(transition.label)} changed "
            f"from {_escape_markdown(_status_label(transition.previous_status))} "
            f"to {_escape_markdown(_status_label(transition.current_status))} "
            f"({_escape_markdown(transition.display_value)})"
        )
    return "\n".join(lines)


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
