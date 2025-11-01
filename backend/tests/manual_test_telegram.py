import asyncio

from backend.app.routers.telegram import telegram_webhook, TelegramUpdate


class DummySession:
    pass

async def run():
    results = {}

    async def fake_send_stats(session, chat_id=None, **kwargs):
        results['stats_chat'] = chat_id
        return 'ok'

    async def fake_send_warn(session, chat_id=None, **kwargs):
        results['warn_chat'] = chat_id
        return 'warn'

    async def fake_send_message(token, chat_id, text):
        results['unauthorized'] = (token, chat_id, text)

    from backend.app import routers
    # Monkeypatch directly
    telegram = routers.telegram
    telegram.send_stats_message = fake_send_stats
    telegram.send_warn_message = fake_send_warn
    telegram.send_message = fake_send_message
    telegram._is_authorized_user = lambda message: True

    update_stats = TelegramUpdate(message={"text": "/stats", "from": {"id": 1}, "chat": {"id": 100}})
    await telegram_webhook(update_stats, session=DummySession())

    telegram._is_authorized_user = lambda message: True
    update_warn = TelegramUpdate(message={"text": "/warn", "from": {"id": 1}, "chat": {"id": 200}})
    await telegram_webhook(update_warn, session=DummySession())

    telegram._is_authorized_user = lambda message: False
    await telegram_webhook(update_stats, session=DummySession())

    return results

if __name__ == "__main__":
    out = asyncio.run(run())
    print(out)
