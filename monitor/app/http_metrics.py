from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, generate_latest
from starlette.responses import Response as StarletteResponse


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


def _handler_name(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None) or getattr(route, "path_format", None)
    if isinstance(path, str) and path:
        return path
    return "unmatched"


def _request_target(request: Request) -> str:
    query = request.url.query
    if query:
        return f"{request.url.path}?{query}"
    return request.url.path


def _access_log_level(status_code: int) -> int:
    if status_code >= 400:
        return logging.WARNING
    return logging.INFO


def install_http_metrics(application: FastAPI, *, service_name: str) -> None:
    @application.middleware("http")
    async def collect_http_metrics(
        request: Request,
        call_next: Callable[[Request], Awaitable[StarletteResponse]],
    ) -> StarletteResponse:
        should_record_metrics = request.url.path not in _EXCLUDED_PATHS
        status_code = 500
        target = _request_target(request)
        summary = f"HTTP {request.method} {target} -> {status_code}"
        try:
            response = await call_next(request)
            status_code = response.status_code
            summary = f"HTTP {request.method} {target} -> {status_code}"
            return response
        finally:
            logger.log(
                _access_log_level(status_code),
                summary,
                extra={
                    "handler": f"{request.method} {target}",
                    "status_code": status_code,
                    "msg_short": summary,
                },
            )
            if should_record_metrics:
                HTTP_REQUESTS_TOTAL.labels(
                    service=service_name,
                    method=request.method,
                    handler=_handler_name(request),
                    status_code=str(status_code),
                    status_class=_status_class(status_code),
                ).inc()

    @application.get(PROMETHEUS_PATH, include_in_schema=False)
    async def prometheus_metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
