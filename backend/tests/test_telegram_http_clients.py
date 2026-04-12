from types import SimpleNamespace

import httpx
import pytest
from telegram.request import BaseRequest, HTTPXRequest

from backend.app.services import http_clients
from backend.app.services.telegram_service import TelegramError, send_message


def _telegram_settings(
    *,
    proxy_url: str | None = None,
    bind_interface: str | None = None,
    local_address: str | None = None,
):
    return SimpleNamespace(
        telegram_proxy_url=proxy_url,
        telegram_bind_interface=bind_interface,
        telegram_local_address=local_address,
        monitor_request_timeout_seconds=10,
    )


def test_get_telegram_network_kwargs_include_proxy_interface_and_local_address(monkeypatch):
    route = http_clients.TelegramRouteConfig(
        name="vpn-interface",
        proxy="http://proxy.internal:10809",
        bind_interface="wg0",
        local_address="192.0.2.20",
    )
    monkeypatch.setattr(
        http_clients,
        "settings",
        _telegram_settings(
            proxy_url="http://proxy.internal:10809",
            bind_interface="wg0",
            local_address="192.0.2.20",
        ),
        raising=False,
    )
    monkeypatch.setattr(http_clients.socket, "SO_BINDTODEVICE", 25, raising=False)

    kwargs = http_clients.get_telegram_network_kwargs(route)

    assert kwargs["proxy"] == "http://proxy.internal:10809"
    assert kwargs["local_address"] == "192.0.2.20"
    assert kwargs["socket_options"] == [(http_clients.socket.SOL_SOCKET, 25, b"wg0\0")]


def test_get_telegram_route_configs_prioritize_proxy_then_interface(monkeypatch):
    monkeypatch.setattr(
        http_clients,
        "settings",
        _telegram_settings(
            proxy_url="http://proxy.internal:10809",
            bind_interface="wg0",
            local_address="192.0.2.20",
        ),
        raising=False,
    )

    routes = http_clients.get_telegram_route_configs()

    assert [route.name for route in routes] == ["proxy", "vpn-interface"]
    assert routes[0].proxy == "http://proxy.internal:10809"
    assert routes[1].bind_interface == "wg0"
    assert routes[1].local_address == "192.0.2.20"


def test_build_telegram_request_uses_fallback_request_for_multiple_routes(monkeypatch):
    monkeypatch.setattr(
        http_clients,
        "settings",
        _telegram_settings(proxy_url="http://proxy.internal:10809", bind_interface="wg0"),
        raising=False,
    )

    request = http_clients.build_telegram_request(connection_pool_size=7)

    assert isinstance(request, BaseRequest)
    assert isinstance(request, http_clients.FallbackTelegramRequest)


def test_build_telegram_request_uses_custom_transport_for_single_route(monkeypatch):
    monkeypatch.setattr(http_clients, "settings", _telegram_settings(), raising=False)

    request = http_clients.build_telegram_request(connection_pool_size=7)

    assert isinstance(request, HTTPXRequest)
    assert request._client_kwargs["transport"] is not None
    assert request._client_kwargs["proxy"] is None


@pytest.mark.asyncio
async def test_send_message_wraps_httpx_transport_errors(monkeypatch):
    class BrokenClient:
        async def post(self, *args, **kwargs):
            request = httpx.Request("POST", "https://api.telegram.org")
            raise httpx.ConnectError("connect failed", request=request)

    monkeypatch.setattr(
        "backend.app.services.telegram_service.get_telegram_http_clients",
        lambda: [("proxy", BrokenClient())],
    )

    with pytest.raises(TelegramError, match="Telegram transport error"):
        await send_message("token", "123", "hello")


@pytest.mark.asyncio
async def test_send_message_falls_back_to_second_route(monkeypatch):
    calls: list[str] = []

    class BrokenClient:
        async def post(self, *args, **kwargs):
            calls.append("proxy")
            request = httpx.Request("POST", "https://api.telegram.org")
            raise httpx.ConnectError("connect failed", request=request)

    class WorkingResponse:
        status_code = 200
        is_error = False

        @staticmethod
        def json():
            return {"ok": True}

    class WorkingClient:
        async def post(self, *args, **kwargs):
            calls.append("vpn-interface")
            return WorkingResponse()

    monkeypatch.setattr(
        "backend.app.services.telegram_service.get_telegram_http_clients",
        lambda: [("proxy", BrokenClient()), ("vpn-interface", WorkingClient())],
    )

    await send_message("token", "123", "hello")

    assert calls == ["proxy", "vpn-interface"]
