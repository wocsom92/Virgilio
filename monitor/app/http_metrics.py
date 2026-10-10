from __future__ import annotations

import logging

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, generate_latest
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from monitor.app.logging import bind_request_handler, reset_request_handler


PROMETHEUS_PATH = "/prometheus"
_EXCLUDED_PATHS = {PROMETHEUS_PATH}
logger = logging.getLogger("monitor.http")


def _get_or_create_http_requests_total() -> Counter:
    try:
        return Counter(
            "server_monitor_http_requests_total",
            "Count of HTTP requests served by server_monitor services.",
            ("service", "method", "handler", "status_code", "status_class"),
        )
    except ValueError:
        collector = getattr(REGISTRY, "_names_to_collectors", {}).get("server_monitor_http_requests_total")
        if isinstance(collector, Counter):
            return collector
        raise


HTTP_REQUESTS_TOTAL = _get_or_create_http_requests_total()


def _status_class(status_code: int) -> str:
    if 100 <= status_code <= 599:
        return f"{status_code // 100}xx"
    return "unknown"


def _handler_name(scope: Scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None) or getattr(route, "path_format", None)
    if isinstance(path, str) and path:
        return path
    return "unmatched"


def _request_target(scope: Scope) -> str:
    path = scope.get("path", "")
    query = scope.get("query_string", b"")
    if query:
        return f"{path}?{query.decode('latin-1')}"
    return path


def _access_log_level(status_code: int) -> int:
    if status_code >= 400:
        return logging.WARNING
    return logging.INFO


class RequestContextMiddleware:
    """One pure-ASGI middleware for log context, access log line and Prometheus counter."""

    def __init__(self, app: ASGIApp, *, service_name: str) -> None:
        self.app = app
        self.service_name = service_name

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        target = _request_target(scope)
        status_code = 500
        handler_token = bind_request_handler(f"{method} {target}")

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            summary = f"HTTP {method} {target} -> {status_code}"
            logger.log(
                _access_log_level(status_code),
                summary,
                extra={"handler": f"{method} {target}", "status_code": status_code, "msg_short": summary},
            )
            if scope.get("path") not in _EXCLUDED_PATHS:
                HTTP_REQUESTS_TOTAL.labels(
                    service=self.service_name,
                    method=method,
                    handler=_handler_name(scope),
                    status_code=str(status_code),
                    status_class=_status_class(status_code),
                ).inc()
            reset_request_handler(handler_token)


def install_http_metrics(application: FastAPI, *, service_name: str) -> None:
    application.add_middleware(RequestContextMiddleware, service_name=service_name)

    @application.get(PROMETHEUS_PATH, include_in_schema=False)
    async def prometheus_metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
