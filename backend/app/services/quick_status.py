from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable
import asyncio
import math

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, undefer

from backend.app.bot import health as bot_health
from backend.app.core.config import settings
from backend.app.models.monitors import (
    MetricSnapshot,
    QuickStatusItem,
    QuickStatusPingSample,
    QuickStatusPrometheusSample,
    QuickStatusStatusEvent,
)
from backend.app.schemas.quick_status import (
    QuickStatusDetailLine,
    QuickStatusItemCreate,
    QuickStatusSeriesPoint,
    QuickStatusSeriesSummary,
    QuickStatusTileRead,
    is_supported_quick_status_metric,
)
from backend.app.services.monitor_client import MonitorClientError, fetch_ping
from backend.app.services.prometheus_client import (
    PrometheusQueryError,
    fetch_prometheus_query_value,
)
from backend.app.services.telegram_settings import get_or_create_settings


_PERCENT_METRICS = {"disk_usage_percent", "ram_used_percent", "swap_used_percent", "mount_used_percent"}
_REVERSE_THRESHOLD_METRICS = {"last_restart", "memory_available_gb", "mount_available_gb", "ssh_last_unsuccessful_attempt"}
_PING_METRICS = {"ping_result", "ping_delay_ms"}
_PROMETHEUS_METRICS = {"prometheus_query"}
_INFO_ONLY_METRICS = {"swap_used_percent", "docker_container_count"}  # shown, never alerting
# Metrics whose value lives in the (deferred) raw_payload JSON column.
_RAW_PAYLOAD_METRICS = {
    "mount_available_gb",
    "ssh_last_successful_login",
    "ssh_last_unsuccessful_attempt",
    "ssh_status",
    "telegram_bot_status",
    "wifi_status",
    "vpn_status",
}
_ALERT_STATUSES = {"warn", "critical"}
_HISTORY_SEGMENT_COUNT = 12
_HISTORY_SEGMENT_DURATION = timedelta(hours=2)
_HISTORY_WINDOW = _HISTORY_SEGMENT_DURATION * _HISTORY_SEGMENT_COUNT
_TILE_STALE_AFTER = timedelta(minutes=30)
_HISTORY_STATUS_PRIORITY = {
    "critical": 4,
    "warn": 3,
    "unknown": 2,
    "info": 1,
    "ok": 1,
}


def _sync_quick_status_alert_condition_started_at(item: QuickStatusItem, raw_status: str, now: datetime) -> None:
    if raw_status in _ALERT_STATUSES:
        if item.alert_condition_started_at is None:
            item.alert_condition_started_at = now
    else:
        item.alert_condition_started_at = None


def _effective_quick_status_display_status(
    raw_status: str,
    item: QuickStatusItem,
    *,
    now: datetime,
    confirmation_minutes: int,
) -> str:
    if raw_status not in _ALERT_STATUSES:
        return raw_status
    if confirmation_minutes <= 0:
        return raw_status
    started = item.alert_condition_started_at
    if started is None:
        return "ok"
    if now - _coerce_utc(started) >= timedelta(minutes=confirmation_minutes):
        return raw_status
    return "ok"


async def _apply_quick_status_alert_suppression_to_tiles(
    session: AsyncSession,
    items_list: list[QuickStatusItem],
    tiles: list[QuickStatusTileRead],
    *,
    now: datetime,
    persist: bool = False,
) -> list[QuickStatusTileRead]:
    """Hide warn/critical until the condition lasted the configured confirmation time.

    With ``persist=False`` (dashboard reads) the stored ``alert_condition_started_at`` is only
    read; the background poller and ingest paths call with ``persist=True`` and own the writes.
    """
    settings_model = await get_or_create_settings(session)
    confirmation_minutes = max(0, int(settings_model.quick_status_alert_confirmation_minutes or 0))
    items_by_id = {item.id: item for item in items_list}
    result: list[QuickStatusTileRead] = []
    for tile in tiles:
        if tile.id <= 0:
            result.append(tile)
            continue
        item = items_by_id.get(tile.id)
        if item is None:
            result.append(tile)
            continue
        raw_status = tile.status
        if persist:
            _sync_quick_status_alert_condition_started_at(item, raw_status, now)
            session.add(item)
        effective = _effective_quick_status_display_status(
            raw_status,
            item,
            now=now,
            confirmation_minutes=confirmation_minutes,
        )
        if effective != raw_status:
            result.append(tile.model_copy(update={"status": effective}))
        else:
            result.append(tile)
    return result


@dataclass(slots=True)
class PingCheckResult:
    checked_at: datetime
    success: bool
    latency_ms: float | None


@dataclass(slots=True)
class PrometheusCheckResult:
    checked_at: datetime
    value: float | None
    sampled_at: datetime | None = None
    error_message: str | None = None


@dataclass(slots=True)
class QuickStatusTransition:
    item_id: int
    backend_id: int
    backend_name: str
    label: str
    metric_key: str
    previous_status: str
    current_status: str
    display_value: str


@dataclass(slots=True)
class QuickStatusEvaluation:
    value: float | None
    display_value: str
    status: str


_PING_CACHE: dict[tuple[int, int | None, str], PingCheckResult] = {}
_PROMETHEUS_CACHE: dict[tuple[int, int | None, str, str], PrometheusCheckResult] = {}
_NETWORK_CHECK_CONCURRENCY = 8
# Last status written to quick_status_status_events per item (primed from the DB on first use).
_LAST_STATUS: dict[int, str] = {}


def clear_quick_status_caches() -> None:
    """Drop in-memory ping/Prometheus results and the status-event cursor (tests, admin edits)."""
    _PING_CACHE.clear()
    _PROMETHEUS_CACHE.clear()
    _LAST_STATUS.clear()


def _is_metric_item(item: QuickStatusItem) -> bool:
    return item.metric_key not in _PING_METRICS and item.metric_key not in _PROMETHEUS_METRICS


def _loaded_relation(item: QuickStatusItem, relation_name: str):
    state = getattr(item, "__dict__", None)
    if not isinstance(state, dict):
        return None
    return state.get(relation_name)


def _ping_cache_key(item: QuickStatusItem) -> tuple[int, int | None, str]:
    return (item.id, item.backend_id, item.ping_endpoint or "")


def _prometheus_cache_key(item: QuickStatusItem) -> tuple[int, int | None, str, str]:
    return (item.id, item.backend_id, item.prometheus_url or "", item.prometheus_query or "")


def _quick_status_item_sort_key(item: QuickStatusItem) -> tuple[int, str, int, int, int]:
    backend = _loaded_relation(item, "backend")
    backend_order = backend.display_order if backend is not None else 0
    backend_name = backend.name if backend is not None else ""
    return (
        backend_order,
        backend_name.casefold(),
        item.backend_id or 0,
        item.display_order,
        item.id,
    )


def _extract_mount_used_percent(snapshot: MetricSnapshot, mount_path: str | None) -> float | None:
    if not mount_path:
        return None
    mounts = snapshot.mounted_usage or []
    if not isinstance(mounts, list):
        return None
    for entry in mounts:
        if isinstance(entry, dict) and entry.get("mount_point") == mount_path:
            value = entry.get("used_percent")
            return float(value) if isinstance(value, (int, float)) else None
    return None


def _extract_mount_available_gb(snapshot: MetricSnapshot, mount_path: str | None) -> float | None:
    if not mount_path:
        return None
    mounts = snapshot.mounted_usage or []
    if not isinstance(mounts, list):
        mounts = []
    for entry in mounts:
        if not isinstance(entry, dict) or entry.get("mount_point") != mount_path:
            continue
        total_gb = entry.get("total_gb")
        used_percent = entry.get("used_percent")
        if not isinstance(total_gb, (int, float)) or not isinstance(used_percent, (int, float)):
            return None
        free_gb = float(total_gb) * max(0.0, min(100.0, 100.0 - float(used_percent))) / 100.0
        return free_gb
    if mount_path == "/":
        return _extract_raw_payload_value(snapshot, "disk_available_gb")
    return None


def _extract_cpu_load_one(snapshot: MetricSnapshot) -> float | None:
    payload = snapshot.cpu_load or {}
    if isinstance(payload, dict):
        value = payload.get("one")
        return float(value) if isinstance(value, (int, float)) else None
    return None


def _extract_cpu_load_five(snapshot: MetricSnapshot) -> float | None:
    payload = snapshot.cpu_load or {}
    if isinstance(payload, dict):
        value = payload.get("five")
        return float(value) if isinstance(value, (int, float)) else None
    return None


def _extract_cpu_load_fifteen(snapshot: MetricSnapshot) -> float | None:
    payload = snapshot.cpu_load or {}
    if isinstance(payload, dict):
        value = payload.get("fifteen")
        return float(value) if isinstance(value, (int, float)) else None
    return None


def _extract_uptime_hours(snapshot: MetricSnapshot) -> float | None:
    if snapshot.uptime_seconds is None:
        return None
    return float(snapshot.uptime_seconds) / 3600


def _extract_running_container_count(snapshot: MetricSnapshot) -> float | None:
    if snapshot.docker_container_count is not None:
        return float(snapshot.docker_container_count)
    containers = snapshot.docker_running_containers
    if not isinstance(containers, list):
        return None
    names = [name for name in containers if isinstance(name, str) and name.strip()]
    return float(len(names))


def _extract_raw_payload_value(snapshot: MetricSnapshot, key: str) -> float | None:
    payload = snapshot.raw_payload if isinstance(snapshot.raw_payload, dict) else {}
    raw = payload.get(key)
    return float(raw) if isinstance(raw, (int, float)) else None


def _telegram_bot_evaluation(snapshot: MetricSnapshot) -> tuple[int | None, list[tuple[str, str]]] | None:
    """Bot status from this backend's own polling state, or None when the bot is not run here.

    The agent only knows whether the egress proxy container on its host is up; that is folded in
    when the backend is configured to send through the proxy.
    """
    health = bot_health.get_bot_health()
    if not health.enabled:
        return None
    payload = snapshot.raw_payload if isinstance(snapshot.raw_payload, dict) else {}
    proxy_running = payload.get("telegram_egress_proxy_running")
    return bot_health.evaluate_bot_health(
        health,
        proxy_configured=bool(settings.telegram_proxy_url),
        proxy_running=proxy_running if isinstance(proxy_running, bool) else None,
    )


def _telegram_bot_level(snapshot: MetricSnapshot) -> float | None:
    evaluation = _telegram_bot_evaluation(snapshot)
    if evaluation is None:
        # Bot not run by this backend: fall back to whatever the agent reported (legacy setups).
        return _extract_raw_payload_value(snapshot, "telegram_bot_status_level")
    level, _lines = evaluation
    return float(level) if level is not None else None


