from __future__ import annotations

import asyncio
from contextlib import suppress
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
import time
from typing import Iterable
from urllib.parse import urlsplit

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.monitors import SiteMonitor, SiteMonitorSample
from backend.app.schemas.site_monitoring import SiteMonitorCreate, SiteMonitorStatusRead
from backend.app.services.http_clients import get_monitor_http_client
from backend.app.services.system_settings import metric_retention_timedelta


_HISTORY_SEGMENT_COUNT = 48
_HISTORY_SEGMENT_DURATION = timedelta(minutes=30)
_HISTORY_WINDOW = _HISTORY_SEGMENT_DURATION * _HISTORY_SEGMENT_COUNT
_SITE_MONITOR_CHECK_INTERVAL_SECONDS = int(_HISTORY_SEGMENT_DURATION.total_seconds())
_PING_TIME_PATTERN = re.compile(r"time[=<]?\s*(\d+(?:\.\d+)?)\s*ms", re.IGNORECASE)


@dataclass(slots=True)
class SiteCheckResult:
    checked_at: datetime
    success: bool
    latency_ms: float | None
    status_code: int | None
    detail: str | None


@dataclass(slots=True)
class TcpProbeAttempt:
    port: int
    success: bool
    latency_ms: float | None
    detail: str | None


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _history_window_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    window_end = _coerce_utc(now or datetime.now(tz=timezone.utc))
    return window_end - _HISTORY_WINDOW, window_end


def _resolve_status(site_monitor: SiteMonitor, sample: SiteMonitorSample | None) -> str:
    if sample is None:
        return "unknown"
    if sample.consecutive_failures >= int(site_monitor.critical_consecutive_failures):
        return "critical"
    if sample.consecutive_failures >= int(site_monitor.warning_consecutive_failures):
        return "warn"
    return "ok"


def _format_display_value(site_monitor: SiteMonitor, sample: SiteMonitorSample | None) -> str:
    if sample is None:
        return "—"
    if sample.consecutive_failures > 0:
        if sample.consecutive_failures == 1:
            return "1 failure"
        return f"{sample.consecutive_failures} failures"
    if not sample.success:
        if site_monitor.check_type == "http" and sample.status_code is not None:
            return f"HTTP {sample.status_code}"
        return "Failed"
    if site_monitor.check_type == "http":
        if sample.status_code is not None and sample.latency_ms is not None:
            return f"{sample.status_code} · {round(sample.latency_ms):.0f} ms"
        if sample.status_code is not None:
            return f"HTTP {sample.status_code}"
    if sample.latency_ms is not None:
        return f"{round(sample.latency_ms):.0f} ms"
    return "OK"


def _history_status_from_sample(sample: SiteMonitorSample | None) -> str:
    if sample is None:
        return "unknown"
    return "ok" if sample.success else "critical"


def _site_monitor_check_interval_seconds(_: SiteMonitor | None = None) -> int:
    return _SITE_MONITOR_CHECK_INTERVAL_SECONDS


def _is_due(site_monitor: SiteMonitor, sample: SiteMonitorSample | None, now: datetime) -> bool:
    if sample is None:
        return True
    latest_checked_at = _coerce_utc(sample.checked_at)
    return (now - latest_checked_at).total_seconds() >= _site_monitor_check_interval_seconds(site_monitor)


def _parse_ping_latency(output: str, fallback_ms: float | None) -> float | None:
    match = _PING_TIME_PATTERN.search(output)
    if match:
        return float(match.group(1))
    return fallback_ms


def _resolve_ping_probe_target(target: str) -> tuple[str, list[int]]:
    normalized_target = target.strip()
    if not normalized_target:
        raise ValueError("target is empty")

    parsed = urlsplit(normalized_target if "://" in normalized_target else f"//{normalized_target}")
    host = (parsed.hostname or normalized_target).strip("[]")
    if not host:
        raise ValueError("target does not include a valid host")

    if parsed.port is not None:
        return host, [int(parsed.port)]
    if parsed.scheme == "http":
        return host, [80]
    if parsed.scheme == "https":
        return host, [443]
    return host, [443, 80]


