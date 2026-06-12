"""Regression: deleting a project must succeed when quick-status history references it."""

import pytest
from sqlalchemy import select

from backend.app.models.monitors import (
    Project,
    QuickStatusItem,
    QuickStatusPingSample,
    QuickStatusPrometheusSample,
)
from backend.app.services.backend_queries import delete_project


@pytest.mark.asyncio
async def test_delete_project_removes_quick_status_items(db_session):
    project = Project(name="to-delete", display_order=0)
    db_session.add(project)
    await db_session.flush()
    item = QuickStatusItem(
        project_id=project.id,
        label="Disk",
        metric_key="disk_usage_percent",
        warning_threshold=80,
        critical_threshold=90,
        display_order=0,
    )
    db_session.add(item)
    await db_session.commit()

    ok = await delete_project(db_session, project.id)
    assert ok is True

    remaining_project = await db_session.get(Project, project.id)
    assert remaining_project is None
    items = (await db_session.execute(select(QuickStatusItem).where(QuickStatusItem.project_id == project.id))).scalars().all()
    assert items == []


@pytest.mark.asyncio
async def test_delete_project_returns_false_when_missing(db_session):
    assert await delete_project(db_session, 999_999) is False


@pytest.mark.asyncio
async def test_delete_project_removes_linked_history(db_session):
    project = Project(name="with-history", display_order=0)
    db_session.add(project)
    await db_session.flush()

    item = QuickStatusItem(
        project_id=project.id,
        label="Latency",
        metric_key="ping_delay_ms",
        warning_threshold=100,
        critical_threshold=200,
        ping_endpoint="google.com",
        display_order=0,
    )
    db_session.add(item)
    await db_session.flush()

    db_session.add_all(
        [
            QuickStatusPingSample(
                quick_status_item_id=item.id,
                checked_at=item.created_at,
                success=True,
                latency_ms=42,
            ),
            QuickStatusPrometheusSample(
                quick_status_item_id=item.id,
                checked_at=item.created_at,
                sampled_at=item.created_at,
                value=12,
            ),
        ]
    )
    await db_session.commit()

    assert await delete_project(db_session, project.id) is True

    ping_rows = (await db_session.execute(select(QuickStatusPingSample))).scalars().all()
    prom_rows = (await db_session.execute(select(QuickStatusPrometheusSample))).scalars().all()

    assert ping_rows == []
    assert prom_rows == []