def _metric_value(snapshot: MetricSnapshot, metric_key: str, mount_path: str | None) -> float | None:
    if metric_key == "disk_usage_percent":
        return float(snapshot.disk_usage_percent) if snapshot.disk_usage_percent is not None else None
    if metric_key == "ram_used_percent":
        return float(snapshot.ram_used_percent) if snapshot.ram_used_percent is not None else None
    if metric_key == "cpu_temperature_c":
        return float(snapshot.cpu_temperature_c) if snapshot.cpu_temperature_c is not None else None
    if metric_key == "memory_available_gb":
        return float(snapshot.memory_available_gb) if snapshot.memory_available_gb is not None else None
    if metric_key == "swap_used_percent":
        return float(snapshot.swap_used_percent) if snapshot.swap_used_percent is not None else None
    if metric_key == "docker_container_count":
        return _extract_running_container_count(snapshot)
    if metric_key == "cpu_load_one":
        return _extract_cpu_load_one(snapshot)
    if metric_key == "cpu_load_five":
        return _extract_cpu_load_five(snapshot)
    if metric_key == "cpu_load_fifteen":
        return _extract_cpu_load_fifteen(snapshot)
    if metric_key == "mount_used_percent":
        return _extract_mount_used_percent(snapshot, mount_path)
    if metric_key == "mount_available_gb":
        return _extract_mount_available_gb(snapshot, mount_path)
    if metric_key == "last_restart":
        return _extract_uptime_hours(snapshot)
    if metric_key == "ssh_last_successful_login":
        return _extract_raw_payload_value(snapshot, "ssh_last_successful_login_seconds")
    if metric_key == "ssh_last_unsuccessful_attempt":
        seconds = _extract_raw_payload_value(snapshot, "ssh_last_unsuccessful_attempt_seconds")
        return (seconds / 3600) if seconds is not None else None
    if metric_key == "ssh_status":
        return _extract_raw_payload_value(snapshot, "ssh_status_level")
    if metric_key == "telegram_bot_status":
        return _telegram_bot_level(snapshot)
    if metric_key == "wifi_status":
        return _extract_raw_payload_value(snapshot, "wifi_status_level")
    if metric_key == "vpn_status":
        return _extract_raw_payload_value(snapshot, "vpn_status_level")
    return None


def _format_compact_number(value: float) -> str:
    if math.isfinite(value) and float(value).is_integer():
        return str(int(value))
    absolute = abs(value)
    if 0.01 <= absolute < 100:
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return format(value, ".4g")


def _format_value(metric_key: str, value: float | None) -> str:
    if value is None:
        return "—"
    if metric_key in _PERCENT_METRICS:
        return f"{value:.0f}%"
    if metric_key == "cpu_temperature_c":
        return f"{value:.1f} °C"
    if metric_key in {"memory_available_gb", "mount_available_gb"}:
        return _format_binary_size_from_gib(value)
    if metric_key == "docker_container_count":
        return f"{int(round(value))}"
    if metric_key == "last_restart":
        return _format_uptime_hours(value)
    if metric_key == "ping_delay_ms":
        return f"{value:.0f}ms"
    if metric_key in {"ssh_last_successful_login", "ssh_last_unsuccessful_attempt"}:
        hours = value / 3600 if metric_key == "ssh_last_successful_login" else value
        return _format_uptime_hours(hours)
    if metric_key in {"ssh_status", "telegram_bot_status", "wifi_status", "vpn_status"}:
        level = int(round(value))
        if level <= 0:
            return "OK"
        if level == 1:
            return "WARN"
        return "CRIT"
    if metric_key in _PROMETHEUS_METRICS:
        return _format_compact_number(value)
    return f"{value:.2f}"


def _format_prometheus_unit_value(value: float, display_unit: str | None) -> str:
    if not display_unit:
        return _format_compact_number(value)
    compact = display_unit.strip()
    if compact == "%":
        return f"{value:.0f}%"
    if compact == "°C":
        return f"{value:.1f} °C"
    if compact in {"ms", "s", "m", "h"}:
        return f"{_format_compact_number(value)}{compact}"
    return f"{_format_compact_number(value)} {compact}"


def _format_item_value(item: QuickStatusItem, value: float | None) -> str:
    if getattr(item, "display_variant", "tile") == "temperature":
        if value is None:
            return "—"
        return f"{value:.1f} °C"
    if getattr(item, "display_variant", "tile") == "humidity":
        if value is None:
            return "—"
        return f"{value:.0f}%"
    if item.metric_key in _PROMETHEUS_METRICS and value is not None:
        return _format_prometheus_unit_value(value, getattr(item, "display_unit", None))
    return _format_value(item.metric_key, value)


def _format_binary_size_from_gib(value_gib: float) -> str:
    if value_gib >= 1024:
        return f"{round(value_gib / 1024):.0f} TiB"
    if value_gib >= 1:
        return f"{round(value_gib):.0f} GiB"
    value_mib = value_gib * 1024
    if value_mib >= 1:
        return f"{round(value_mib):.0f} MiB"
    return f"{round(value_mib * 1024):.0f} KiB"


def _detail_severity_for_status(status: str) -> str:
    if status == "critical":
        return "critical"
    if status == "warn":
        return "warn"
    return "ok"


def _format_ip_detail(ip_value: str, country_code: Any, probe_service: Any) -> str:
    country_suffix = (
        f" ({country_code.strip().upper()})"
        if isinstance(country_code, str) and country_code.strip()
        else ""
    )
    service_suffix = (
        f" via {probe_service.strip()}"
        if isinstance(probe_service, str) and probe_service.strip()
        else ""
    )
    return f"{ip_value.strip()}{country_suffix}{service_suffix}"


