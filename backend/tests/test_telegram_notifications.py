from datetime import datetime, timedelta, timezone

import pytest

from backend.app.models.monitors import MetricSnapshot, MonitoredBackend, QuickStatusItem, TelegramSettings
from backend.app.services.telegram_notifications import (
    dispatch_due_quick_status_notifications,
    queue_quick_status_notifications,
)


async def _create_backend(session, name: str = "backend-1") -> MonitoredBackend:
    backend = MonitoredBackend(
        name=name,
        base_url="http://example.test",
        api_token="token",
        is_active=True,
        poll_interval_seconds=60,
    )
    session.add(backend)
    await session.commit()
    await session.refresh(backend)
    return backend


async def _create_settings(session, *, cooldown_minutes: int, last_sent_at: datetime | None) -> TelegramSettings:
    settings = TelegramSettings(
        bot_token="token",
        default_chat_id="chat-id",
        is_active=True,
        notification_batch_window_seconds=60,
        notification_cooldown_minutes=cooldown_minutes,
        quick_status_last_notification_at=last_sent_at,
    )
    session.add(settings)
    await session.commit()
    await session.refresh(settings)
    return settings


async def _create_item(
    session,
    backend_id: int,
    *,
    last_notified_status: str | None,
) -> QuickStatusItem:
    item = QuickStatusItem(
        backend_id=backend_id,
        label="Disk",
        metric_key="disk_usage_percent",
        warning_threshold=80,
        critical_threshold=90,
        display_order=0,
        last_notified_status=last_notified_status,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


async def _create_snapshot(session, backend_id: int, disk_usage_percent: float) -> MetricSnapshot:
    snapshot = MetricSnapshot(
        backend_id=backend_id,
        reported_at=datetime.now(tz=timezone.utc),
        disk_usage_percent=disk_usage_percent,
        raw_payload={"disk_usage_percent": disk_usage_percent},
    )
    session.add(snapshot)
    await session.commit()
    await session.refresh(snapshot)
    return snapshot


@pytest.mark.asyncio
async def test_alert_notification_is_queued_until_cooldown_expires(db_session, monkeypatch):
    now = datetime.now(tz=timezone.utc)
    backend = await _create_backend(db_session)
    await _create_settings(db_session, cooldown_minutes=15, last_sent_at=now)
    item = await _create_item(db_session, backend.id, last_notified_status="ok")
    await _create_snapshot(db_session, backend.id, 95)

    await queue_quick_status_notifications(db_session, [item])
    await db_session.refresh(item)

    assert item.pending_notification_status == "critical"
    assert item.pending_notification_due_at is not None
    assert item.pending_notification_due_at >= now + timedelta(minutes=15) - timedelta(seconds=5)

    sent: list[str] = []

    async def fake_send_message(token, chat_id, text):
        sent.append(text)

    monkeypatch.setattr("backend.app.services.telegram_notifications.send_message", fake_send_message)

    assert await dispatch_due_quick_status_notifications(db_session) is None
    assert sent == []

    settings = await db_session.get(TelegramSettings, 1)
    settings.quick_status_last_notification_at = now - timedelta(minutes=16)
    item.pending_notification_due_at = now - timedelta(seconds=1)
    db_session.add(settings)
    db_session.add(item)
    await db_session.commit()

    text = await dispatch_due_quick_status_notifications(db_session)
    await db_session.refresh(item)

    assert text is not None
    assert "Disk changed" in text
    assert sent
    assert item.last_notified_status == "critical"
    assert item.pending_notification_status is None


@pytest.mark.asyncio
async def test_pending_alert_is_dropped_if_state_clears_before_cooldown(db_session, monkeypatch):
    now = datetime.now(tz=timezone.utc)
    backend = await _create_backend(db_session)
    settings = await _create_settings(db_session, cooldown_minutes=15, last_sent_at=now)
    item = await _create_item(db_session, backend.id, last_notified_status="ok")
    await _create_snapshot(db_session, backend.id, 95)

    await queue_quick_status_notifications(db_session, [item])
    await db_session.refresh(item)
    assert item.pending_notification_status == "critical"

    await _create_snapshot(db_session, backend.id, 40)
    await queue_quick_status_notifications(db_session, [item])
    await db_session.refresh(item)

    assert item.pending_notification_status is None
    assert item.pending_notification_due_at is None

    sent: list[str] = []

    async def fake_send_message(token, chat_id, text):
        sent.append(text)

    monkeypatch.setattr("backend.app.services.telegram_notifications.send_message", fake_send_message)

    settings.quick_status_last_notification_at = now - timedelta(minutes=20)
    db_session.add(settings)
    await db_session.commit()

    assert await dispatch_due_quick_status_notifications(db_session) is None
    assert sent == []


@pytest.mark.asyncio
async def test_recovery_notification_is_sent_when_alert_clears(db_session, monkeypatch):
    backend = await _create_backend(db_session)
    await _create_settings(db_session, cooldown_minutes=0, last_sent_at=None)
    item = await _create_item(db_session, backend.id, last_notified_status="critical")
    await _create_snapshot(db_session, backend.id, 35)

    await queue_quick_status_notifications(db_session, [item])
    await db_session.refresh(item)
    assert item.pending_notification_status == "ok"

    sent: list[str] = []

    async def fake_send_message(token, chat_id, text):
        sent.append(text)

    monkeypatch.setattr("backend.app.services.telegram_notifications.send_message", fake_send_message)

    text = await dispatch_due_quick_status_notifications(db_session)
    await db_session.refresh(item)

    assert text is not None
    assert "cleared" in text
    assert sent
    assert item.last_notified_status == "ok"
    assert item.pending_notification_status is None


@pytest.mark.asyncio
async def test_pending_alerts_are_grouped_across_batch_window(db_session, monkeypatch):
    now = datetime.now(tz=timezone.utc)
    backend = await _create_backend(db_session)
    await _create_settings(db_session, cooldown_minutes=0, last_sent_at=None)
    disk_item = await _create_item(db_session, backend.id, last_notified_status="ok")
    load_item = QuickStatusItem(
        backend_id=backend.id,
        label="Load",
        metric_key="cpu_load_one",
        warning_threshold=1,
        critical_threshold=2,
        display_order=1,
        last_notified_status="ok",
    )
    db_session.add(load_item)
    await db_session.commit()
    await db_session.refresh(load_item)

    snapshot = MetricSnapshot(
        backend_id=backend.id,
        reported_at=now,
        disk_usage_percent=95,
        cpu_load={"one": 3.0},
        raw_payload={"disk_usage_percent": 95},
    )
    db_session.add(snapshot)
    await db_session.commit()

    await queue_quick_status_notifications(db_session, [disk_item])
    await db_session.refresh(disk_item)
    first_due_at = disk_item.pending_notification_due_at
    assert first_due_at is not None

    await queue_quick_status_notifications(db_session, [load_item])
    await db_session.refresh(load_item)
    assert load_item.pending_notification_due_at is not None
    assert load_item.pending_notification_due_at >= first_due_at

    disk_item.pending_notification_due_at = now - timedelta(seconds=1)
    db_session.add(disk_item)
    await db_session.commit()

    sent: list[str] = []

    async def fake_send_message(token, chat_id, text):
        sent.append(text)

    monkeypatch.setattr("backend.app.services.telegram_notifications.send_message", fake_send_message)

    text = await dispatch_due_quick_status_notifications(db_session)

    assert text is not None
    assert "Disk changed" in text
    assert "Load changed" in text
    assert sent
