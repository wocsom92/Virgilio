from sqlalchemy import select

from backend.app.models.monitors import Project, QuickStatusItem
from backend.app.services.self_monitoring import PROJECT_NAME, ensure_self_monitoring_project


async def test_ensure_self_monitoring_project_creates_project_and_items(db_session):
    await ensure_self_monitoring_project(db_session)
    await ensure_self_monitoring_project(db_session)

    projects = (await db_session.execute(select(Project).where(Project.name == PROJECT_NAME))).scalars().all()
    items = (
        await db_session.execute(
            select(QuickStatusItem).join(Project, QuickStatusItem.project_id == Project.id).where(Project.name == PROJECT_NAME)
        )
    ).scalars().all()

    assert len(projects) == 1
    assert len(items) == 3
    assert all(item.metric_key == "prometheus_query" for item in items)
    assert all(item.display_variant == "self_monitoring" for item in items)


async def test_ensure_self_monitoring_project_replaces_legacy_items(db_session):
    project = Project(name=PROJECT_NAME, display_order=1)
    db_session.add(project)
    await db_session.flush()
    db_session.add(
        QuickStatusItem(
            project_id=project.id,
            label="Backend API calls",
            metric_key="prometheus_query",
            warning_threshold=1,
            critical_threshold=2,
            prometheus_url="http://prometheus:9090",
            prometheus_query="up",
            display_variant="graph",
            display_order=0,
        )
    )
    await db_session.commit()

    await ensure_self_monitoring_project(db_session)

    items = (
        await db_session.execute(
            select(QuickStatusItem).join(Project, QuickStatusItem.project_id == Project.id).where(Project.name == PROJECT_NAME)
        )
    ).scalars().all()

    labels = {item.label for item in items}
    assert "Backend API calls" not in labels
    assert labels == {"Total requests", "Request peaks", "Calls by endpoint"}