def _build_detail_lines(
    item: QuickStatusItem,
    snapshot: MetricSnapshot | None,
    *,
    status: str,
    prometheus_result: PrometheusCheckResult | None = None,
    prometheus_summary: QuickStatusSeriesSummary | None = None,
    now: datetime | None = None,
) -> list[QuickStatusDetailLine] | None:
    if item.metric_key in _PROMETHEUS_METRICS:
        lines: list[QuickStatusDetailLine] = []
        severity = _detail_severity_for_status(status)
        if item.prometheus_url:
            lines.append(QuickStatusDetailLine(text=f"Endpoint: {item.prometheus_url}", severity=severity))
        if item.prometheus_query:
            lines.append(QuickStatusDetailLine(text=f"Query: {item.prometheus_query}", severity=severity))
        if prometheus_result is not None and prometheus_result.sampled_at is not None:
            is_stale = _is_prometheus_result_stale(prometheus_result, now=now)
            lines.append(
                QuickStatusDetailLine(
                    text=(
                        "Last update: "
                        f"{prometheus_result.sampled_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
                    ),
                    severity="warn" if is_stale else severity,
                )
            )
            if is_stale:
                lines.append(
                    QuickStatusDetailLine(
                        text="Stale: no Prometheus update in the last 30 minutes",
                        severity="warn",
                    )
                )
        if prometheus_result is not None and prometheus_result.error_message:
            lines.append(
                QuickStatusDetailLine(
                    text=f"Error: {prometheus_result.error_message}",
                    severity="warn" if status == "unknown" else severity,
                )
            )
        if getattr(item, "display_variant", "tile") in {"graph", "temperature"} and prometheus_summary is not None:
            if prometheus_summary.min_value is not None:
                lines.append(
                    QuickStatusDetailLine(
                        text=f"24h low: {_format_item_value(item, prometheus_summary.min_value)}",
                        severity=severity,
                    )
                )
            if prometheus_summary.max_value is not None:
                lines.append(
                    QuickStatusDetailLine(
                        text=f"24h high: {_format_item_value(item, prometheus_summary.max_value)}",
                        severity=severity,
                    )
                )
            if prometheus_summary.average_value is not None:
                lines.append(
                    QuickStatusDetailLine(
                        text=f"24h avg: {_format_item_value(item, prometheus_summary.average_value)}",
                        severity=severity,
                    )
                )
            if prometheus_summary.delta_value is not None:
                direction = "change"
                delta_text = _format_item_value(item, abs(prometheus_summary.delta_value))
                sign = "+" if prometheus_summary.delta_value > 0 else ("-" if prometheus_summary.delta_value < 0 else "±")
                lines.append(
                    QuickStatusDetailLine(
                        text=f"24h {direction}: {sign}{delta_text}",
                        severity=severity,
                    )
                )
        return lines or None

    if snapshot is None:
        return None
    payload = snapshot.raw_payload if isinstance(snapshot.raw_payload, dict) else {}
    if item.metric_key == "ssh_last_unsuccessful_attempt":
        method = payload.get("ssh_last_failure_auth_method")
        username = payload.get("ssh_last_failure_username")
        source_ip = payload.get("ssh_last_failure_source_ip")
        port = payload.get("ssh_last_failure_port")
        raw_line = payload.get("ssh_last_failure_line")
        severity = _detail_severity_for_status(status)

        lines: list[QuickStatusDetailLine] = []
        if isinstance(method, str) and method.strip():
            lines.append(QuickStatusDetailLine(text=f"Method: {method.strip()}", severity=severity))
        if isinstance(username, str) and username.strip():
            lines.append(QuickStatusDetailLine(text=f"User: {username.strip()}", severity=severity))
        if isinstance(source_ip, str) and source_ip.strip():
            lines.append(QuickStatusDetailLine(text=f"Source: {source_ip.strip()}", severity=severity))
        if isinstance(port, int):
            lines.append(QuickStatusDetailLine(text=f"Port: {port}", severity=severity))
        if isinstance(raw_line, str) and raw_line.strip():
            lines.append(QuickStatusDetailLine(text=f"Log: {raw_line.strip()}", severity=severity))
        return lines or None

    if item.metric_key == "telegram_bot_status":
        evaluation = _telegram_bot_evaluation(snapshot)
        if evaluation is not None:
            _level, health_lines = evaluation
            return [QuickStatusDetailLine(text=text, severity=line_severity) for text, line_severity in health_lines] or None
        bot_running = payload.get("telegram_bot_running")
        egress_proxy_running = payload.get("telegram_egress_proxy_running")
        lines = []
        if isinstance(bot_running, bool):
            lines.append(
                QuickStatusDetailLine(
                    text=f"Bot container: {'running' if bot_running else 'stopped'}",
                    severity="ok" if bot_running else "critical",
                )
            )
        if isinstance(egress_proxy_running, bool):
            lines.append(
                QuickStatusDetailLine(
                    text=f"Egress proxy: {'running' if egress_proxy_running else 'stopped'}",
                    severity="ok" if egress_proxy_running else "warn",
                )
            )
        return lines or None

    if item.metric_key == "wifi_status":
        ssid = payload.get("wifi_ssid")
        interface = payload.get("wifi_interface")
        client_count = payload.get("wifi_client_count")
        lease_count = payload.get("wifi_dhcp_lease_count")
        operstate = payload.get("wifi_operstate")
        hostapd_socket_present = payload.get("wifi_hostapd_socket_present")
        lines: list[QuickStatusDetailLine] = []
        if isinstance(ssid, str) and ssid.strip():
            lines.append(QuickStatusDetailLine(text=f"SSID: {ssid.strip()}", severity="ok"))
        if isinstance(interface, str) and interface.strip():
            lines.append(QuickStatusDetailLine(text=f"Interface: {interface.strip()}", severity="ok"))
        if isinstance(operstate, str) and operstate.strip():
            lines.append(
                QuickStatusDetailLine(
                    text=f"Operstate: {operstate.strip()}",
                    severity="ok" if operstate.strip() == "up" else "critical",
                )
            )
        if isinstance(hostapd_socket_present, bool):
            lines.append(
                QuickStatusDetailLine(
                    text=f"hostapd socket: {'present' if hostapd_socket_present else 'missing'}",
                    severity="ok" if hostapd_socket_present else "critical",
                )
            )
        if isinstance(client_count, int):
            lines.append(QuickStatusDetailLine(text=f"Associated clients: {client_count}", severity="ok"))
        clients_raw = payload.get("wifi_associated_clients")
        if isinstance(clients_raw, list):
            _wifi_detail_cap = 24
            valid_clients: list[dict[str, object]] = []
            for entry in clients_raw:
                if not isinstance(entry, dict):
                    continue
                mac = entry.get("mac")
                if not isinstance(mac, str) or not mac.strip():
                    continue
                valid_clients.append(entry)
            for entry in valid_clients[:_wifi_detail_cap]:
                mac = str(entry.get("mac", "")).strip()
                sig = entry.get("signal_dbm")
                if isinstance(sig, int):
                    lines.append(QuickStatusDetailLine(text=f"Client: {mac} · {sig} dBm", severity="ok"))
                else:
                    lines.append(QuickStatusDetailLine(text=f"Client: {mac}", severity="ok"))
            remaining = len(valid_clients) - _wifi_detail_cap
            if remaining > 0:
                lines.append(QuickStatusDetailLine(text=f"… and {remaining} more", severity="ok"))
        if isinstance(lease_count, int):
            lines.append(QuickStatusDetailLine(text=f"DHCP leases: {lease_count}", severity="ok"))
        return lines or None

    if item.metric_key == "vpn_status":
        active_backend = payload.get("vpn_active_backend")
        hotspot_public_ip = payload.get("vpn_hotspot_public_ip")
        hotspot_probe_service = payload.get("vpn_hotspot_probe_service")
        hotspot_country_code = payload.get("vpn_hotspot_country_code")
        expected_public_ip = payload.get("vpn_expected_public_ip")
        expected_probe_service = payload.get("vpn_expected_probe_service")
        expected_country_code = payload.get("vpn_expected_country_code")
        default_public_ip = payload.get("vpn_default_public_ip")
        default_probe_service = payload.get("vpn_default_probe_service")
        default_country_code = payload.get("vpn_default_country_code")
        lines = []
        if isinstance(active_backend, str) and active_backend.strip():
            lines.append(QuickStatusDetailLine(text=f"Active backend: {active_backend.strip()}", severity="ok"))
        if isinstance(hotspot_public_ip, str) and hotspot_public_ip.strip():
            severity = "ok" if hotspot_public_ip == expected_public_ip else _detail_severity_for_status(status)
            lines.append(
                QuickStatusDetailLine(
                    text=f"Hotspot egress IP: {_format_ip_detail(hotspot_public_ip, hotspot_country_code, hotspot_probe_service)}",
                    severity=severity,
                )
            )
        if isinstance(expected_public_ip, str) and expected_public_ip.strip():
            lines.append(
                QuickStatusDetailLine(
                    text=f"Backend egress IP: {_format_ip_detail(expected_public_ip, expected_country_code, expected_probe_service)}",
                    severity="ok",
                )
            )
        if isinstance(default_public_ip, str) and default_public_ip.strip():
            severity = "warn" if default_public_ip == hotspot_public_ip else "ok"
            lines.append(
                QuickStatusDetailLine(
                    text=f"Host default IP: {_format_ip_detail(default_public_ip, default_country_code, default_probe_service)}",
                    severity=severity,
                )
            )
        return lines or None

    if item.metric_key != "ssh_status":
        return None
    pubkey_enabled = payload.get("ssh_pubkey_auth_enabled")
    password_auth_disabled = payload.get("ssh_password_auth_disabled")
    kbd_interactive_disabled = payload.get("ssh_kbd_interactive_auth_disabled")
    permit_root_mode = payload.get("ssh_permit_root_login_mode")
    pubkey_line = payload.get("ssh_pubkey_auth_line")
    password_auth_line = payload.get("ssh_password_auth_line")
    kbd_interactive_line = payload.get("ssh_kbd_interactive_auth_line")
    permit_root_line = payload.get("ssh_permit_root_login_line")

    lines: list[QuickStatusDetailLine] = []

    if isinstance(pubkey_line, str) and pubkey_line.strip():
        lines.append(
            QuickStatusDetailLine(
                text=pubkey_line.strip(),
                severity="ok" if pubkey_enabled is True else "warn",
            )
        )
    if isinstance(password_auth_line, str) and password_auth_line.strip():
        lines.append(
            QuickStatusDetailLine(
                text=password_auth_line.strip(),
                severity="ok" if password_auth_disabled is True else "warn",
            )
        )
    if isinstance(kbd_interactive_line, str) and kbd_interactive_line.strip():
        lines.append(
            QuickStatusDetailLine(
                text=kbd_interactive_line.strip(),
                severity="ok" if kbd_interactive_disabled is True else "warn",
            )
        )
    if isinstance(permit_root_line, str) and permit_root_line.strip():
        permit_root_token = permit_root_mode.strip().lower() if isinstance(permit_root_mode, str) else ""
        if permit_root_token == "no":
            severity = "ok"
        elif permit_root_token in {"prohibit-password", "without-password", "forced-commands-only"}:
            severity = "warn"
        else:
            severity = "critical"
        lines.append(QuickStatusDetailLine(text=permit_root_line.strip(), severity=severity))

    return lines or None


def _format_uptime_hours(value: float) -> str:
    total_minutes = int(round(value * 60))
    days, rem_minutes = divmod(total_minutes, 1440)
    hours, minutes = divmod(rem_minutes, 60)
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return f"{minutes}m"


def _format_elapsed_seconds(value: float) -> str:
    total = max(0, int(round(value)))
    days, rem = divmod(total, 86_400)
    hours, rem = divmod(rem, 3_600)
    minutes, seconds = divmod(rem, 60)
    return f"{days:02d}:{hours:02d}:{minutes:02d}:{seconds:02d}"


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _history_window_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    window_end = _coerce_utc(now or datetime.now(tz=timezone.utc))
    return window_end - _HISTORY_WINDOW, window_end


def _empty_history() -> list[str]:
    return ["unknown"] * _HISTORY_SEGMENT_COUNT


def _history_segment_ranges(window_start: datetime) -> list[tuple[datetime, datetime]]:
    return [
        (
            window_start + (_HISTORY_SEGMENT_DURATION * index),
            window_start + (_HISTORY_SEGMENT_DURATION * (index + 1)),
        )
        for index in range(_HISTORY_SEGMENT_COUNT)
    ]


def _dominant_status(durations: dict[str, float]) -> str:
    if not durations:
        return "unknown"
    return max(
        durations.items(),
        key=lambda entry: (
            entry[1],
            _HISTORY_STATUS_PRIORITY.get(entry[0], 0),
        ),
    )[0]


def _accumulate_status_durations(
    buckets: list[dict[str, float]],
    bucket_ranges: list[tuple[datetime, datetime]],
    status: str,
    start: datetime,
    end: datetime,
) -> None:
    if end <= start:
        return
    for index, (bucket_start, bucket_end) in enumerate(bucket_ranges):
        overlap_start = max(start, bucket_start)
        overlap_end = min(end, bucket_end)
        if overlap_end <= overlap_start:
            continue
        buckets[index][status] = buckets[index].get(status, 0.0) + (overlap_end - overlap_start).total_seconds()


def _finalize_history_buckets(buckets: list[dict[str, float]]) -> list[str]:
    return [_dominant_status(bucket) for bucket in buckets]


def _ping_result_from_sample(sample: QuickStatusPingSample) -> PingCheckResult:
    return PingCheckResult(
        checked_at=_coerce_utc(sample.checked_at),
        success=bool(sample.success),
        latency_ms=float(sample.latency_ms) if sample.latency_ms is not None else None,
    )


def _prometheus_result_from_sample(sample: QuickStatusPrometheusSample) -> PrometheusCheckResult:
    return PrometheusCheckResult(
        checked_at=_coerce_utc(sample.checked_at),
        value=float(sample.value) if sample.value is not None else None,
        sampled_at=_coerce_utc(sample.sampled_at) if sample.sampled_at is not None else None,
        error_message=sample.error_message,
    )


def _is_prometheus_result_stale(
    prometheus_result: PrometheusCheckResult | None,
    *,
    now: datetime | None = None,
) -> bool:
    if prometheus_result is None:
        return False
    return _is_reference_stale(prometheus_result.sampled_at, now=now)


def _is_reference_stale(reference_at: datetime | None, *, now: datetime | None = None) -> bool:
    if reference_at is None:
        return False
    reference_now = _coerce_utc(now or datetime.now(tz=timezone.utc))
    return reference_now - _coerce_utc(reference_at) > _TILE_STALE_AFTER


def _evaluate_ping_result(item: QuickStatusItem, ping_result: PingCheckResult | None) -> QuickStatusEvaluation:
    if ping_result is None:
        return QuickStatusEvaluation(value=None, display_value="—", status="unknown")
    if item.metric_key == "ping_result":
        return QuickStatusEvaluation(
            value=1.0 if ping_result.success else 0.0,
            display_value="OK" if ping_result.success else "NOK",
            status="ok" if ping_result.success else "critical",
        )
    if ping_result.success and ping_result.latency_ms is not None:
        value = ping_result.latency_ms
        return QuickStatusEvaluation(
            value=value,
            display_value=_format_item_value(item, value),
            status=_resolve_status(
                value,
                item.warning_threshold,
                item.critical_threshold,
                item.metric_key,
                lower_warning_threshold=item.lower_warning_threshold,
                lower_critical_threshold=item.lower_critical_threshold,
            ),
        )
    return QuickStatusEvaluation(value=None, display_value="timeout", status="critical")


def _evaluate_prometheus_result(
    item: QuickStatusItem,
    prometheus_result: PrometheusCheckResult | None,
    *,
    now: datetime | None = None,
) -> QuickStatusEvaluation:
    if prometheus_result is None:
        return QuickStatusEvaluation(value=None, display_value="—", status="unknown")
    if prometheus_result.value is None:
        display_value = "ERR" if prometheus_result.error_message else "—"
        return QuickStatusEvaluation(value=None, display_value=display_value, status="unknown")
    value = prometheus_result.value
    return QuickStatusEvaluation(
        value=value,
        display_value=_format_item_value(item, value),
        status=_resolve_status(
            value,
            item.warning_threshold,
            item.critical_threshold,
            item.metric_key,
            prometheus_threshold_direction=item.prometheus_threshold_direction,
            lower_warning_threshold=item.lower_warning_threshold,
            lower_critical_threshold=item.lower_critical_threshold,
        ),
    )


