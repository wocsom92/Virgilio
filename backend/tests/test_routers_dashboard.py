from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from backend.app.core.security import get_current_user
from backend.app.db.session import get_session
from backend.app.main import app
from backend.app.models.monitors import MetricSnapshot, MonitoredBackend, QuickStatusItem
from backend.app.models.users import User
from backend.app.services.system_settings import get_system_settings
from backend.app.services.telegram_settings import get_or_create_settings


class _CommitSpy:
    def __init__(self, session):
        self._session = session
        self.commits = 0

    def __getattr__(self, name):
        return getattr(self._session, name)

    async def commit(self):
        self.commits += 1
        await self._session.commit()


@pytest_asyncio.fixture
async def client(db_session):
    spy = _CommitSpy(db_session)

    async def _session_override():
        yield spy

    async def _user_override():
        return User(id=1, username="admin", hashed_password="x", role="admin")

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_current_user] = _user_override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http, spy
    app.dependency_overrides.clear()


async def _seed(session):
    # Mirrors the lifespan: settings singletons exist before any request is served.
    await get_system_settings(session)
    await get_or_create_settings(session)
    now = datetime.now(tz=timezone.utc)
    backend = MonitoredBackend(
        name="alpha", base_url="http://alpha", api_token="token-alpha", display_order=1,
        selected_metrics={"network_interfaces": ["eth0"]},
    )
    session.add(backend)
    await session.flush()
    item = QuickStatusItem(
        backend_id=backend.id, label="RAM", metric_key="ram_used_percent", warning_threshold=80, critical_threshold=90,
        display_order=0,
    )
    session.add(item)
    for index in range(700):
        session.add(
            MetricSnapshot(
                backend_id=backend.id,
                reported_at=now - timedelta(minutes=index),
                raw_payload={"ssh_last_failure_username": "root", "top_processes": []},
                ram_used_percent=40.0 + (index % 10),
                network_counters=[{"interface": "eth0", "bytes_sent": 1000 * (700 - index), "bytes_recv": 0}],
                uptime_seconds=100_000 + index,
            )
        )
    await session.commit()
    return backend, item


@pytest.mark.asyncio
async def test_dashboard_list_has_no_raw_payload(client, db_session):
    http, _ = client
    await _seed(db_session)
    response = await http.get("/dashboard/")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["latest_snapshot"]["ram_used_percent"] is not None
    assert "raw_payload" not in body[0]["latest_snapshot"]
    assert body[0]["api_token"] is None


@pytest.mark.asyncio
async def test_quick_status_reads_never_commit(client, db_session):
    http, spy = client
    await _seed(db_session)
    spy.commits = 0
    for path in ("/dashboard/quick-status", "/dashboard/quick-status/history", "/dashboard/site-monitors"):
        response = await http.get(path)
        assert response.status_code == 200, path
    assert spy.commits == 0


@pytest.mark.asyncio
async def test_series_is_downsampled_to_max_points(client, db_session):
    http, _ = client
    backend, _ = await _seed(db_session)
    response = await http.get(f"/dashboard/{backend.id}/series", params={"range_name": "daily"})
    assert response.status_code == 200
    body = response.json()
    assert len(body["points"]) <= 300
    assert body["bucket_seconds"] == 288  # 86400 / 300
    assert body["points"][-1]["network_bps"][0]["interface"] == "eth0"

    response = await http.get(f"/dashboard/{backend.id}/series", params={"range_name": "daily", "max_points": 50})
    assert len(response.json()["points"]) <= 50


@pytest.mark.asyncio
async def test_latest_snapshot_requires_a_user(client, db_session):
    http, _ = client
    backend, _ = await _seed(db_session)
    app.dependency_overrides.pop(get_current_user, None)
    response = await http.get(f"/metrics/{backend.id}/latest")
    assert response.status_code == 401