async def _probe_tcp_endpoint(host: str, port: int, timeout_seconds: float) -> TcpProbeAttempt:
    started_at = time.perf_counter()
    writer: asyncio.StreamWriter | None = None
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout_seconds)
        return TcpProbeAttempt(
            port=port,
            success=True,
            latency_ms=max(0.0, (time.perf_counter() - started_at) * 1000),
            detail=None,
        )
    except ConnectionRefusedError:
        # A refused TCP connection still proves that the remote host is reachable.
        return TcpProbeAttempt(
            port=port,
            success=True,
            latency_ms=max(0.0, (time.perf_counter() - started_at) * 1000),
            detail=None,
        )
    except asyncio.TimeoutError:
        return TcpProbeAttempt(
            port=port,
            success=False,
            latency_ms=None,
            detail=f"timed out connecting to {host}:{port}",
        )
    except OSError as exc:
        return TcpProbeAttempt(
            port=port,
            success=False,
            latency_ms=None,
            detail=f"could not connect to {host}:{port}: {exc}",
        )
    finally:
        if writer is not None:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()


async def _run_tcp_ping_fallback(site_monitor: SiteMonitor, checked_at: datetime) -> SiteCheckResult:
    try:
        host, ports = _resolve_ping_probe_target(site_monitor.target)
    except ValueError as exc:
        return SiteCheckResult(
            checked_at=checked_at,
            success=False,
            latency_ms=None,
            status_code=None,
            detail=f"invalid target: {exc}",
        )

    timeout_seconds = max(0.1, float(site_monitor.timeout_ms) / 1000)
    attempts: list[TcpProbeAttempt] = []
    tasks = [asyncio.create_task(_probe_tcp_endpoint(host, port, timeout_seconds)) for port in ports]
    try:
        for task in asyncio.as_completed(tasks):
            attempt = await task
            attempts.append(attempt)
            if attempt.success:
                return SiteCheckResult(
                    checked_at=checked_at,
                    success=True,
                    latency_ms=attempt.latency_ms,
                    status_code=None,
                    detail=None,
                )
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    detail = next((attempt.detail for attempt in attempts if attempt.detail), None) or f"could not reach {host}"
    return SiteCheckResult(
        checked_at=checked_at,
        success=False,
        latency_ms=None,
        status_code=None,
        detail=detail[:500],
    )


def _should_use_tcp_ping_fallback(stderr_text: str) -> bool:
    lowered = stderr_text.lower()
    return (
        "operation not permitted" in lowered
        or "permission denied" in lowered
        or "not permitted" in lowered
    )


