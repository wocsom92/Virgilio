from __future__ import annotations

from contextvars import ContextVar, Token
from datetime import datetime, timezone
import json
import logging
import logging.config
import re
from typing import Any
from urllib.parse import urlsplit


_RESERVED_RECORD_FIELDS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "msg_short",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}

_REQUEST_HANDLER: ContextVar[str | None] = ContextVar("backend_request_handler", default=None)
_REQUEST_USER_ID: ContextVar[int | None] = ContextVar("backend_request_user_id", default=None)
_HTTP_CLIENT_MESSAGE_RE = re.compile(r'^HTTP Request: ([A-Z]+) (\S+) "HTTP/\d+(?:\.\d+)? (\d{3}) [^"]+"$')
_SENSITIVE_TEXT_RE = re.compile(r"(/bot)[^/\s\"]+")


def bind_request_handler(handler: str | None) -> Token[str | None]:
    normalized = handler.strip() if handler else None
    return _REQUEST_HANDLER.set(normalized or None)


def reset_request_handler(token: Token[str | None]) -> None:
    _REQUEST_HANDLER.reset(token)


def bind_request_user_id(user_id: int | None) -> Token[int | None]:
    return _REQUEST_USER_ID.set(user_id)


def set_request_user_id(user_id: int | None) -> None:
    _REQUEST_USER_ID.set(user_id)


def reset_request_user_id(token: Token[int | None]) -> None:
    _REQUEST_USER_ID.reset(token)


def _iso_timestamp(created: float) -> str:
    return datetime.fromtimestamp(created, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _normalize_level(level_name: str) -> str:
    normalized = str(level_name or "INFO").upper()
    if normalized in {"WARN", "WARNING"}:
        return "warn"
    return normalized.lower()


def _redact_sensitive_text(value: str) -> str:
    return _SENSITIVE_TEXT_RE.sub(r"\1<redacted>", value)


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_sensitive_text(value)
    return value


def _resolve_handler(record: logging.LogRecord) -> str:
    explicit_handler = getattr(record, "handler", None)
    if explicit_handler:
        return _redact_sensitive_text(str(explicit_handler))

    scoped_handler = _REQUEST_HANDLER.get()
    if scoped_handler:
        return _redact_sensitive_text(scoped_handler)

    if record.name == "uvicorn.access" and isinstance(record.args, tuple) and len(record.args) >= 3:
        method, path = record.args[1], record.args[2]
        if isinstance(method, str) and isinstance(path, str):
            return _redact_sensitive_text(f"{method} {path}")

    return "background"


def _resolve_short_message(record: logging.LogRecord) -> str:
    explicit_message = getattr(record, "msg_short", None)
    if explicit_message:
        return str(explicit_message)

    if record.name == "uvicorn.access" and isinstance(record.args, tuple) and len(record.args) >= 5:
        method, path, status_code = record.args[1], record.args[2], record.args[4]
        if isinstance(method, str) and isinstance(path, str):
            return f"HTTP {method} {path} -> {status_code}"

    rendered_message = _redact_sensitive_text(record.getMessage().strip())
    if not rendered_message:
        return "log event"

    http_client_match = _HTTP_CLIENT_MESSAGE_RE.match(rendered_message)
    if http_client_match:
        method, url, status_code = http_client_match.groups()
        parsed_url = urlsplit(url)
        target = parsed_url.path or "/"
        if parsed_url.query:
            target = f"{target}?{parsed_url.query}"
        return f"Outgoing HTTP {method} {target} -> {status_code}"

    if ": " in rendered_message:
        return rendered_message.split(": ", 1)[0]

    if len(rendered_message) > 120:
        return f"{rendered_message[:117]}..."

    return rendered_message


def _resolve_status_code(record: logging.LogRecord) -> int | None:
    explicit_status = getattr(record, "status_code", None)
    if explicit_status is not None:
        try:
            return int(explicit_status)
        except (TypeError, ValueError):
            return None

    if record.name == "uvicorn.access" and isinstance(record.args, tuple) and len(record.args) >= 5:
        status_code = record.args[4]
        try:
            return int(status_code)
        except (TypeError, ValueError):
            return None

    return None


class JsonFormatter(logging.Formatter):
    def __init__(self, service_name: str):
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "@timestamp": _iso_timestamp(record.created),
            "handler": _resolve_handler(record),
            "level": _normalize_level(record.levelname),
            "msg": _resolve_short_message(record),
            "message": _redact_sensitive_text(record.getMessage()),
            "logger_name": record.name,
            "service_name": getattr(record, "service_name", self.service_name),
            "source_module": record.module,
            "source_line": record.lineno,
            "process_id": record.process,
            "thread_name": record.threadName,
        }
        request_user_id = _REQUEST_USER_ID.get()
        if request_user_id is not None:
            payload["user_id"] = request_user_id
        status_code = _resolve_status_code(record)
        if status_code is not None:
            payload["status_code"] = status_code

        for key, value in record.__dict__.items():
            if key.startswith("_") or key in _RESERVED_RECORD_FIELDS:
                continue
            payload[key] = _redact_value(value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(payload, default=str, ensure_ascii=True)


def build_logging_config(service_name: str, level: str = "INFO") -> dict[str, Any]:
    normalized_level = str(level or "INFO").upper()
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {
                "()": "backend.app.core.logging.JsonFormatter",
                "service_name": service_name,
            }
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "json",
                "stream": "ext://sys.stdout",
            }
        },
        "root": {
            "handlers": ["default"],
            "level": normalized_level,
        },
        "loggers": {
            "uvicorn": {
                "handlers": ["default"],
                "level": normalized_level,
                "propagate": False,
            },
            "uvicorn.error": {
                "handlers": ["default"],
                "level": normalized_level,
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["default"],
                "level": normalized_level,
                "propagate": False,
            },
        },
    }


def configure_logging(service_name: str, level: str = "INFO") -> None:
    logging.config.dictConfig(build_logging_config(service_name=service_name, level=level))
