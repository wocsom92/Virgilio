from __future__ import annotations

import asyncio
from dataclasses import dataclass
import ipaddress
import logging
import os
import socket
from urllib.parse import urlsplit

from backend.app.core.logging import configure_logging


logger = logging.getLogger(__name__)
_MAX_HEADER_BYTES = 65536


@dataclass(frozen=True, slots=True)
class ProxyRoute:
    name: str
    upstream_proxy_url: str | None = None
    bind_interface: str | None = None
    local_address: str | None = None


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _parse_allowed_cidrs(raw: str | None) -> list[ipaddress._BaseNetwork]:
    values = raw or "127.0.0.1/32,172.16.0.0/12"
    networks: list[ipaddress._BaseNetwork] = []
    for part in values.split(","):
        token = part.strip()
        if not token:
            continue
        networks.append(ipaddress.ip_network(token, strict=False))
    return networks


def _build_routes() -> list[ProxyRoute]:
    upstream_proxy_url = _env("TELEGRAM_EGRESS_PROXY_UPSTREAM_PROXY_URL", "http://127.0.0.1:10809")
    bind_interface = _env("TELEGRAM_EGRESS_PROXY_BIND_INTERFACE")
    local_address = _env("TELEGRAM_EGRESS_PROXY_LOCAL_ADDRESS")

    routes: list[ProxyRoute] = []
    if upstream_proxy_url:
        routes.append(ProxyRoute(name="xray-upstream", upstream_proxy_url=upstream_proxy_url))
    if bind_interface or local_address:
        routes.append(
            ProxyRoute(
                name="interface-direct",
                bind_interface=bind_interface,
                local_address=local_address,
            )
        )
    if not routes:
        routes.append(ProxyRoute(name="direct"))
    return routes


LISTEN_HOST = _env("TELEGRAM_EGRESS_PROXY_LISTEN_HOST", "0.0.0.0") or "0.0.0.0"
LISTEN_PORT = int(_env("TELEGRAM_EGRESS_PROXY_LISTEN_PORT", "18080") or "18080")
CONNECT_TIMEOUT_SECONDS = float(_env("TELEGRAM_EGRESS_PROXY_CONNECT_TIMEOUT_SECONDS", "10") or "10")
ALLOWED_CLIENT_CIDRS = _parse_allowed_cidrs(_env("TELEGRAM_EGRESS_PROXY_ALLOWED_CLIENT_CIDRS"))
ROUTES = _build_routes()


def _client_allowed(peer_host: str | None) -> bool:
    if not peer_host:
        return False
    try:
        client_ip = ipaddress.ip_address(peer_host)
    except ValueError:
        return False
    return any(client_ip in network for network in ALLOWED_CLIENT_CIDRS)


async def _read_headers(reader: asyncio.StreamReader) -> bytes:
    buffer = bytearray()
    while b"\r\n\r\n" not in buffer:
        chunk = await reader.read(4096)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > _MAX_HEADER_BYTES:
            raise ValueError("Request headers too large")
    return bytes(buffer)


def _parse_connect_target(target: str) -> tuple[str, int]:
    token = target.strip()
    if token.startswith("["):
        host, _, port_text = token.rpartition("]:")
        if not host or not port_text:
            raise ValueError(f"Invalid CONNECT target: {target}")
        return host[1:], int(port_text)
    if ":" not in token:
        raise ValueError(f"Invalid CONNECT target: {target}")
    host, port_text = token.rsplit(":", 1)
    return host, int(port_text)


