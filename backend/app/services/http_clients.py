from __future__ import annotations

from dataclasses import dataclass
import logging
import socket
from typing import Any, Sequence

import httpx
from telegram.error import NetworkError, TimedOut
from telegram.request import BaseRequest, HTTPXRequest

from backend.app.core.config import settings


logger = logging.getLogger(__name__)
_monitor_client: httpx.AsyncClient | None = None
_telegram_clients: list[tuple[str, httpx.AsyncClient]] | None = None


@dataclass(frozen=True, slots=True)
class TelegramRouteConfig:
    name: str
    proxy: str | None = None
    bind_interface: str | None = None
    local_address: str | None = None


def get_monitor_http_client() -> httpx.AsyncClient:
    global _monitor_client
    if _monitor_client is None:
        _monitor_client = httpx.AsyncClient(
            timeout=settings.monitor_request_timeout_seconds,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
    return _monitor_client


def get_telegram_route_configs() -> list[TelegramRouteConfig]:
    proxy = (settings.telegram_proxy_url or "").strip() or None
    bind_interface = (settings.telegram_bind_interface or "").strip() or None
    local_address = (settings.telegram_local_address or "").strip() or None

    routes: list[TelegramRouteConfig] = []
    if proxy:
        routes.append(TelegramRouteConfig(name="proxy", proxy=proxy))
    if bind_interface or local_address:
        routes.append(
            TelegramRouteConfig(
                name="vpn-interface",
                bind_interface=bind_interface,
                local_address=local_address,
            )
        )
    if not routes:
        routes.append(TelegramRouteConfig(name="default"))
    return routes


def get_telegram_socket_options(route: TelegramRouteConfig) -> list[tuple[int, int, bytes]]:
    interface = (route.bind_interface or "").strip()
    if not interface:
        return []

    bind_to_device = getattr(socket, "SO_BINDTODEVICE", None)
    if bind_to_device is None:
        logger.warning(
            "Telegram bind interface %s ignored because SO_BINDTODEVICE is unavailable on this platform",
            interface,
        )
        return []

    return [(socket.SOL_SOCKET, bind_to_device, interface.encode() + b"\0")]


def get_telegram_network_kwargs(route: TelegramRouteConfig) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if route.proxy:
        kwargs["proxy"] = route.proxy
    if route.local_address:
        kwargs["local_address"] = route.local_address
    socket_options = get_telegram_socket_options(route)
    if socket_options:
        kwargs["socket_options"] = socket_options
    return kwargs


def build_telegram_transport(
    route: TelegramRouteConfig,
    *,
    max_connections: int,
    max_keepalive_connections: int,
) -> httpx.AsyncHTTPTransport:
    return httpx.AsyncHTTPTransport(
        limits=httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive_connections,
        ),
        **get_telegram_network_kwargs(route),
    )


def _build_single_telegram_request(
    route: TelegramRouteConfig,
    *,
    connection_pool_size: int,
) -> HTTPXRequest:
    return HTTPXRequest(
        connection_pool_size=connection_pool_size,
        read_timeout=10,
        write_timeout=10,
        connect_timeout=10,
        pool_timeout=5,
        httpx_kwargs={
            "transport": build_telegram_transport(
                route,
                max_connections=connection_pool_size,
                max_keepalive_connections=connection_pool_size,
            )
        },
    )


class FallbackTelegramRequest(BaseRequest):
    def __init__(self, requests: Sequence[tuple[str, HTTPXRequest]]) -> None:
        self._requests = list(requests)

    @property
    def read_timeout(self) -> float | None:
        return self._requests[0][1].read_timeout

    async def initialize(self) -> None:
        for _, request in self._requests:
            await request.initialize()

    async def shutdown(self) -> None:
        first_error: Exception | None = None
        for _, request in self._requests:
            try:
                await request.shutdown()
            except Exception as exc:  # pragma: no cover - defensive cleanup
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    async def do_request(
        self,
        url: str,
        method: str,
        request_data=None,
        read_timeout: Any = BaseRequest.DEFAULT_NONE,
        write_timeout: Any = BaseRequest.DEFAULT_NONE,
        connect_timeout: Any = BaseRequest.DEFAULT_NONE,
        pool_timeout: Any = BaseRequest.DEFAULT_NONE,
    ) -> tuple[int, bytes]:
        errors: list[str] = []
        for index, (name, request) in enumerate(self._requests):
            try:
                return await request.do_request(
                    url=url,
                    method=method,
                    request_data=request_data,
                    read_timeout=read_timeout,
                    write_timeout=write_timeout,
                    connect_timeout=connect_timeout,
                    pool_timeout=pool_timeout,
                )
            except (TimedOut, NetworkError) as exc:
                errors.append(f"{name}: {exc}")
                if index + 1 < len(self._requests):
                    logger.warning("Telegram route %s failed, trying next route: %s", name, exc)
                    continue
                raise NetworkError(f"All Telegram routes failed: {'; '.join(errors)}") from exc

        raise NetworkError("No Telegram routes configured")


def build_telegram_request(*, connection_pool_size: int = 10) -> BaseRequest:
    requests = [
        (route.name, _build_single_telegram_request(route, connection_pool_size=connection_pool_size))
        for route in get_telegram_route_configs()
    ]
    if len(requests) == 1:
        return requests[0][1]
    return FallbackTelegramRequest(requests)


def get_telegram_http_clients() -> list[tuple[str, httpx.AsyncClient]]:
    global _telegram_clients
    if _telegram_clients is None:
        _telegram_clients = []
        for route in get_telegram_route_configs():
            client = httpx.AsyncClient(
                timeout=10,
                transport=build_telegram_transport(route, max_connections=20, max_keepalive_connections=10),
            )
            _telegram_clients.append((route.name, client))
    return _telegram_clients


def get_telegram_http_client() -> httpx.AsyncClient:
    clients = get_telegram_http_clients()
    if not clients:
        raise RuntimeError("No Telegram HTTP client configured")
    return clients[0][1]


async def close_http_clients() -> None:
    global _monitor_client, _telegram_clients
    if _monitor_client is not None:
        await _monitor_client.aclose()
        _monitor_client = None
    if _telegram_clients is not None:
        for _, client in _telegram_clients:
            await client.aclose()
        _telegram_clients = None
