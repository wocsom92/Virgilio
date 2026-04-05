from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.services.http_metrics import install_http_metrics


def test_http_metrics_collect_status_classes_and_skip_prometheus_scrape():
    app = FastAPI()
    install_http_metrics(app, service_name="backend-test")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    with TestClient(app) as client:
        ok_response = client.get("/healthz")
        missing_response = client.get("/missing")
        metrics_response = client.get("/prometheus")

    assert ok_response.status_code == 200
    assert missing_response.status_code == 404
    assert metrics_response.status_code == 200
    assert (
        'server_monitor_http_requests_total{handler="/healthz",method="GET",service="backend-test",status_class="2xx",status_code="200"} 1.0'
        in metrics_response.text
    )
    assert (
        'server_monitor_http_requests_total{handler="unmatched",method="GET",service="backend-test",status_class="4xx",status_code="404"} 1.0'
        in metrics_response.text
    )
    assert 'handler="/prometheus",method="GET",service="backend-test"' not in metrics_response.text