def _resolve_status(
    value: float | None,
    warning_threshold: float,
    critical_threshold: float,
    metric_key: str,
    *,
    prometheus_threshold_direction: str | None = None,
    lower_warning_threshold: float | None = None,
    lower_critical_threshold: float | None = None,
) -> str:
    if value is None:
        return "unknown"
    if metric_key in _INFO_ONLY_METRICS:
        return "info"
    if metric_key == "ssh_last_successful_login":
        return "ok"
    if metric_key in {"ssh_status", "telegram_bot_status", "wifi_status", "vpn_status"}:
        if value >= 2:
            return "critical"
        if value >= 1:
            return "warn"
        return "ok"
    if lower_warning_threshold is not None and lower_critical_threshold is not None:
        if value <= lower_critical_threshold or value >= critical_threshold:
            return "critical"
        if value <= lower_warning_threshold or value >= warning_threshold:
            return "warn"
        return "ok"
    if metric_key in _PROMETHEUS_METRICS and prometheus_threshold_direction == "lower":
        if value <= critical_threshold:
            return "critical"
        if value <= warning_threshold:
            return "warn"
        return "ok"
    if metric_key in _REVERSE_THRESHOLD_METRICS:
        if value <= critical_threshold:
            return "critical"
        if value <= warning_threshold:
            return "warn"
        return "ok"
    if value >= critical_threshold:
        return "critical"
    if value >= warning_threshold:
        return "warn"
    return "ok"


def evaluate_quick_status_item(item: QuickStatusItem, snapshot: MetricSnapshot | None) -> QuickStatusEvaluation:
    if snapshot is None:
        return QuickStatusEvaluation(value=None, display_value="—", status="unknown")
    value = _metric_value(snapshot, item.metric_key, item.mount_path)
    return QuickStatusEvaluation(
        value=value,
        display_value=_format_item_value(item, value),
        status=_resolve_status(
            value,
            item.warning_threshold,
            item.critical_threshold,
            item.metric_key,
            prometheus_threshold_direction=item.prometheus_threshold_direction,
            lower_warning_threshold=item.lower_warning_threshold,
            lower_critical_threshold=item.lower_critical_threshold,
        ),
    )


def _build_item_history(
    item: QuickStatusItem,
    snapshots: list[MetricSnapshot],
    window_start: datetime,
    window_end: datetime,
) -> list[str]:
    if item.metric_key in _PING_METRICS or item.metric_key in _PROMETHEUS_METRICS:
        return _empty_history()

    bucket_ranges = _history_segment_ranges(window_start)
    buckets = [{} for _ in range(_HISTORY_SEGMENT_COUNT)]
    ordered_snapshots = sorted(
        snapshots,
        key=lambda snapshot: (_coerce_utc(snapshot.reported_at), snapshot.id),
    )

    previous_snapshot = next(
        (
            snapshot
            for snapshot in reversed(ordered_snapshots)
            if _coerce_utc(snapshot.reported_at) < window_start
        ),
        None,
    )
    current_status = evaluate_quick_status_item(item, previous_snapshot).status
    current_start = window_start

    for snapshot in ordered_snapshots:
        reported_at = _coerce_utc(snapshot.reported_at)
        if reported_at < window_start:
            continue
        if reported_at > window_end:
            break
        _accumulate_status_durations(buckets, bucket_ranges, current_status, current_start, reported_at)
        current_status = evaluate_quick_status_item(item, snapshot).status
        current_start = reported_at

    _accumulate_status_durations(buckets, bucket_ranges, current_status, current_start, window_end)
    return _finalize_history_buckets(buckets)


def _build_ping_history(
    item: QuickStatusItem,
    samples: list[QuickStatusPingSample],
    window_start: datetime,
    window_end: datetime,
) -> list[str]:
    bucket_ranges = _history_segment_ranges(window_start)
    buckets = [{} for _ in range(_HISTORY_SEGMENT_COUNT)]
    ordered_samples = sorted(
        samples,
        key=lambda sample: (_coerce_utc(sample.checked_at), sample.id),
    )

    previous_sample = next(
        (
            sample
            for sample in reversed(ordered_samples)
            if _coerce_utc(sample.checked_at) < window_start
        ),
        None,
    )
    current_status = _evaluate_ping_result(
        item,
        _ping_result_from_sample(previous_sample) if previous_sample is not None else None,
    ).status
    current_start = window_start

    for sample in ordered_samples:
        checked_at = _coerce_utc(sample.checked_at)
        if checked_at < window_start:
            continue
        if checked_at > window_end:
            break
        _accumulate_status_durations(buckets, bucket_ranges, current_status, current_start, checked_at)
        current_status = _evaluate_ping_result(item, _ping_result_from_sample(sample)).status
        current_start = checked_at

    _accumulate_status_durations(buckets, bucket_ranges, current_status, current_start, window_end)
    return _finalize_history_buckets(buckets)


def _build_prometheus_history(
    item: QuickStatusItem,
    samples: list[QuickStatusPrometheusSample],
    window_start: datetime,
    window_end: datetime,
) -> list[str]:
    bucket_ranges = _history_segment_ranges(window_start)
    buckets = [{} for _ in range(_HISTORY_SEGMENT_COUNT)]
    ordered_samples = sorted(
        samples,
        key=lambda sample: (_coerce_utc(sample.checked_at), sample.id),
    )

    previous_sample = next(
        (
            sample
            for sample in reversed(ordered_samples)
            if _coerce_utc(sample.checked_at) < window_start
        ),
        None,
    )
    current_status = _evaluate_prometheus_result(
        item,
        _prometheus_result_from_sample(previous_sample) if previous_sample is not None else None,
        now=window_start,
    ).status
    current_start = window_start

    for sample in ordered_samples:
        checked_at = _coerce_utc(sample.checked_at)
        if checked_at < window_start:
            continue
        if checked_at > window_end:
            break
        _accumulate_status_durations(buckets, bucket_ranges, current_status, current_start, checked_at)
        current_status = _evaluate_prometheus_result(
            item,
            _prometheus_result_from_sample(sample),
            now=checked_at,
        ).status
        current_start = checked_at

    latest_sample = ordered_samples[-1] if ordered_samples else previous_sample
    latest_sampled_at = _coerce_utc(latest_sample.sampled_at) if latest_sample and latest_sample.sampled_at else None
    stale_at = latest_sampled_at + _TILE_STALE_AFTER if latest_sampled_at is not None else None
    if current_status != "unknown" and stale_at is not None and current_start < stale_at < window_end:
        _accumulate_status_durations(buckets, bucket_ranges, current_status, current_start, stale_at)
        current_status = "unknown"
        current_start = stale_at

    _accumulate_status_durations(buckets, bucket_ranges, current_status, current_start, window_end)
    return _finalize_history_buckets(buckets)


def _build_prometheus_series_points(
    samples: list[QuickStatusPrometheusSample],
    window_start: datetime,
    window_end: datetime,
    latest_result: PrometheusCheckResult | None = None,
    *,
    interval_seconds: int | None = None,
) -> list[QuickStatusSeriesPoint]:
    normalized_points: dict[datetime, float | None] = {}
    gap_threshold = max(
        timedelta(seconds=max(5, int(interval_seconds or 60)) * 2),
        _TILE_STALE_AFTER,
    )

    def store_point(recorded_at: datetime | None, value: float | None) -> None:
        if recorded_at is None:
            return
        normalized = _coerce_utc(recorded_at)
        if normalized < window_start or normalized > window_end:
            return
        next_value = float(value) if value is not None else None
        previous_value = normalized_points.get(normalized)
        if previous_value is not None and next_value is None:
            return
        normalized_points[normalized] = next_value

    for sample in samples:
        store_point(sample.sampled_at or sample.checked_at, sample.value)

    if latest_result is not None:
        store_point(latest_result.sampled_at or latest_result.checked_at, latest_result.value)

    ordered_points = [
        QuickStatusSeriesPoint(recorded_at=recorded_at, value=value)
        for recorded_at, value in sorted(normalized_points.items(), key=lambda entry: entry[0])
    ]
    if not ordered_points:
        return [
            QuickStatusSeriesPoint(recorded_at=window_start, value=None),
            QuickStatusSeriesPoint(recorded_at=window_end, value=None),
        ]

    points: list[QuickStatusSeriesPoint] = []
    if ordered_points[0].recorded_at > window_start:
        points.append(QuickStatusSeriesPoint(recorded_at=window_start, value=None))

    previous_recorded_at = window_start
    for point in ordered_points:
        if point.recorded_at - previous_recorded_at > gap_threshold:
            points.append(QuickStatusSeriesPoint(recorded_at=point.recorded_at, value=None))
        points.append(point)
        previous_recorded_at = point.recorded_at

    if ordered_points[-1].recorded_at < window_end:
        points.append(QuickStatusSeriesPoint(recorded_at=window_end, value=None))

    return points


def _build_prometheus_series_summary(points: list[QuickStatusSeriesPoint]) -> QuickStatusSeriesSummary | None:
    if not points:
        return None
    values = [point.value for point in points if point.value is not None]
    if not values:
        return None
    return QuickStatusSeriesSummary(
        min_value=min(values),
        max_value=max(values),
        average_value=sum(values) / len(values),
        delta_value=values[-1] - values[0] if len(values) >= 2 else 0.0,
    )


def _build_heartbeat_history(
    backend: object,
    timestamps: list[datetime],
    window_start: datetime,
    window_end: datetime,
) -> list[str]:
    """Heartbeat strip from snapshot timestamps only (the one before the window plus those inside)."""
    bucket_ranges = _history_segment_ranges(window_start)
    buckets = [{} for _ in range(_HISTORY_SEGMENT_COUNT)]
    ordered = sorted(_coerce_utc(value) for value in timestamps)
    if not ordered:
        for bucket in buckets:
            bucket["unknown"] = _HISTORY_SEGMENT_DURATION.total_seconds()
        return _finalize_history_buckets(buckets)

    freshness_seconds = max(getattr(backend, "poll_interval_seconds", 60) or 60, 30) * 3
    freshness_window = timedelta(seconds=freshness_seconds)

    previous_reported_at = next((value for value in reversed(ordered) if value < window_start), None)
    current_start = window_start
    current_status = "unknown"
    fresh_until: datetime | None = None

    if previous_reported_at is not None:
        fresh_until = previous_reported_at + freshness_window
        current_status = "ok" if fresh_until > window_start else "critical"

    for reported_at in ordered:
        if reported_at < window_start:
            continue
        if reported_at > window_end:
            break

        if current_status == "ok" and fresh_until is not None and fresh_until <= reported_at:
            _accumulate_status_durations(buckets, bucket_ranges, "ok", current_start, fresh_until)
            _accumulate_status_durations(buckets, bucket_ranges, "critical", fresh_until, reported_at)
        else:
            _accumulate_status_durations(buckets, bucket_ranges, current_status, current_start, reported_at)

        current_status = "ok"
        current_start = reported_at
        fresh_until = reported_at + freshness_window

    if current_status == "ok" and fresh_until is not None and fresh_until < window_end:
        _accumulate_status_durations(buckets, bucket_ranges, "ok", current_start, fresh_until)
        _accumulate_status_durations(buckets, bucket_ranges, "critical", fresh_until, window_end)
    else:
        _accumulate_status_durations(buckets, bucket_ranges, current_status, current_start, window_end)

    return _finalize_history_buckets(buckets)


