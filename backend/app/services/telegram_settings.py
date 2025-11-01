from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.models.monitors import TelegramSettings as TelegramSettingsModel
from backend.app.schemas.telegram import WarnThresholds


async def get_or_create_settings(session: AsyncSession) -> TelegramSettingsModel:
    result = await session.execute(select(TelegramSettingsModel).limit(1))
    instance = result.scalars().first()
    if instance is None:
        instance = TelegramSettingsModel(
            bot_token=settings.telegram_bot_token,
            default_chat_id=settings.telegram_default_chat_id,
            is_active=bool(settings.telegram_bot_token and settings.telegram_default_chat_id),
        )
        session.add(instance)
        await session.commit()
        await session.refresh(instance)
    return instance


async def get_warn_thresholds(session: AsyncSession) -> WarnThresholds | None:
    settings_model = await get_or_create_settings(session)
    raw_thresholds = settings_model.warn_thresholds
    if not raw_thresholds:
        return None
    try:
        return WarnThresholds.model_validate(raw_thresholds)
    except Exception:  # pragma: no cover - defensive parsing for legacy data
        return None
