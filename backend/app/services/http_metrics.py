from __future__ import annotations

import logging

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, generate_latest
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.app.core.logging import (
    bind_request_handler,
    bind_request_user_id,
    reset_request_handler,
    reset_request_user_id,
)


PROMETHEUS_PATH = "/prometheus"
_EXCLUDED_PATHS = {PROMETHEUS_PATH}
logger = logging.getLogger("backend.http")


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
    # FastAPI's router writes the matched route into the same scope dict, so it is visible here
    # once the inner app has run.
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
    """Pure-ASGI replacement for two BaseHTTPMiddleware wrappers: binds the log context, writes
    one access-log line and increments the Prometheus counter per request, without the
    per-request task and response buffering that BaseHTTPMiddleware adds."""

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
        user_token = bind_request_user_id(None)

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            summary = f"HTTP {method} {target} -> {status_code}"
            extra: dict[str, object] = {
                "handler": f"{method} {target}",
                "status_code": status_code,
                "msg_short": summary,
            }
            state = scope.get("state")
            user_id = state.get("authenticated_user_id") if isinstance(state, dict) else None
            if isinstance(user_id, int):
                extra["user_id"] = user_id
            logger.log(_access_log_level(status_code), summary, extra=extra)
            if scope.get("path") not in _EXCLUDED_PATHS:
                HTTP_REQUESTS_TOTAL.labels(
                    service=self.service_name,
                    method=method,
                    handler=_handler_name(scope),
                    status_code=str(status_code),
                    status_class=_status_class(status_code),
                ).inc()
            reset_request_user_id(user_token)
            reset_request_handler(handler_token)


def install_http_metrics(application: FastAPI, *, service_name: str) -> None:
    application.add_middleware(RequestContextMiddleware, service_name=service_name)

    @application.get(PROMETHEUS_PATH, include_in_schema=False)
    async def prometheus_metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
