from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.monitors import Project, QuickStatusItem


PROJECT_NAME = "Self-monitoring"
PROMETHEUS_URL = "http://prometheus:9090"
TOTALS_LABEL = "Total requests"
PEAKS_LABEL = "Request peaks"
ENDPOINTS_LABEL = "Calls by endpoint"
_INTERVAL_SECONDS = 300

_DEFAULT_ITEMS: tuple[dict[str, object], ...] = (
    {
        "label": TOTALS_LABEL,
        "prometheus_query": "sum(increase(server_monitor_http_requests_total[1h]))",
        "warning_threshold": 1_000_000,
        "critical_threshold": 2_000_000,
        "display_unit": "req/h",
    },
    {
        "label": PEAKS_LABEL,
        "prometheus_query": 'max(sum by (service) (rate(server_monitor_http_requests_total[5m]))) * 60',
        "warning_threshold": 1_000_000,
        "critical_threshold": 2_000_000,
        "display_unit": "req/min",
    },
    {
        "label": ENDPOINTS_LABEL,
        "prometheus_query": 'max(sum by (service, handler) (increase(server_monitor_http_requests_total[1h])))',
        "warning_threshold": 1_000_000,
        "critical_threshold": 2_000_000,
        "display_unit": "req/h",
    },
)

_LEGACY_LABELS = {
    "Backend API calls",
    "Backend HTTP 200",
    "Backend HTTP 4xx",
    "Backend HTTP 5xx",
    "Monitor API calls",
    "Monitor HTTP 200",
    "Monitor HTTP 4xx",
    "Monitor HTTP 5xx",
}


async def ensure_self_monitoring_project(session: AsyncSession) -> None:
    changed = False
    project = await session.scalar(select(Project).where(Project.name == PROJECT_NAME))
    if project is None:
        max_display_order = await session.scalar(select(func.max(Project.display_order)))
        project = Project(name=PROJECT_NAME, display_order=(max_display_order or 0) + 1)
        session.add(project)
        await session.flush()
        changed = True

    existing_items = await session.execute(select(QuickStatusItem).where(QuickStatusItem.project_id == project.id))
    items_by_label = {item.label: item for item in existing_items.scalars()}

    for legacy_label in _LEGACY_LABELS:
        legacy_item = items_by_label.get(legacy_label)
        if legacy_item is None:
            continue
        await session.delete(legacy_item)
        changed = True

    for index, item_spec in enumerate(_DEFAULT_ITEMS):
        label = str(item_spec["label"])
        item = items_by_label.get(label)
        if item is None:
            item = QuickStatusItem(
                backend_id=None,
                project_id=project.id,
                label=label,
                metric_key="prometheus_query",
                warning_threshold=float(item_spec["warning_threshold"]),
                critical_threshold=float(item_spec["critical_threshold"]),
                prometheus_url=PROMETHEUS_URL,
                prometheus_query=str(item_spec["prometheus_query"]),
                prometheus_interval_seconds=_INTERVAL_SECONDS,
                prometheus_threshold_direction="higher",
                display_variant="self_monitoring",
                display_unit=str(item_spec["display_unit"]),
                show_updated_age=True,
                display_order=index,
            )
            session.add(item)
            changed = True
            continue

        next_values = {
            "metric_key": "prometheus_query",
            "warning_threshold": float(item_spec["warning_threshold"]),
            "critical_threshold": float(item_spec["critical_threshold"]),
            "prometheus_url": PROMETHEUS_URL,
            "prometheus_query": str(item_spec["prometheus_query"]),
            "prometheus_interval_seconds": _INTERVAL_SECONDS,
            "prometheus_threshold_direction": "higher",
            "display_variant": "self_monitoring",
            "display_unit": str(item_spec["display_unit"]),
            "show_updated_age": True,
            "display_order": index,
        }
        for field_name, field_value in next_values.items():
            if getattr(item, field_name) != field_value:
                setattr(item, field_name, field_value)
                changed = True

    if changed:
        await session.commit()
