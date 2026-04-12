import httpx

from backend.app.services.http_clients import get_telegram_http_clients


class TelegramError(RuntimeError):
    pass


async def send_message(bot_token: str, chat_id: str, text: str, parse_mode: str = "Markdown") -> None:
    """Send a Telegram message using the Bot API."""
    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    clients = get_telegram_http_clients()
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode, "disable_web_page_preview": True}
    transport_errors: list[str] = []

    for index, (route_name, client) in enumerate(clients):
        try:
            response = await client.post(api_url, json=payload)
        except httpx.HTTPError as exc:
            transport_errors.append(f"{route_name}: {exc}")
            if index + 1 < len(clients):
                continue
            raise TelegramError(f"Telegram transport error: {'; '.join(transport_errors)}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise TelegramError(
                f"Telegram API returned an invalid response with status {response.status_code}"
            ) from exc

        if response.is_error or not data.get("ok", False):
            raise TelegramError(f"Telegram API error: {data}")
        return

    raise TelegramError("Telegram transport error: no routes available")