def _build_item_history_from_events(
    events: list[tuple[datetime, str]],
    window_start: datetime,
    window_end: datetime,
) -> list[str]:
    """Same dominant-status-per-bucket rule as the snapshot algorithm, fed by the event log."""
    bucket_ranges = _history_segment_ranges(window_start)
    buckets = [{} for _ in range(_HISTORY_SEGMENT_COUNT)]
    ordered = sorted(((_coerce_utc(started_at), status) for started_at, status in events), key=lambda entry: entry[0])

    current_status = "unknown"
    for started_at, status in ordered:
        if started_at < window_start:
            current_status = status
            continue
        break
    current_start = window_start
    for started_at, status in ordered:
        if started_at < window_start:
            continue
        if started_at > window_end:
            break
        _accumulate_status_durations(buckets, bucket_ranges, current_status, current_start, started_at)
        current_status = status
        current_start = started_at
    _accumulate_status_durations(buckets, bucket_ranges, current_status, current_start, window_end)
    return _finalize_history_buckets(buckets)


async def _load_history_events(
    session: AsyncSession,
    item_ids: set[int],
    window_start: datetime,
    window_end: datetime,
) -> dict[int, list[tuple[datetime, str]]]:
    """Status events inside the window plus the last one before it, per item."""
    events: dict[int, list[tuple[datetime, str]]] = {item_id: [] for item_id in item_ids}
    for item_id in sorted(item_ids):
        previous = await session.execute(
            select(QuickStatusStatusEvent.started_at, QuickStatusStatusEvent.status)
            .where(
                QuickStatusStatusEvent.quick_status_item_id == item_id,
                QuickStatusStatusEvent.started_at < window_start,
            )
            .order_by(QuickStatusStatusEvent.started_at.desc(), QuickStatusStatusEvent.id.desc())
            .limit(1)
        )
        events[item_id].extend((started_at, status) for started_at, status in previous.all())
        current = await session.execute(
            select(QuickStatusStatusEvent.started_at, QuickStatusStatusEvent.status)
            .where(
                QuickStatusStatusEvent.quick_status_item_id == item_id,
                QuickStatusStatusEvent.started_at >= window_start,
                QuickStatusStatusEvent.started_at <= window_end,
            )
            .order_by(QuickStatusStatusEvent.started_at, QuickStatusStatusEvent.id)
        )
        events[item_id].extend((started_at, status) for started_at, status in current.all())
    return events


async def _load_heartbeat_timestamps(
    session: AsyncSession,
    backend_ids: set[int],
    window_start: datetime,
    window_end: datetime,
) -> dict[int, list[datetime]]:
    """Snapshot timestamps per backend (two narrow queries each; no row objects, no JSON)."""
    timestamps: dict[int, list[datetime]] = {backend_id: [] for backend_id in backend_ids}
    for backend_id in sorted(backend_ids):
        previous = await session.scalar(
            select(MetricSnapshot.reported_at)
            .where(MetricSnapshot.backend_id == backend_id, MetricSnapshot.reported_at < window_start)
            .order_by(MetricSnapshot.reported_at.desc(), MetricSnapshot.id.desc())
            .limit(1)
        )
        if previous is not None:
            timestamps[backend_id].append(previous)
        current = await session.execute(
            select(MetricSnapshot.reported_at)
            .where(
                MetricSnapshot.backend_id == backend_id,
                MetricSnapshot.reported_at >= window_start,
                MetricSnapshot.reported_at <= window_end,
            )
            .order_by(MetricSnapshot.reported_at, MetricSnapshot.id)
        )
        timestamps[backend_id].extend(current.scalars())
    return timestamps


async def _latest_event_status(session: AsyncSession, item_id: int) -> str | None:
    return await session.scalar(
        select(QuickStatusStatusEvent.status)
        .where(QuickStatusStatusEvent.quick_status_item_id == item_id)
        .order_by(QuickStatusStatusEvent.started_at.desc(), QuickStatusStatusEvent.id.desc())
        .limit(1)
    )