async def _run_ping_check(site_monitor: SiteMonitor, checked_at: datetime) -> SiteCheckResult:
    timeout_seconds = max(1, min(30, math.ceil(int(site_monitor.timeout_ms) / 1000)))
    started_at = time.perf_counter()
    try:
        process = await asyncio.create_subprocess_exec(
            "ping",
            "-c",
            "1",
            "-W",
            str(timeout_seconds),
            site_monitor.target,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return await _run_tcp_ping_fallback(site_monitor, checked_at)

    stdout, stderr = await process.communicate()
    elapsed_ms = max(0.0, (time.perf_counter() - started_at) * 1000)
    stdout_text = stdout.decode("utf-8", errors="ignore").strip()
    stderr_text = stderr.decode("utf-8", errors="ignore").strip()
    if process.returncode == 0:
        latency_ms = _parse_ping_latency(stdout_text, elapsed_ms)
        if latency_ms is not None and latency_ms > float(site_monitor.timeout_ms):
            return SiteCheckResult(
                checked_at=checked_at,
                success=False,
                latency_ms=latency_ms,
                status_code=None,
                detail=f"response exceeded timeout {site_monitor.timeout_ms} ms",
            )
        return SiteCheckResult(
            checked_at=checked_at,
            success=True,
            latency_ms=latency_ms,
            status_code=None,
            detail=None,
        )
    if _should_use_tcp_ping_fallback(stderr_text):
        return await _run_tcp_ping_fallback(site_monitor, checked_at)
    detail = stderr_text or stdout_text or "ping failed"
    return SiteCheckResult(
        checked_at=checked_at,
        success=False,
        latency_ms=None,
        status_code=None,
        detail=detail[:500],
    )


async def _run_http_check(site_monitor: SiteMonitor, checked_at: datetime) -> SiteCheckResult:
    client = get_monitor_http_client()
    started_at = time.perf_counter()
    try:
        response = await client.get(
            site_monitor.target,
            follow_redirects=True,
            timeout=max(0.1, float(site_monitor.timeout_ms) / 1000),
        )
        latency_ms = max(0.0, (time.perf_counter() - started_at) * 1000)
    except httpx.RequestError as exc:
        return SiteCheckResult(
            checked_at=checked_at,
            success=False,
            latency_ms=None,
            status_code=None,
            detail=f"request failed: {exc}"[:500],
        )

    expected_codes = {
        int(code)
        for code in (site_monitor.expected_status_codes or [])
        if isinstance(code, int)
    }
    if expected_codes and response.status_code not in expected_codes:
        return SiteCheckResult(
            checked_at=checked_at,
            success=False,
            latency_ms=latency_ms,
            status_code=response.status_code,
            detail=f"unexpected status {response.status_code}",
        )
    expected_substring = (site_monitor.expected_response_substring or "").strip()
    if expected_substring and expected_substring not in response.text:
        return SiteCheckResult(
            checked_at=checked_at,
            success=False,
            latency_ms=latency_ms,
            status_code=response.status_code,
            detail="response body did not contain the expected text",
        )
    if latency_ms > float(site_monitor.timeout_ms):
        return SiteCheckResult(
            checked_at=checked_at,
            success=False,
            latency_ms=latency_ms,
            status_code=response.status_code,
            detail=f"response exceeded timeout {site_monitor.timeout_ms} ms",
        )
    return SiteCheckResult(
        checked_at=checked_at,
        success=True,
        latency_ms=latency_ms,
        status_code=response.status_code,
        detail=None,
    )


async def _run_site_check(site_monitor: SiteMonitor, checked_at: datetime) -> SiteCheckResult:
    if site_monitor.check_type == "ping":
        return await _run_ping_check(site_monitor, checked_at)
    return await _run_http_check(site_monitor, checked_at)


async def _persist_site_sample(
    session: AsyncSession,
    site_monitor: SiteMonitor,
    result: SiteCheckResult,
    latest_sample: SiteMonitorSample | None,
) -> SiteMonitorSample | None:
    if latest_sample is not None and _coerce_utc(latest_sample.checked_at) >= result.checked_at:
        return None

    retention_window = await metric_retention_timedelta(session)
    cutoff = result.checked_at - retention_window
    await session.execute(
        SiteMonitorSample.__table__.delete()
        .where(SiteMonitorSample.checked_at < cutoff)
        .execution_options(synchronize_session=False)
    )
    sample = SiteMonitorSample(
        site_monitor_id=site_monitor.id,
        checked_at=result.checked_at,
        success=result.success,
        latency_ms=result.latency_ms,
        status_code=result.status_code,
        detail=result.detail,
        consecutive_failures=(0 if result.success else ((latest_sample.consecutive_failures + 1) if latest_sample is not None else 1)),
    )
    session.add(sample)
    await session.flush()
    return sample


async def _load_latest_samples(session: AsyncSession, item_ids: set[int]) -> dict[int, SiteMonitorSample]:
    if not item_ids:
        return {}

    ranked_samples = (
        select(
            SiteMonitorSample.id.label("sample_id"),
            func.row_number().over(
                partition_by=SiteMonitorSample.site_monitor_id,
                order_by=(SiteMonitorSample.checked_at.desc(), SiteMonitorSample.id.desc()),
            ).label("row_number"),
        )
        .where(SiteMonitorSample.site_monitor_id.in_(item_ids))
        .subquery()
    )
    result = await session.execute(
        select(SiteMonitorSample)
        .join(ranked_samples, SiteMonitorSample.id == ranked_samples.c.sample_id)
        .where(ranked_samples.c.row_number == 1)
    )
    return {sample.site_monitor_id: sample for sample in result.scalars()}


async def _load_history_samples(
    session: AsyncSession,
    item_ids: set[int],
    window_start: datetime,
    window_end: datetime,
) -> dict[int, list[SiteMonitorSample]]:
    if not item_ids:
        return {}

    ranked_previous_samples = (
        select(
            SiteMonitorSample.id.label("sample_id"),
            func.row_number().over(
                partition_by=SiteMonitorSample.site_monitor_id,
                order_by=(SiteMonitorSample.checked_at.desc(), SiteMonitorSample.id.desc()),
            ).label("row_number"),
        )
        .where(
            SiteMonitorSample.site_monitor_id.in_(item_ids),
            SiteMonitorSample.checked_at < window_start,
        )
        .subquery()
    )
    previous_result = await session.execute(
        select(SiteMonitorSample)
        .join(ranked_previous_samples, SiteMonitorSample.id == ranked_previous_samples.c.sample_id)
        .where(ranked_previous_samples.c.row_number == 1)
    )
    current_result = await session.execute(
        select(SiteMonitorSample)
        .where(
            SiteMonitorSample.site_monitor_id.in_(item_ids),
            SiteMonitorSample.checked_at >= window_start,
            SiteMonitorSample.checked_at <= window_end,
        )
        .order_by(SiteMonitorSample.site_monitor_id, SiteMonitorSample.checked_at, SiteMonitorSample.id)
    )

    samples_by_item: dict[int, list[SiteMonitorSample]] = {item_id: [] for item_id in item_ids}
    for sample in previous_result.scalars():
        samples_by_item.setdefault(sample.site_monitor_id, []).append(sample)
    for sample in current_result.scalars():
        samples_by_item.setdefault(sample.site_monitor_id, []).append(sample)
    for samples in samples_by_item.values():
        samples.sort(key=lambda sample: (_coerce_utc(sample.checked_at), sample.id))
    return samples_by_item


def _build_history(
    samples: list[SiteMonitorSample],
    window_start: datetime,
    window_end: datetime,
) -> list[str]:
    history = ["unknown"] * _HISTORY_SEGMENT_COUNT
    for sample in sorted(samples, key=lambda item: (_coerce_utc(item.checked_at), item.id)):
        checked_at = _coerce_utc(sample.checked_at)
        if checked_at < window_start or checked_at > window_end:
            continue
        offset_seconds = (checked_at - window_start).total_seconds()
        slot_index = min(
            _HISTORY_SEGMENT_COUNT - 1,
            max(0, int(offset_seconds // _HISTORY_SEGMENT_DURATION.total_seconds())),
        )
        history[slot_index] = _history_status_from_sample(sample)
    return history


async def list_site_monitors(session: AsyncSession, *, only_active: bool = False) -> list[SiteMonitor]:
    query = select(SiteMonitor)
    if only_active:
        query = query.where(SiteMonitor.is_active.is_(True))
    result = await session.execute(
        query.order_by(SiteMonitor.display_order, SiteMonitor.name, SiteMonitor.id)
    )
    return list(result.scalars())


async def refresh_due_site_monitor_samples(
    session: AsyncSession,
    items: Iterable[SiteMonitor],
    *,
    force: bool = False,
    now: datetime | None = None,
) -> dict[int, SiteMonitorSample]:
    items_list = list(items)
    if not items_list:
        return {}

    check_time = _coerce_utc(now or datetime.now(tz=timezone.utc))
    latest_samples = await _load_latest_samples(session, {item.id for item in items_list})
    for item in items_list:
        latest_sample = latest_samples.get(item.id)
        if not force and not _is_due(item, latest_sample, check_time):
            continue
        result = await _run_site_check(item, check_time)
        persisted_sample = await _persist_site_sample(session, item, result, latest_sample)
        if persisted_sample is not None:
            latest_samples[item.id] = persisted_sample
    return latest_samples


async def build_site_monitor_statuses(
    session: AsyncSession,
    items: Iterable[SiteMonitor],
    *,
    now: datetime | None = None,
    latest_samples: dict[int, SiteMonitorSample] | None = None,
) -> list[SiteMonitorStatusRead]:
    items_list = sorted(list(items), key=lambda item: (item.display_order, item.name.casefold(), item.id))
    if not items_list:
        return []

    check_time = _coerce_utc(now or datetime.now(tz=timezone.utc))
    history_window_start, history_window_end = _history_window_bounds(check_time)
    if latest_samples is None:
        latest_samples = await _load_latest_samples(session, {item.id for item in items_list})
    history_samples = await _load_history_samples(
        session,
        {item.id for item in items_list},
        history_window_start,
        history_window_end,
    )

    return [
        SiteMonitorStatusRead(
            id=item.id,
            name=item.name,
            check_type=item.check_type,
            target=item.target,
            status=_resolve_status(item, latest_samples.get(item.id)),
            display_value=_format_display_value(item, latest_samples.get(item.id)),
            history=_build_history(history_samples.get(item.id, []), history_window_start, history_window_end),
            checked_at=latest_samples.get(item.id).checked_at if latest_samples.get(item.id) else None,
            latency_ms=latest_samples.get(item.id).latency_ms if latest_samples.get(item.id) else None,
            status_code=latest_samples.get(item.id).status_code if latest_samples.get(item.id) else None,
            detail=latest_samples.get(item.id).detail if latest_samples.get(item.id) else None,
            consecutive_failures=latest_samples.get(item.id).consecutive_failures if latest_samples.get(item.id) else 0,
        )
        for item in items_list
    ]


async def run_due_site_monitor_checks(session: AsyncSession, *, now: datetime | None = None) -> None:
    items = await list_site_monitors(session, only_active=True)
    if not items:
        return
    await refresh_due_site_monitor_samples(session, items, now=now)
    await session.commit()


async def create_site_monitor(session: AsyncSession, payload: SiteMonitorCreate) -> SiteMonitor:
    item = SiteMonitor(
        name=payload.name,
        check_type=payload.check_type,
        target=payload.target,
        expected_status_codes=payload.expected_status_codes,
        expected_response_substring=payload.expected_response_substring,
        timeout_ms=payload.timeout_ms,
        warning_consecutive_failures=payload.warning_consecutive_failures,
        critical_consecutive_failures=payload.critical_consecutive_failures,
        check_interval_seconds=_site_monitor_check_interval_seconds(),
        display_order=payload.display_order,
        is_active=payload.is_active,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


async def update_site_monitor(session: AsyncSession, item: SiteMonitor, payload: SiteMonitorCreate) -> SiteMonitor:
    item.name = payload.name
    item.check_type = payload.check_type
    item.target = payload.target
    item.expected_status_codes = payload.expected_status_codes
    item.expected_response_substring = payload.expected_response_substring
    item.timeout_ms = payload.timeout_ms
    item.warning_consecutive_failures = payload.warning_consecutive_failures
    item.critical_consecutive_failures = payload.critical_consecutive_failures
    item.check_interval_seconds = _site_monitor_check_interval_seconds(item)
    item.display_order = payload.display_order
    item.is_active = payload.is_active
    await session.commit()
    await session.refresh(item)
    return item
