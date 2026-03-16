from datetime import datetime, timedelta, timezone

import pytest

from backend.app.models.monitors import MetricSnapshot, MonitoredBackend, QuickStatusItem, TelegramSettings
from backend.app.services.telegram_notifications import (
    dispatch_due_quick_status_notifications,
    maybe_send_successful_ssh_login_notification,
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


@pytest.mark.asyncio
async def test_transition_notification_includes_top_processes_for_cpu_and_memory_alerts(db_session, monkeypatch):
    backend = await _create_backend(db_session)
    await _create_settings(db_session, cooldown_minutes=0, last_sent_at=None)

    cpu_item = QuickStatusItem(
        backend_id=backend.id,
        label="CPU load",
        metric_key="cpu_load_one",
        warning_threshold=1,
        critical_threshold=2,
        display_order=0,
        last_notified_status="ok",
    )
    memory_item = QuickStatusItem(
        backend_id=backend.id,
        label="Memory available",
        metric_key="memory_available_gb",
        warning_threshold=2,
        critical_threshold=1,
        display_order=1,
        last_notified_status="ok",
    )
    db_session.add(cpu_item)
    db_session.add(memory_item)
    await db_session.commit()
    await db_session.refresh(cpu_item)
    await db_session.refresh(memory_item)

    snapshot = MetricSnapshot(
        backend_id=backend.id,
        reported_at=datetime.now(tz=timezone.utc),
        cpu_load={"one": 3.0},
        memory_available_gb=0.5,
        raw_payload={
            "top_processes": {
                "cpu": [{"pid": 101, "name": "ffmpeg", "cpu_percent": 87.5, "memory_percent": 4.2}],
                "memory": [{"pid": 202, "name": "postgres", "cpu_percent": 12.0, "memory_percent": 33.3}],
            }
        },
    )
    db_session.add(snapshot)
    await db_session.commit()

    await queue_quick_status_notifications(db_session, [cpu_item, memory_item])

    sent: list[str] = []

    async def fake_send_message(token, chat_id, text):
        sent.append(text)

    monkeypatch.setattr("backend.app.services.telegram_notifications.send_message", fake_send_message)

    text = await dispatch_due_quick_status_notifications(db_session)

    assert text is not None
    assert "Top CPU Processes" in text
    assert "ffmpeg" in text
    assert "Top Memory Processes" in text
    assert "postgres" in text
    assert sent


@pytest.mark.asyncio
async def test_transition_notification_includes_ssh_failure_details(db_session, monkeypatch):
    backend = await _create_backend(db_session)
    await _create_settings(db_session, cooldown_minutes=0, last_sent_at=None)

    ssh_item = QuickStatusItem(
        backend_id=backend.id,
        label="SSH failed login",
        metric_key="ssh_last_unsuccessful_attempt",
        warning_threshold=168,
        critical_threshold=24,
        display_order=0,
        last_notified_status="ok",
    )
    db_session.add(ssh_item)
    await db_session.commit()
    await db_session.refresh(ssh_item)

    snapshot = MetricSnapshot(
        backend_id=backend.id,
        reported_at=datetime.now(tz=timezone.utc),
        raw_payload={
            "ssh_last_unsuccessful_attempt_seconds": 3600,
            "ssh_last_failure_auth_method": "publickey",
            "ssh_last_failure_username": "root",
            "ssh_last_failure_source_ip": "10.0.0.8",
            "ssh_last_failure_port": 55123,
            "ssh_last_failure_line": "Failed publickey for root from 10.0.0.8 port 55123 ssh2",
        },
    )
    db_session.add(snapshot)
    await db_session.commit()

    await queue_quick_status_notifications(db_session, [ssh_item])

    sent: list[str] = []

    async def fake_send_message(token, chat_id, text):
        sent.append(text)

    monkeypatch.setattr("backend.app.services.telegram_notifications.send_message", fake_send_message)

    text = await dispatch_due_quick_status_notifications(db_session)

    assert text is not None
    assert "SSH Failure Details" in text
    assert "Method: publickey" in text
    assert "User: root" in text
    assert "Source: 10.0.0.8" in text
    assert "Port: 55123" in text
    assert sent


@pytest.mark.asyncio
async def test_successful_ssh_login_notification_includes_details(db_session, monkeypatch):
    backend = await _create_backend(db_session)
    await _create_settings(db_session, cooldown_minutes=0, last_sent_at=None)

    previous_snapshot = MetricSnapshot(
        backend_id=backend.id,
        reported_at=datetime.now(tz=timezone.utc) - timedelta(minutes=5),
        raw_payload={"ssh_last_successful_login_seconds": 7200},
    )
    db_session.add(previous_snapshot)
    await db_session.commit()

    from backend.app.schemas.metrics import MetricSnapshotCreate

    reported_at = datetime(2026, 3, 16, 16, 42, 0, tzinfo=timezone.utc)

    payload = MetricSnapshotCreate.model_validate(
        {
            "reported_at": reported_at,
            "raw_payload": {
                "ssh_last_successful_login_seconds": 30,
                "ssh_last_successful_auth_method": "publickey",
                "ssh_last_successful_username": "root",
                "ssh_last_successful_source_ip": "10.0.0.8",
                "ssh_last_successful_port": 55123,
                "ssh_last_successful_line": "Accepted publickey for root from 10.0.0.8 port 55123 ssh2",
            },
        }
    )

    sent: list[str] = []

    async def fake_send_message(token, chat_id, text):
        sent.append(text)

    monkeypatch.setattr("backend.app.services.telegram_notifications.send_message", fake_send_message)

    text = await maybe_send_successful_ssh_login_notification(
        db_session,
        backend=backend,
        previous_snapshot=previous_snapshot,
        payload=payload,
    )

    assert text is not None
    assert "Successful SSH login detected at 2026\\-03\\-16 16:42:00 UTC\\." in text
    assert "SSH Login Details" in text
    assert "Method: publickey" in text
    assert "User: root" in text
    assert "Source: 10.0.0.8" in text
    assert "Port: 55123" in text
    assert sent