async def rebuild_status_events_for_item(
    session: AsyncSession,
    item: QuickStatusItem,
    *,
    now: datetime | None = None,
) -> int:
    """Recompute the item's event log from the last 24h of snapshots (item created/edited, or
    first ingest on a database that predates the log). Returns the number of events written."""
    if not _is_metric_item(item):
        return 0
    await session.execute(
        delete(QuickStatusStatusEvent)
        .where(QuickStatusStatusEvent.quick_status_item_id == item.id)
        .execution_options(synchronize_session=False)
    )
    window_start, window_end = _history_window_bounds(now)
    options = [undefer(MetricSnapshot.raw_payload)] if item.metric_key in _RAW_PAYLOAD_METRICS else []
    previous = (
        await session.execute(
            select(MetricSnapshot)
            .options(*options)
            .where(MetricSnapshot.backend_id == item.backend_id, MetricSnapshot.reported_at < window_start)
            .order_by(MetricSnapshot.reported_at.desc(), MetricSnapshot.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    current = await session.execute(
        select(MetricSnapshot)
        .options(*options)
        .where(
            MetricSnapshot.backend_id == item.backend_id,
            MetricSnapshot.reported_at >= window_start,
            MetricSnapshot.reported_at <= window_end,
        )
        .order_by(MetricSnapshot.reported_at, MetricSnapshot.id)
    )
    snapshots = ([previous] if previous is not None else []) + list(current.scalars())
    last_status: str | None = None
    written = 0
    for snapshot in snapshots:
        status = evaluate_quick_status_item(item, snapshot).status
        if status == last_status:
            continue
        session.add(
            QuickStatusStatusEvent(
                quick_status_item_id=item.id,
                status=status,
                started_at=_coerce_utc(snapshot.reported_at),
            )
        )
        last_status = status
        written += 1
    await session.flush()
    if last_status is not None:
        _LAST_STATUS[item.id] = last_status
    else:
        _LAST_STATUS.pop(item.id, None)
    return written


async def record_quick_status_status_events(
    session: AsyncSession,
    items: Iterable[QuickStatusItem],
    snapshot: MetricSnapshot,
) -> int:
    """Append an event for every metric item whose status changed with this snapshot."""
    written = 0
    reported_at = _coerce_utc(snapshot.reported_at)
    for item in items:
        if not _is_metric_item(item) or item.backend_id != snapshot.backend_id:
            continue
        if item.id not in _LAST_STATUS:
            latest = await _latest_event_status(session, item.id)
            if latest is None:
                # No log yet for this item (fresh install or pre-9.0 data): seed it from history.
                written += await rebuild_status_events_for_item(session, item, now=reported_at)
                latest = _LAST_STATUS.get(item.id)
            if latest is not None:
                _LAST_STATUS[item.id] = latest
        status = evaluate_quick_status_item(item, snapshot).status
        if _LAST_STATUS.get(item.id) == status:
            continue
        session.add(QuickStatusStatusEvent(quick_status_item_id=item.id, status=status, started_at=reported_at))
        _LAST_STATUS[item.id] = status
        written += 1
    return written


async def _load_latest_snapshots(
    session: AsyncSession,
    backend_ids: set[int],
) -> dict[int, MetricSnapshot]:
    """Newest snapshot per backend, including the deferred raw_payload (details need it)."""
    latest: dict[int, MetricSnapshot] = {}
    for backend_id in sorted(backend_ids):
        result = await session.execute(
            select(MetricSnapshot)
            .options(undefer(MetricSnapshot.raw_payload))
            .where(MetricSnapshot.backend_id == backend_id)
            .order_by(MetricSnapshot.reported_at.desc(), MetricSnapshot.id.desc())
            .limit(1)
        )
        snapshot = result.scalar_one_or_none()
        if snapshot is not None:
            latest[backend_id] = snapshot
    return latest


async def _load_ping_history_samples(
    session: AsyncSession,
    item_ids: set[int],
    window_start: datetime,
    window_end: datetime,
) -> dict[int, list[QuickStatusPingSample]]:
    if not item_ids:
        return {}

    ranked_previous_samples = (
        select(
            QuickStatusPingSample.id.label("sample_id"),
            func.row_number().over(
                partition_by=QuickStatusPingSample.quick_status_item_id,
                order_by=(QuickStatusPingSample.checked_at.desc(), QuickStatusPingSample.id.desc()),
            ).label("row_number"),
        )
        .where(
            QuickStatusPingSample.quick_status_item_id.in_(item_ids),
            QuickStatusPingSample.checked_at < window_start,
        )
        .subquery()
    )

    previous_result = await session.execute(
        select(QuickStatusPingSample).join(
            ranked_previous_samples,
            QuickStatusPingSample.id == ranked_previous_samples.c.sample_id,
        ).where(ranked_previous_samples.c.row_number == 1)
    )
    current_result = await session.execute(
        select(QuickStatusPingSample)
        .where(
            QuickStatusPingSample.quick_status_item_id.in_(item_ids),
            QuickStatusPingSample.checked_at >= window_start,
            QuickStatusPingSample.checked_at <= window_end,
        )
        .order_by(QuickStatusPingSample.quick_status_item_id, QuickStatusPingSample.checked_at, QuickStatusPingSample.id)
    )

    samples_by_item: dict[int, list[QuickStatusPingSample]] = {item_id: [] for item_id in item_ids}
    for sample in previous_result.scalars():
        samples_by_item.setdefault(sample.quick_status_item_id, []).append(sample)
    for sample in current_result.scalars():
        samples_by_item.setdefault(sample.quick_status_item_id, []).append(sample)
    for samples in samples_by_item.values():
        samples.sort(key=lambda sample: (_coerce_utc(sample.checked_at), sample.id))
    return samples_by_item


async def _load_prometheus_history_samples(
    session: AsyncSession,
    item_ids: set[int],
    window_start: datetime,
    window_end: datetime,
) -> dict[int, list[QuickStatusPrometheusSample]]:
    if not item_ids:
        return {}

    ranked_previous_samples = (
        select(
            QuickStatusPrometheusSample.id.label("sample_id"),
            func.row_number().over(
                partition_by=QuickStatusPrometheusSample.quick_status_item_id,
                order_by=(QuickStatusPrometheusSample.checked_at.desc(), QuickStatusPrometheusSample.id.desc()),
            ).label("row_number"),
        )
        .where(
            QuickStatusPrometheusSample.quick_status_item_id.in_(item_ids),
            QuickStatusPrometheusSample.checked_at < window_start,
        )
        .subquery()
    )

    previous_result = await session.execute(
        select(QuickStatusPrometheusSample).join(
            ranked_previous_samples,
            QuickStatusPrometheusSample.id == ranked_previous_samples.c.sample_id,
        ).where(ranked_previous_samples.c.row_number == 1)
    )
    current_result = await session.execute(
        select(QuickStatusPrometheusSample)
        .where(
            QuickStatusPrometheusSample.quick_status_item_id.in_(item_ids),
            QuickStatusPrometheusSample.checked_at >= window_start,
            QuickStatusPrometheusSample.checked_at <= window_end,
        )
        .order_by(
            QuickStatusPrometheusSample.quick_status_item_id,
            QuickStatusPrometheusSample.checked_at,
            QuickStatusPrometheusSample.id,
        )
    )

    samples_by_item: dict[int, list[QuickStatusPrometheusSample]] = {item_id: [] for item_id in item_ids}
    for sample in previous_result.scalars():
        samples_by_item.setdefault(sample.quick_status_item_id, []).append(sample)
    for sample in current_result.scalars():
        samples_by_item.setdefault(sample.quick_status_item_id, []).append(sample)
    for samples in samples_by_item.values():
        samples.sort(key=lambda sample: (_coerce_utc(sample.checked_at), sample.id))
    return samples_by_item


async def _load_latest_ping_samples(
    session: AsyncSession,
    item_ids: set[int],
) -> dict[int, QuickStatusPingSample]:
    latest: dict[int, QuickStatusPingSample] = {}
    for item_id in sorted(item_ids):
        result = await session.execute(
            select(QuickStatusPingSample)
            .where(QuickStatusPingSample.quick_status_item_id == item_id)
            .order_by(QuickStatusPingSample.checked_at.desc(), QuickStatusPingSample.id.desc())
            .limit(1)
        )
        sample = result.scalar_one_or_none()
        if sample is not None:
            latest[item_id] = sample
    return latest


async def _load_latest_prometheus_samples(
    session: AsyncSession,
    item_ids: set[int],
) -> dict[int, QuickStatusPrometheusSample]:
    latest: dict[int, QuickStatusPrometheusSample] = {}
    for item_id in sorted(item_ids):
        result = await session.execute(
            select(QuickStatusPrometheusSample)
            .where(QuickStatusPrometheusSample.quick_status_item_id == item_id)
            .order_by(QuickStatusPrometheusSample.checked_at.desc(), QuickStatusPrometheusSample.id.desc())
            .limit(1)
        )
        sample = result.scalar_one_or_none()
        if sample is not None:
            latest[item_id] = sample
    return latest


def _has_detail_content(
    item: QuickStatusItem,
    snapshot: MetricSnapshot | None,
) -> bool:
    if item.metric_key in _PROMETHEUS_METRICS:
        return True
    if snapshot is None:
        return False

    payload = snapshot.raw_payload if isinstance(snapshot.raw_payload, dict) else {}

    if item.metric_key == "ssh_last_unsuccessful_attempt":
        return bool(
            (
                isinstance(payload.get("ssh_last_failure_auth_method"), str)
                and payload.get("ssh_last_failure_auth_method", "").strip()
            )
            or (
                isinstance(payload.get("ssh_last_failure_username"), str)
                and payload.get("ssh_last_failure_username", "").strip()
            )
            or (
                isinstance(payload.get("ssh_last_failure_source_ip"), str)
                and payload.get("ssh_last_failure_source_ip", "").strip()
            )
            or isinstance(payload.get("ssh_last_failure_port"), int)
            or (
                isinstance(payload.get("ssh_last_failure_line"), str)
                and payload.get("ssh_last_failure_line", "").strip()
            )
        )

    if item.metric_key == "ssh_status":
        for key in (
            "ssh_pubkey_auth_line",
            "ssh_password_auth_line",
            "ssh_kbd_interactive_auth_line",
            "ssh_permit_root_login_line",
        ):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return True
        return False

    if item.metric_key == "telegram_bot_status":
        if bot_health.get_bot_health().enabled:
            return True
        return isinstance(payload.get("telegram_bot_running"), bool) or isinstance(
            payload.get("telegram_egress_proxy_running"), bool
        )

    if item.metric_key == "wifi_status":
        return any(
            payload.get(key) is not None
            for key in (
                "wifi_ssid",
                "wifi_interface",
                "wifi_client_count",
                "wifi_dhcp_lease_count",
                "wifi_operstate",
                "wifi_hostapd_socket_present",
                "wifi_associated_clients",
            )
        )

    if item.metric_key == "vpn_status":
        return any(
            payload.get(key) is not None
            for key in (
                "vpn_active_backend",
                "vpn_hotspot_public_ip",
                "vpn_expected_public_ip",
                "vpn_default_public_ip",
            )
        )

    return False


async def _persist_ping_result(
    session: AsyncSession,
    item: QuickStatusItem,
    ping_result: PingCheckResult,
    latest_sample: QuickStatusPingSample | None,
) -> QuickStatusPingSample | None:
    if latest_sample is not None and _coerce_utc(latest_sample.checked_at) >= ping_result.checked_at:
        return None
    sample = QuickStatusPingSample(
        quick_status_item_id=item.id,
        checked_at=ping_result.checked_at,
        success=ping_result.success,
        latency_ms=ping_result.latency_ms,
    )
    session.add(sample)
    await session.flush()
    return sample


async def _persist_prometheus_result(
    session: AsyncSession,
    item: QuickStatusItem,
    result: PrometheusCheckResult,
    latest_sample: QuickStatusPrometheusSample | None,
) -> QuickStatusPrometheusSample | None:
    if latest_sample is not None and _coerce_utc(latest_sample.checked_at) >= result.checked_at:
        return None
    sample = QuickStatusPrometheusSample(
        quick_status_item_id=item.id,
        checked_at=result.checked_at,
        sampled_at=result.sampled_at,
        value=result.value,
        error_message=result.error_message,
    )
    session.add(sample)
    await session.flush()
    return sample


def _cached_ping_result(
    item: QuickStatusItem,
    latest_sample: QuickStatusPingSample | None,
) -> PingCheckResult | None:
    """Newest known ping result without touching the network (cache first, then DB sample)."""
    cached = _PING_CACHE.get(_ping_cache_key(item))
    from_sample = _ping_result_from_sample(latest_sample) if latest_sample is not None else None
    if cached is not None and (from_sample is None or cached.checked_at >= from_sample.checked_at):
        return cached
    if from_sample is not None:
        _PING_CACHE[_ping_cache_key(item)] = from_sample
    return from_sample


async def _fetch_ping_result(item: QuickStatusItem, checked_at: datetime) -> PingCheckResult | None:
    """Ask the backend's monitor agent to ping the item's endpoint (network, background only)."""
    if not item.ping_endpoint:
        return None
    backend = _loaded_relation(item, "backend")
    if backend is None:
        return None
    timeout_seconds = min(30, max(1, int(settings.monitor_request_timeout_seconds or 1)))
    try:
        payload = await fetch_ping(backend.base_url, backend.api_token, item.ping_endpoint, timeout_seconds)
    except (MonitorClientError, Exception):
        payload = None
    if payload is None:
        return PingCheckResult(checked_at=checked_at, success=False, latency_ms=None)
    latency = payload.get("latency_ms")
    return PingCheckResult(
        checked_at=checked_at,
        success=bool(payload.get("success")),
        latency_ms=float(latency) if isinstance(latency, (int, float)) else None,
    )


def _cached_prometheus_result(
    item: QuickStatusItem,
    latest_sample: QuickStatusPrometheusSample | None,
) -> PrometheusCheckResult | None:
    cached = _PROMETHEUS_CACHE.get(_prometheus_cache_key(item))
    from_sample = _prometheus_result_from_sample(latest_sample) if latest_sample is not None else None
    if cached is not None and (from_sample is None or cached.checked_at >= from_sample.checked_at):
        return cached
    if from_sample is not None:
        _PROMETHEUS_CACHE[_prometheus_cache_key(item)] = from_sample
    return from_sample


async def _fetch_prometheus_result(item: QuickStatusItem, checked_at: datetime) -> PrometheusCheckResult | None:
    if not item.prometheus_url or not item.prometheus_query:
        return None
    try:
        response = await fetch_prometheus_query_value(item.prometheus_url, item.prometheus_query)
        return PrometheusCheckResult(checked_at=checked_at, sampled_at=response.sampled_at, value=response.value)
    except (PrometheusQueryError, Exception) as exc:
        message = str(exc).strip() or "Prometheus query failed"
        return PrometheusCheckResult(checked_at=checked_at, value=None, error_message=message)


async def list_quick_status_items(session: AsyncSession) -> list[QuickStatusItem]:
    result = await session.execute(
        select(QuickStatusItem)
        .options(selectinload(QuickStatusItem.backend))
        .order_by(QuickStatusItem.display_order, QuickStatusItem.id)
    )
    return [item for item in result.scalars() if is_supported_quick_status_metric(getattr(item, "metric_key", None))]


async def list_quick_status_items_for_backend(session: AsyncSession, backend_id: int) -> list[QuickStatusItem]:
    result = await session.execute(
        select(QuickStatusItem)
        .options(selectinload(QuickStatusItem.backend))
        .where(QuickStatusItem.backend_id == backend_id)
        .order_by(QuickStatusItem.display_order, QuickStatusItem.id)
    )
    return [item for item in result.scalars() if is_supported_quick_status_metric(getattr(item, "metric_key", None))]


async def refresh_due_quick_status_samples(
    session: AsyncSession,
    items: Iterable[QuickStatusItem] | None = None,
    *,
    now: datetime | None = None,
) -> int:
    """Run due ping/Prometheus checks concurrently and persist new samples.

    Due-ness is decided from the in-memory cache (primed from the newest DB sample on a cold
    start), so steady-state ticks do no database work for items that are not due yet.
    """
    items_list = list(items) if items is not None else await list_quick_status_items(session)
    ping_items = [item for item in items_list if item.metric_key in _PING_METRICS and item.ping_endpoint]
    prometheus_items = [
        item
        for item in items_list
        if item.metric_key in _PROMETHEUS_METRICS and item.prometheus_url and item.prometheus_query
    ]
    if not ping_items and not prometheus_items:
        return 0

    checked_at = _coerce_utc(now or datetime.now(tz=timezone.utc))

    cold_ping_ids = {item.id for item in ping_items if _ping_cache_key(item) not in _PING_CACHE}
    cold_prometheus_ids = {item.id for item in prometheus_items if _prometheus_cache_key(item) not in _PROMETHEUS_CACHE}
    latest_ping_samples = await _load_latest_ping_samples(session, cold_ping_ids)
    latest_prometheus_samples = await _load_latest_prometheus_samples(session, cold_prometheus_ids)

    due: list[tuple[str, QuickStatusItem]] = []
    for item in ping_items:
        known = _cached_ping_result(item, latest_ping_samples.get(item.id))
        interval = max(5, int(item.ping_interval_seconds or 60))
        if known is None or checked_at - known.checked_at >= timedelta(seconds=interval):
            due.append(("ping", item))
    for item in prometheus_items:
        known = _cached_prometheus_result(item, latest_prometheus_samples.get(item.id))
        interval = max(5, int(item.prometheus_interval_seconds or 60))
        if known is None or checked_at - known.checked_at >= timedelta(seconds=interval):
            due.append(("prometheus", item))
    if not due:
        return 0

    semaphore = asyncio.Semaphore(_NETWORK_CHECK_CONCURRENCY)

    async def _run(kind: str, item: QuickStatusItem):
        async with semaphore:
            if kind == "ping":
                return await _fetch_ping_result(item, checked_at)
            return await _fetch_prometheus_result(item, checked_at)

    results = await asyncio.gather(*(_run(kind, item) for kind, item in due), return_exceptions=True)

    # Persist sequentially: one AsyncSession must not be used concurrently.
    due_ping_ids = {item.id for kind, item in due if kind == "ping"} - set(latest_ping_samples)
    due_prometheus_ids = {item.id for kind, item in due if kind == "prometheus"} - set(latest_prometheus_samples)
    latest_ping_samples.update(await _load_latest_ping_samples(session, due_ping_ids))
    latest_prometheus_samples.update(await _load_latest_prometheus_samples(session, due_prometheus_ids))

    persisted_count = 0
    for (kind, item), result in zip(due, results):
        if result is None or isinstance(result, BaseException):
            continue
        if kind == "ping" and isinstance(result, PingCheckResult):
            _PING_CACHE[_ping_cache_key(item)] = result
            persisted = await _persist_ping_result(session, item, result, latest_ping_samples.get(item.id))
        elif kind == "prometheus" and isinstance(result, PrometheusCheckResult):
            _PROMETHEUS_CACHE[_prometheus_cache_key(item)] = result
            persisted = await _persist_prometheus_result(
                session, item, result, latest_prometheus_samples.get(item.id)
            )
        else:
            persisted = None
        if persisted is not None:
            persisted_count += 1
    return persisted_count


async def refresh_quick_status_alert_state(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> None:
    """Record when warn/critical conditions started (owned by the background poller)."""
    items = await list_quick_status_items(session)
    if not items:
        return
    await build_quick_status_tile_summaries(session, items, now=now, persist_alert_state=True)
    await session.commit()


async def run_due_quick_status_checks(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> int:
    persisted_count = await refresh_due_quick_status_samples(session, now=now)
    if persisted_count:
        await session.commit()
    return persisted_count


async def build_quick_status_tiles(
    session: AsyncSession,
    items: Iterable[QuickStatusItem],
    *,
    include_heartbeat_tiles: bool = False,
    backends: Iterable[object] | None = None,
    now: datetime | None = None,
    persist_alert_state: bool = False,
) -> list[QuickStatusTileRead]:
    """Full tiles with 24h history strips, series and details. Never touches the network."""
    items_list = sorted(
        [item for item in items if is_supported_quick_status_metric(getattr(item, "metric_key", None))],
        key=_quick_status_item_sort_key,
    )
    backend_by_id: dict[int, object] = {}
    for item in items_list:
        backend = getattr(item, "backend", None)
        if backend is not None and item.backend_id is not None:
            backend_by_id[item.backend_id] = backend
    for backend in backends or []:
        backend_id = getattr(backend, "id", None)
        if backend_id is not None:
            backend_by_id[backend_id] = backend

    if not items_list and not (include_heartbeat_tiles and backend_by_id):
        return []

    backend_ids = set(backend_by_id) | {item.backend_id for item in items_list if item.backend_id is not None}
    window_start, window_end = _history_window_bounds(now)
    ping_item_ids = {item.id for item in items_list if item.metric_key in _PING_METRICS}
    prometheus_item_ids = {item.id for item in items_list if item.metric_key in _PROMETHEUS_METRICS}
    metric_item_ids = {item.id for item in items_list if _is_metric_item(item)}
    ping_history_by_item = await _load_ping_history_samples(session, ping_item_ids, window_start, window_end)
    prometheus_history_by_item = await _load_prometheus_history_samples(
        session, prometheus_item_ids, window_start, window_end
    )
    events_by_item = await _load_history_events(session, metric_item_ids, window_start, window_end)
    heartbeat_timestamps = (
        await _load_heartbeat_timestamps(session, backend_ids, window_start, window_end)
        if include_heartbeat_tiles
        else {}
    )
    latest_snapshots = await _load_latest_snapshots(
        session, {item.backend_id for item in items_list if item.backend_id is not None} | (backend_ids if include_heartbeat_tiles else set())
    )
    ping_results_by_item: dict[int, PingCheckResult | None] = {}
    prometheus_results_by_item: dict[int, PrometheusCheckResult | None] = {}
    prometheus_series_by_item: dict[int, list[QuickStatusSeriesPoint]] = {}
    prometheus_summary_by_item: dict[int, QuickStatusSeriesSummary | None] = {}

    for item in items_list:
        if item.metric_key in _PING_METRICS:
            history = ping_history_by_item.get(item.id) or []
            ping_results_by_item[item.id] = _cached_ping_result(item, history[-1] if history else None)
        elif item.metric_key in _PROMETHEUS_METRICS:
            history = prometheus_history_by_item.get(item.id) or []
            prometheus_results_by_item[item.id] = _cached_prometheus_result(item, history[-1] if history else None)

    tiles: list[QuickStatusTileRead] = []
    if include_heartbeat_tiles:
        for backend_id in sorted(
            backend_ids,
            key=lambda current_backend_id: (
                getattr(backend_by_id.get(current_backend_id), "display_order", 0),
                getattr(backend_by_id.get(current_backend_id), "name", "").casefold(),
                current_backend_id,
            ),
        ):
            backend = backend_by_id.get(backend_id)
            latest_snapshot = latest_snapshots.get(backend_id)
            freshness_seconds = max(getattr(backend, "poll_interval_seconds", 60) or 60, 30) * 3
            reference_at = getattr(backend, "last_seen_at", None) or getattr(latest_snapshot, "reported_at", None)
            reference_date = _coerce_utc(reference_at) if isinstance(reference_at, datetime) else None
            is_fresh = reference_date is not None and (window_end - reference_date).total_seconds() <= freshness_seconds
            is_stale = _is_reference_stale(reference_date, now=window_end)
            tiles.append(
                QuickStatusTileRead(
                    id=-backend_id,
                    backend_id=backend_id,
                    backend_display_order=getattr(backend, "display_order", 0),
                    backend_name=getattr(backend, "name", f"Backend #{backend_id}"),
                    label="HB",
                    metric_key="ssh_status",
                    value=1.0 if is_fresh else (0.0 if reference_date is not None else None),
                    display_value="Now" if is_fresh else ("Late" if reference_date is not None else "—"),
                    status="ok" if is_fresh else ("critical" if reference_date is not None else "unknown"),
                    stale=is_stale,
                    history=_build_heartbeat_history(
                        backend,
                        heartbeat_timestamps.get(backend_id, []),
                        window_start,
                        window_end,
                    ),
                    history_loaded=True,
                    has_details=True,
                    reported_at=reference_date,
                    details=[
                        QuickStatusDetailLine(
                            text=f"Heartbeat threshold: {freshness_seconds}s",
                            severity="ok" if is_fresh else ("critical" if reference_date is not None else "warn"),
                        ),
                        QuickStatusDetailLine(
                            text=f"Last seen: {reference_date.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
                            if reference_date is not None
                            else "No heartbeat received yet",
                            severity="ok" if is_fresh else ("critical" if reference_date is not None else "warn"),
                        ),
                    ],
                )
            )

    for item in items_list:
        if item.metric_key not in _PROMETHEUS_METRICS:
            continue
        prometheus_result = prometheus_results_by_item.get(item.id)
        series_points = _build_prometheus_series_points(
            prometheus_history_by_item.get(item.id, []),
            window_start,
            window_end,
            prometheus_result,
            interval_seconds=item.prometheus_interval_seconds,
        )
        prometheus_series_by_item[item.id] = series_points
        prometheus_summary_by_item[item.id] = _build_prometheus_series_summary(series_points)

    for item in items_list:
        backend = _loaded_relation(item, "backend")
        snapshot = latest_snapshots.get(item.backend_id)
        ping_result = ping_results_by_item.get(item.id) if item.metric_key in _PING_METRICS else None
        prometheus_result = (
            prometheus_results_by_item.get(item.id) if item.metric_key in _PROMETHEUS_METRICS else None
        )
        value = _metric_value(snapshot, item.metric_key, item.mount_path) if snapshot else None
        status = _resolve_status(
            value,
            item.warning_threshold,
            item.critical_threshold,
            item.metric_key,
            prometheus_threshold_direction=item.prometheus_threshold_direction,
        )
        display_value = _format_item_value(item, value)
        reported_at = _coerce_utc(snapshot.reported_at) if snapshot and snapshot.reported_at is not None else None
        if item.metric_key in _PING_METRICS:
            ping_evaluation = _evaluate_ping_result(item, ping_result)
            value = ping_evaluation.value
            display_value = ping_evaluation.display_value
            status = ping_evaluation.status
            reported_at = _coerce_utc(ping_result.checked_at) if ping_result is not None else None
        elif item.metric_key in _PROMETHEUS_METRICS:
            prometheus_evaluation = _evaluate_prometheus_result(item, prometheus_result, now=window_end)
            value = prometheus_evaluation.value
            display_value = prometheus_evaluation.display_value
            status = prometheus_evaluation.status
            reported_at = (
                _coerce_utc(prometheus_result.sampled_at)
                if prometheus_result is not None and prometheus_result.sampled_at is not None
                else (_coerce_utc(prometheus_result.checked_at) if prometheus_result is not None else None)
            )
        stale = _is_reference_stale(reported_at, now=window_end)
        details = _build_detail_lines(
            item,
            snapshot,
            status=status,
            prometheus_result=prometheus_result,
            prometheus_summary=prometheus_summary_by_item.get(item.id),
            now=window_end,
        )
        tiles.append(
            QuickStatusTileRead(
                id=item.id,
                backend_id=item.backend_id,
                backend_display_order=backend.display_order if backend else 0,
                backend_name=backend.name if backend else f"Backend #{item.backend_id}",
                label=item.label,
                metric_key=item.metric_key,
                display_variant=getattr(item, "display_variant", "tile"),
                display_unit=getattr(item, "display_unit", None),
                value=value,
                display_value=display_value,
                status=status,
                stale=stale,
                show_updated_age=item.show_updated_age,
                history_loaded=True,
                has_details=bool(details),
                history=(
                    _build_ping_history(item, ping_history_by_item.get(item.id, []), window_start, window_end)
                    if item.metric_key in _PING_METRICS
                    else (
                        _build_prometheus_history(
                            item,
                            prometheus_history_by_item.get(item.id, []),
                            window_start,
                            window_end,
                        )
                        if item.metric_key in _PROMETHEUS_METRICS
                        else _build_item_history_from_events(
                            events_by_item.get(item.id, []),
                            window_start,
                            window_end,
                        )
                    )
                ),
                series=prometheus_series_by_item.get(item.id, []),
                series_summary=prometheus_summary_by_item.get(item.id),
                reported_at=reported_at,
                details=details,
            )
        )
    return await _apply_quick_status_alert_suppression_to_tiles(
        session, items_list, tiles, now=window_end, persist=persist_alert_state
    )


async def build_quick_status_tile_summaries(
    session: AsyncSession,
    items: Iterable[QuickStatusItem],
    *,
    include_heartbeat_tiles: bool = False,
    backends: Iterable[object] | None = None,
    now: datetime | None = None,
    include_details: bool = False,
    persist_alert_state: bool = False,
) -> list[QuickStatusTileRead]:
    """Lightweight tiles (no history/series) from the newest stored readings. No network."""
    items_list = sorted(
        [item for item in items if is_supported_quick_status_metric(getattr(item, "metric_key", None))],
        key=_quick_status_item_sort_key,
    )
    backend_by_id: dict[int, object] = {}
    for item in items_list:
        backend = getattr(item, "backend", None)
        if backend is not None and item.backend_id is not None:
            backend_by_id[item.backend_id] = backend
    for backend in backends or []:
        backend_id = getattr(backend, "id", None)
        if backend_id is not None:
            backend_by_id[backend_id] = backend

    if not items_list and not (include_heartbeat_tiles and backend_by_id):
        return []

    summary_now = _coerce_utc(now or datetime.now(tz=timezone.utc))
    backend_ids = set(backend_by_id) | {item.backend_id for item in items_list if item.backend_id is not None}
    ping_item_ids = {item.id for item in items_list if item.metric_key in _PING_METRICS}
    prometheus_item_ids = {item.id for item in items_list if item.metric_key in _PROMETHEUS_METRICS}

    latest_snapshots_by_backend = await _load_latest_snapshots(session, backend_ids)
    latest_ping_samples = await _load_latest_ping_samples(session, ping_item_ids)
    latest_prometheus_samples = await _load_latest_prometheus_samples(session, prometheus_item_ids)

    ping_results_by_item: dict[int, PingCheckResult | None] = {}
    prometheus_results_by_item: dict[int, PrometheusCheckResult | None] = {}
    for item in items_list:
        if item.metric_key in _PING_METRICS:
            ping_results_by_item[item.id] = _cached_ping_result(item, latest_ping_samples.get(item.id))
        elif item.metric_key in _PROMETHEUS_METRICS:
            prometheus_results_by_item[item.id] = _cached_prometheus_result(
                item, latest_prometheus_samples.get(item.id)
            )

    tiles: list[QuickStatusTileRead] = []
    if include_heartbeat_tiles:
        for backend_id in sorted(
            backend_ids,
            key=lambda current_backend_id: (
                getattr(backend_by_id.get(current_backend_id), "display_order", 0),
                getattr(backend_by_id.get(current_backend_id), "name", "").casefold(),
                current_backend_id,
            ),
        ):
            backend = backend_by_id.get(backend_id)
            latest_snapshot = latest_snapshots_by_backend.get(backend_id)
            freshness_seconds = max(getattr(backend, "poll_interval_seconds", 60) or 60, 30) * 3
            reference_at = getattr(backend, "last_seen_at", None) or getattr(latest_snapshot, "reported_at", None)
            reference_date = _coerce_utc(reference_at) if isinstance(reference_at, datetime) else None
            is_fresh = reference_date is not None and (summary_now - reference_date).total_seconds() <= freshness_seconds
            tiles.append(
                QuickStatusTileRead(
                    id=-backend_id,
                    backend_id=backend_id,
                    backend_display_order=getattr(backend, "display_order", 0),
                    backend_name=getattr(backend, "name", f"Backend #{backend_id}"),
                    label="HB",
                    metric_key="ssh_status",
                    value=1.0 if is_fresh else (0.0 if reference_date is not None else None),
                    display_value="Now" if is_fresh else ("Late" if reference_date is not None else "—"),
                    status="ok" if is_fresh else ("critical" if reference_date is not None else "unknown"),
                    stale=_is_reference_stale(reference_date, now=summary_now),
                    history_loaded=False,
                    has_details=True,
                    reported_at=reference_date,
                )
            )

    for item in items_list:
        backend = _loaded_relation(item, "backend")
        snapshot = latest_snapshots_by_backend.get(item.backend_id)
        ping_result = ping_results_by_item.get(item.id) if item.metric_key in _PING_METRICS else None
        prometheus_result = (
            prometheus_results_by_item.get(item.id) if item.metric_key in _PROMETHEUS_METRICS else None
        )
        value = _metric_value(snapshot, item.metric_key, item.mount_path) if snapshot else None
        status = _resolve_status(
            value,
            item.warning_threshold,
            item.critical_threshold,
            item.metric_key,
            prometheus_threshold_direction=item.prometheus_threshold_direction,
        )
        display_value = _format_item_value(item, value)
        reported_at = _coerce_utc(snapshot.reported_at) if snapshot and snapshot.reported_at is not None else None

        if item.metric_key in _PING_METRICS:
            ping_evaluation = _evaluate_ping_result(item, ping_result)
            value = ping_evaluation.value
            display_value = ping_evaluation.display_value
            status = ping_evaluation.status
            reported_at = _coerce_utc(ping_result.checked_at) if ping_result is not None else None
        elif item.metric_key in _PROMETHEUS_METRICS:
            prometheus_evaluation = _evaluate_prometheus_result(item, prometheus_result, now=summary_now)
            value = prometheus_evaluation.value
            display_value = prometheus_evaluation.display_value
            status = prometheus_evaluation.status
            reported_at = (
                _coerce_utc(prometheus_result.sampled_at)
                if prometheus_result is not None and prometheus_result.sampled_at is not None
                else (_coerce_utc(prometheus_result.checked_at) if prometheus_result is not None else None)
            )

        details = (
            _build_detail_lines(
                item,
                snapshot,
                status=status,
                prometheus_result=prometheus_result,
                prometheus_summary=None,
                now=summary_now,
            )
            if include_details
            else None
        )
        tiles.append(
            QuickStatusTileRead(
                id=item.id,
                backend_id=item.backend_id,
                backend_display_order=backend.display_order if backend else 0,
                backend_name=backend.name if backend else f"Backend #{item.backend_id}",
                label=item.label,
                metric_key=item.metric_key,
                display_variant=getattr(item, "display_variant", "tile"),
                display_unit=getattr(item, "display_unit", None),
                value=value,
                display_value=display_value,
                status=status,
                stale=_is_reference_stale(reported_at, now=summary_now),
                show_updated_age=item.show_updated_age,
                history_loaded=False,
                has_details=bool(_has_detail_content(item, snapshot) or item.metric_key in _PROMETHEUS_METRICS),
                reported_at=reported_at,
                details=details,
            )
        )

    return await _apply_quick_status_alert_suppression_to_tiles(
        session, items_list, tiles, now=summary_now, persist=persist_alert_state
    )


def detect_quick_status_transitions_for_snapshot(
    items: Iterable[QuickStatusItem],
    previous_snapshot: MetricSnapshot | None,
    current_snapshot: MetricSnapshot,
    backend_name: str,
) -> list[QuickStatusTransition]:
    transitions: list[QuickStatusTransition] = []
    for item in items:
        if not is_supported_quick_status_metric(getattr(item, "metric_key", None)):
            continue
        if item.metric_key in _PING_METRICS or item.metric_key in _PROMETHEUS_METRICS:
            continue
        previous_value = _metric_value(previous_snapshot, item.metric_key, item.mount_path) if previous_snapshot else None
        previous_status = _resolve_status(
            previous_value,
            item.warning_threshold,
            item.critical_threshold,
            item.metric_key,
            prometheus_threshold_direction=item.prometheus_threshold_direction,
            lower_warning_threshold=item.lower_warning_threshold,
            lower_critical_threshold=item.lower_critical_threshold,
        )
        current_value = _metric_value(current_snapshot, item.metric_key, item.mount_path)
        current_status = _resolve_status(
            current_value,
            item.warning_threshold,
            item.critical_threshold,
            item.metric_key,
            prometheus_threshold_direction=item.prometheus_threshold_direction,
            lower_warning_threshold=item.lower_warning_threshold,
            lower_critical_threshold=item.lower_critical_threshold,
        )
        if current_status in _ALERT_STATUSES and current_status != previous_status:
            transitions.append(
                QuickStatusTransition(
                    item_id=item.id,
                    backend_id=item.backend_id,
                    backend_name=backend_name,
                    label=item.label,
                    metric_key=item.metric_key,
                    previous_status=previous_status,
                    current_status=current_status,
                    display_value=_format_item_value(item, current_value),
                )
            )
    return transitions


async def create_quick_status_item(session: AsyncSession, payload: QuickStatusItemCreate) -> QuickStatusItem:
    item = QuickStatusItem(
        backend_id=payload.backend_id,
        label=payload.label,
        metric_key=payload.metric_key,
        mount_path=payload.mount_path,
        lower_warning_threshold=payload.lower_warning_threshold,
        lower_critical_threshold=payload.lower_critical_threshold,
        warning_threshold=payload.warning_threshold,
        critical_threshold=payload.critical_threshold,
        ping_endpoint=payload.ping_endpoint,
        ping_interval_seconds=payload.ping_interval_seconds,
        prometheus_url=payload.prometheus_url,
        prometheus_query=payload.prometheus_query,
        prometheus_interval_seconds=payload.prometheus_interval_seconds,
        prometheus_threshold_direction=payload.prometheus_threshold_direction,
        display_variant=payload.display_variant,
        display_unit=payload.display_unit,
        show_updated_age=payload.show_updated_age,
        display_order=payload.display_order,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    await rebuild_status_events_for_item(session, item)
    await session.commit()
    return item


async def update_quick_status_item(
    session: AsyncSession,
    item: QuickStatusItem,
    payload: QuickStatusItemCreate,
) -> QuickStatusItem:
    item.backend_id = payload.backend_id
    item.label = payload.label
    item.metric_key = payload.metric_key
    item.mount_path = payload.mount_path
    item.lower_warning_threshold = payload.lower_warning_threshold
    item.lower_critical_threshold = payload.lower_critical_threshold
    item.warning_threshold = payload.warning_threshold
    item.critical_threshold = payload.critical_threshold
    item.ping_endpoint = payload.ping_endpoint
    item.ping_interval_seconds = payload.ping_interval_seconds
    item.prometheus_url = payload.prometheus_url
    item.prometheus_query = payload.prometheus_query
    item.prometheus_interval_seconds = payload.prometheus_interval_seconds
    item.prometheus_threshold_direction = payload.prometheus_threshold_direction
    item.display_variant = payload.display_variant
    item.display_unit = payload.display_unit
    item.show_updated_age = payload.show_updated_age
    item.display_order = payload.display_order
    session.add(item)
    await session.commit()
    await session.refresh(item)
    await rebuild_status_events_for_item(session, item)
    await session.commit()
    return item
