import json
import logging

from backend.app.core.logging import JsonFormatter


def test_json_formatter_redacts_telegram_bot_tokens_from_httpx_logs():
    token = "1234567890:abcdefghijklmnopqrstuvwxyz"
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='HTTP Request: POST https://api.telegram.org/bot%s/sendMessage "HTTP/1.1 200 OK"',
        args=(token,),
        exc_info=None,
    )

    payload = json.loads(JsonFormatter(service_name="backend").format(record))

    assert token not in payload["msg"]
    assert token not in payload["message"]
    assert "/bot<redacted>/sendMessage" in payload["msg"]
    assert "/bot<redacted>/sendMessage" in payload["message"]
