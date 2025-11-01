import httpx


class TelegramError(RuntimeError):
    pass


async def send_message(bot_token: str, chat_id: str, text: str, parse_mode: str = "Markdown") -> None:
    """Send a Telegram message using the Bot API."""
    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            api_url,
            json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode, "disable_web_page_preview": True},
        )
        data = response.json()
        if not data.get("ok", False):
            raise TelegramError(f"Telegram API error: {data}")
