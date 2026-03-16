from __future__ import annotations

import httpx

from backend.app.core.config import settings


_monitor_client: httpx.AsyncClient | None = None
_telegram_client: httpx.AsyncClient | None = None


def get_monitor_http_client() -> httpx.AsyncClient:
    global _monitor_client
    if _monitor_client is None:
        _monitor_client = httpx.AsyncClient(
            timeout=settings.monitor_request_timeout_seconds,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
    return _monitor_client


def get_telegram_http_client() -> httpx.AsyncClient:
    global _telegram_client
    if _telegram_client is None:
        _telegram_client = httpx.AsyncClient(
            timeout=10,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _telegram_client


async def close_http_clients() -> None:
    global _monitor_client, _telegram_client
    if _monitor_client is not None:
        await _monitor_client.aclose()
        _monitor_client = None
    if _telegram_client is not None:
        await _telegram_client.aclose()
        _telegram_client = None
