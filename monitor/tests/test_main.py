from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from monitor.app import main
from monitor.app.schemas import MetricPayload


class DummyRepository:
    def __init__(self) -> None:
        self.recorded: list[MetricPayload] = []
        self.latest_payload: MetricPayload | None = None

    async def initialize(self) -> None:  # pragma: no cover - unused in tests
        return

    async def close(self) -> None:  # pragma: no cover - unused in tests
        return

    async def record(self, payload: MetricPayload) -> None:
        self.recorded.append(payload)

    async def latest(self) -> MetricPayload | None:
        return self.latest_payload


def _sample_metrics(**overrides: Any) -> dict[str, Any]:
    base = {
        "reported_at": datetime.now(tz=timezone.utc).isoformat(),
        "hostname": "test-node",
    }
    base.update(overrides)
    return base


@pytest.fixture
def client(monkeypatch):
    repo = DummyRepository()
    monkeypatch.setattr(main, "repository", repo, raising=False)
    app = main.create_app()
    with TestClient(app) as test_client:
        yield test_client, repo


def test_metrics_requires_bearer_token(client):
    test_client, _ = client

    response = test_client.get("/metrics")

    assert response.status_code == 401


def test_metrics_endpoint_records_payload(monkeypatch, client):
    test_client, repo = client

    monkeypatch.setattr(main.metrics, "collect_metrics", lambda: _sample_metrics(hostname="record-me"))

    response = test_client.get("/metrics", headers={"Authorization": "Bearer monitor-token"})

    assert response.status_code == 200
    data = response.json()
    assert data["metrics"]["hostname"] == "record-me"
    assert len(repo.recorded) == 1
    assert isinstance(repo.recorded[0], MetricPayload)


def test_latest_metrics_uses_cached_value(monkeypatch, client):
    test_client, repo = client
    repo.latest_payload = MetricPayload(
        reported_at=datetime.now(tz=timezone.utc),
        hostname="cached",
    )

    def fail_collect():
        pytest.fail("collect_metrics should not be invoked when cached payload exists")

    monkeypatch.setattr(main.metrics, "collect_metrics", fail_collect)

    response = test_client.get("/metrics/latest", headers={"Authorization": "Bearer monitor-token"})

    assert response.status_code == 200
    assert response.json()["metrics"]["hostname"] == "cached"


def test_latest_metrics_falls_back_to_collect(monkeypatch, client):
    test_client, repo = client
    repo.latest_payload = None

    monkeypatch.setattr(main.metrics, "collect_metrics", lambda: _sample_metrics(hostname="fresh"))

    response = test_client.get("/metrics/latest", headers={"Authorization": "Bearer monitor-token"})

    assert response.status_code == 200
    assert response.json()["metrics"]["hostname"] == "fresh"
