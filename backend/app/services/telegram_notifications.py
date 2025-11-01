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
from backend.app.services.metrics_service import build_stats_message, build_warn_message
from backend.app.services.telegram_settings import get_or_create_settings
from backend.app.services.telegram_service import TelegramError, send_message


logger = logging.getLogger(__name__)


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


async def try_send_warning_notification(
    session: AsyncSession,
    chat_id: str | None = None,
) -> str | None:
    settings_model, target_chat = await resolve_message_context(session, chat_id, strict=False)
    if not settings_model or not target_chat:
        return None
    backends = await fetch_backends_with_latest(session)
    text = build_warn_message(backends)
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
    return await send_compiled_message(session, build_warn_message, chat_id=chat_id)
