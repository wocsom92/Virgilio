from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from backend.app.services.http_clients import get_monitor_http_client


class PrometheusQueryError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass(slots=True)
class PrometheusQueryValue:
    value: float
    sampled_at: datetime | None = None


def _prometheus_query_url(base_url: str) -> str:
    normalized = (base_url or "").strip().rstrip("/")
    if normalized.endswith("/api/v1/query"):
        return normalized
    return f"{normalized}/api/v1/query"


def _parse_numeric_value(raw_value: object) -> float:
    if isinstance(raw_value, (int, float)):
        return float(raw_value)
    if isinstance(raw_value, str):
        try:
            return float(raw_value.strip())
        except ValueError as exc:  # pragma: no cover - defensive guard
            raise PrometheusQueryError(f"Prometheus returned a non-numeric value: {raw_value!r}") from exc
    raise PrometheusQueryError("Prometheus returned an unexpected numeric value")


def _parse_timestamp_value(raw_value: object) -> datetime | None:
    if isinstance(raw_value, (int, float)):
        return datetime.fromtimestamp(float(raw_value), tz=timezone.utc)
    if isinstance(raw_value, str):
        try:
            return datetime.fromtimestamp(float(raw_value.strip()), tz=timezone.utc)
        except ValueError as exc:  # pragma: no cover - defensive guard
            raise PrometheusQueryError(f"Prometheus returned an invalid timestamp: {raw_value!r}") from exc
    raise PrometheusQueryError("Prometheus returned an unexpected timestamp value")


def _extract_vector_value(result: object) -> PrometheusQueryValue:
    if not isinstance(result, list):
        raise PrometheusQueryError("Prometheus returned an invalid vector result")
    if len(result) != 1:
        raise PrometheusQueryError("Prometheus query must return exactly one series")
    entry = result[0]
    if not isinstance(entry, dict):
        raise PrometheusQueryError("Prometheus returned an invalid vector sample")
    value = entry.get("value")
    if not isinstance(value, list) or len(value) != 2:
        raise PrometheusQueryError("Prometheus returned an invalid vector sample value")
    return PrometheusQueryValue(
        value=_parse_numeric_value(value[1]),
        sampled_at=_parse_timestamp_value(value[0]),
    )


def _extract_scalar_value(result: object) -> PrometheusQueryValue:
    if not isinstance(result, list) or len(result) != 2:
        raise PrometheusQueryError("Prometheus returned an invalid scalar result")
    return PrometheusQueryValue(
        value=_parse_numeric_value(result[1]),
        sampled_at=_parse_timestamp_value(result[0]),
    )


async def fetch_prometheus_query_value(base_url: str, query: str) -> PrometheusQueryValue:
    url = _prometheus_query_url(base_url)
    client = get_monitor_http_client()
    try:
        response = await client.get(url, params={"query": query})
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:200]
        raise PrometheusQueryError(
            f"Prometheus responded with {exc.response.status_code}: {body}",
            status_code=exc.response.status_code,
        ) from exc
    except httpx.RequestError as exc:
        raise PrometheusQueryError(f"Could not reach Prometheus: {exc}") from exc

    payload = response.json()
    if not isinstance(payload, dict):
        raise PrometheusQueryError("Prometheus returned an invalid JSON payload")
    if payload.get("status") != "success":
        error_text = payload.get("error") if isinstance(payload.get("error"), str) else "query failed"
        raise PrometheusQueryError(f"Prometheus query failed: {error_text}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise PrometheusQueryError("Prometheus response is missing data")

    result_type = data.get("resultType")
    result = data.get("result")
    if result_type == "vector":
        parsed = _extract_vector_value(result)
    elif result_type == "scalar":
        parsed = _extract_scalar_value(result)
    else:
        raise PrometheusQueryError(f"Unsupported Prometheus result type: {result_type!r}")
    return parsed
