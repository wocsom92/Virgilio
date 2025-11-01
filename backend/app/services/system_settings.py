from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.monitors import SystemSettings

DEFAULT_RETENTION_DAYS = 7
MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = 90


def _clamp_retention_days(value: int | None) -> int:
    """Clamp the provided retention value to sane bounds."""
    if value is None:
        return DEFAULT_RETENTION_DAYS
    return max(MIN_RETENTION_DAYS, min(MAX_RETENTION_DAYS, int(value)))


async def get_system_settings(session: AsyncSession) -> SystemSettings:
    result = await session.execute(select(SystemSettings).limit(1))
    settings = result.scalars().first()
    if settings is None:
        settings = SystemSettings(metric_retention_days=DEFAULT_RETENTION_DAYS)
        session.add(settings)
        await session.commit()
        await session.refresh(settings)
    return settings


async def get_metric_retention_days(session: AsyncSession) -> int:
    settings = await get_system_settings(session)
    return _clamp_retention_days(settings.metric_retention_days)


async def metric_retention_timedelta(session: AsyncSession) -> timedelta:
    return timedelta(days=await get_metric_retention_days(session))


async def update_metric_retention_days(session: AsyncSession, retention_days: int) -> SystemSettings:
    settings = await get_system_settings(session)
    settings.metric_retention_days = _clamp_retention_days(retention_days)
    session.add(settings)
    await session.commit()
    await session.refresh(settings)
    return settings
