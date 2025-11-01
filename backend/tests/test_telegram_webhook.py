import pytest

from backend.app.routers.telegram import telegram_webhook, TelegramUpdate


class DummySession:
    pass


@pytest.mark.asyncio
async def test_telegram_webhook_stats_triggers_notification(monkeypatch):
    monkeypatch.setattr("backend.app.routers.telegram._is_authorized_user", lambda message: True)

    captured = {}

    async def fake_send_stats(session, chat_id=None, **kwargs):
        captured["stats"] = (session, chat_id)
        return "sent"

    monkeypatch.setattr("backend.app.routers.telegram.send_stats_message", fake_send_stats)

    update = TelegramUpdate(message={
        "text": "/stats",
        "from": {"id": 111},
        "chat": {"id": 222},
    })

    response = await telegram_webhook(update, session=DummySession())

    assert response == {"ok": True}
    assert captured["stats"][1] == "222"


@pytest.mark.asyncio
async def test_telegram_webhook_warn_triggers_notification(monkeypatch):
    monkeypatch.setattr("backend.app.routers.telegram._is_authorized_user", lambda message: True)

    captured = {}

    async def fake_send_warn(session, chat_id=None, **kwargs):
        captured["warn"] = (session, chat_id)
        return "sent"

    monkeypatch.setattr("backend.app.routers.telegram.send_warn_message", fake_send_warn)

    update = TelegramUpdate(message={
        "text": "/warn",
        "from": {"id": 111},
        "chat": {"id": 333},
    })

    response = await telegram_webhook(update, session=DummySession())

    assert response == {"ok": True}
    assert captured["warn"][1] == "333"


@pytest.mark.asyncio
async def test_telegram_webhook_unauthorized(monkeypatch):
    captured = {}

    class Settings:
        bot_token = "token"

    async def fake_get_settings(session):
        return Settings()

    async def fake_send_message(token, chat_id, text):
        captured['sent'] = (token, chat_id, text)

    monkeypatch.setattr("backend.app.routers.telegram.get_or_create_settings", fake_get_settings)
    monkeypatch.setattr("backend.app.routers.telegram.send_message", fake_send_message)
    monkeypatch.setattr("backend.app.routers.telegram._is_authorized_user", lambda message: False)

    update = TelegramUpdate(message={
        "text": "/stats",
        "from": {"id": 444},
        "chat": {"id": 555},
    })

    response = await telegram_webhook(update, session=DummySession())

    assert response == {"ok": False, "error": "unauthorized"}
    assert captured['sent'][1] == "555"