def _proxy_address(proxy_url: str) -> tuple[str, int]:
    parsed = urlsplit(proxy_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Unsupported upstream proxy scheme: {parsed.scheme or '<empty>'}")
    if not parsed.hostname or not parsed.port:
        raise ValueError(f"Upstream proxy URL must include host and port: {proxy_url}")
    return parsed.hostname, parsed.port


async def _open_via_upstream_proxy(route: ProxyRoute, host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    assert route.upstream_proxy_url is not None
    proxy_host, proxy_port = _proxy_address(route.upstream_proxy_url)
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(proxy_host, proxy_port),
        timeout=CONNECT_TIMEOUT_SECONDS,
    )
    connect_request = (
        f"CONNECT {host}:{port} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Proxy-Connection: keep-alive\r\n\r\n"
    ).encode("ascii")
    writer.write(connect_request)
    await writer.drain()
    response = await asyncio.wait_for(_read_headers(reader), timeout=CONNECT_TIMEOUT_SECONDS)
    status_line = response.split(b"\r\n", 1)[0].decode("iso-8859-1", errors="replace")
    if " 200 " not in f" {status_line} ":
        writer.close()
        await writer.wait_closed()
        raise ConnectionError(f"Upstream proxy CONNECT failed: {status_line}")
    return reader, writer


async def _open_direct(route: ProxyRoute, host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    loop = asyncio.get_running_loop()
    addresses = await loop.getaddrinfo(host, port, family=socket.AF_INET, type=socket.SOCK_STREAM)
    last_error: Exception | None = None

    for family, socktype, proto, _, sockaddr in addresses:
        sock = socket.socket(family, socktype, proto)
        try:
            sock.setblocking(False)
            if route.bind_interface:
                bind_to_device = getattr(socket, "SO_BINDTODEVICE", None)
                if bind_to_device is None:
                    raise OSError("SO_BINDTODEVICE is unavailable")
                sock.setsockopt(socket.SOL_SOCKET, bind_to_device, route.bind_interface.encode() + b"\0")
            if route.local_address:
                sock.bind((route.local_address, 0))
            await asyncio.wait_for(loop.sock_connect(sock, sockaddr), timeout=CONNECT_TIMEOUT_SECONDS)
            return await asyncio.open_connection(sock=sock)
        except Exception as exc:
            last_error = exc
            sock.close()

    if last_error is None:
        raise ConnectionError("No addresses resolved")
    raise last_error


async def _open_remote_connection(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    errors: list[str] = []
    for route in ROUTES:
        try:
            if route.upstream_proxy_url:
                return await _open_via_upstream_proxy(route, host, port)
            return await _open_direct(route, host, port)
        except Exception as exc:
            errors.append(f"{route.name}: {exc}")
            logger.warning("Telegram egress route %s failed: %s", route.name, exc)
    raise ConnectionError("; ".join(errors) if errors else "No routes configured")


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def _respond(writer: asyncio.StreamWriter, status_line: str, body: str = "") -> None:
    payload = body.encode("utf-8")
    headers = [
        status_line,
        f"Content-Length: {len(payload)}",
        "Connection: close",
    ]
    if payload:
        headers.append("Content-Type: text/plain; charset=utf-8")
    response = ("\r\n".join(headers) + "\r\n\r\n").encode("ascii") + payload
    writer.write(response)
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    peer_host = peer[0] if isinstance(peer, tuple) and peer else None

    try:
        raw_headers = await asyncio.wait_for(_read_headers(reader), timeout=CONNECT_TIMEOUT_SECONDS)
        if not raw_headers:
            await _respond(writer, "HTTP/1.1 400 Bad Request", "empty request")
            return
        header_text = raw_headers.decode("iso-8859-1", errors="replace")
        request_line = header_text.split("\r\n", 1)[0]
        parts = request_line.split()
        if len(parts) < 2:
            await _respond(writer, "HTTP/1.1 400 Bad Request", "invalid request line")
            return
        method, target = parts[0].upper(), parts[1]

        if method == "GET" and target == "/healthz":
            await _respond(writer, "HTTP/1.1 200 OK", "ok")
            return

        if not _client_allowed(peer_host):
            logger.warning("Rejected Telegram proxy client from %s", peer_host or "unknown")
            await _respond(writer, "HTTP/1.1 403 Forbidden", "forbidden")
            return

        if method != "CONNECT":
            await _respond(writer, "HTTP/1.1 405 Method Not Allowed", "CONNECT only")
            return

        host, port = _parse_connect_target(target)
        remote_reader, remote_writer = await _open_remote_connection(host, port)
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        await asyncio.gather(
            _pipe(reader, remote_writer),
            _pipe(remote_reader, writer),
        )
    except Exception as exc:
        logger.warning("Telegram egress proxy request failed: %s", exc)
        if not writer.is_closing():
            try:
                await _respond(writer, "HTTP/1.1 502 Bad Gateway", "bad gateway")
            except Exception:
                writer.close()
                await writer.wait_closed()


async def main() -> None:
    configure_logging(
        service_name="telegram-egress-proxy",
        level=os.getenv("SERVER_MONITOR_LOG_LEVEL", "INFO"),
    )
    logger.info(
        "Starting Telegram egress proxy on %s:%s with routes=%s",
        LISTEN_HOST,
        LISTEN_PORT,
        ",".join(route.name for route in ROUTES),
    )
    server = await asyncio.start_server(_handle_client, LISTEN_HOST, LISTEN_PORT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
