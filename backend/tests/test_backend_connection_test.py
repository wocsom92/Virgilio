"""POST /backends/test-connection: one-off monitor probe used by the admin "Add server" flow."""

import errno
import json
import socket
import ssl

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from backend.app.core.security import get_current_user
from backend.app.db.session import get_session
from backend.app.main import app
from backend.app.models.monitors import MonitoredBackend
from backend.app.models.users import User
from backend.app.services import monitor_client


def _chained(outer: BaseException, inner: BaseException) -> BaseException:
    outer.__cause__ = inner
    return outer


SAMPLE_METRICS = {
    "metrics": {
        "reported_at": "2026-10-24T10:00:00+00:00",
        "hostname": "raspi5",
        "monitor_version": "9.1.0",
        "os_version": "Debian 12",
        "cpu_temperature_c": 47.3,
        "ram_used_percent": 31.5,
        "disk_usage_percent": 12.0,
        "uptime_seconds": 86400,
    }
}


@pytest_asyncio.fixture
async def client(db_session):
    async def _session_override():
        yield db_session

    async def _user_override():
        return User(id=1, username="admin", hashed_password="x", role="admin")

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_current_user] = _user_override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


@pytest.fixture
def monitor_transport(monkeypatch):
    """Replace the shared monitor HTTP client with one backed by a MockTransport."""

    state: dict = {"handler": None, "requests": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        return state["handler"](request)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(monitor_client, "get_monitor_http_client", lambda: mock_client)

    def use(fn):
        state["handler"] = fn
        return state

    yield use


async def _post(client, **body):
    response = await client.post("/backends/test-connection", json=body)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_success_reports_host_details(client, monitor_transport):
    state = monitor_transport(lambda request: httpx.Response(200, json=SAMPLE_METRICS))

    data = await _post(client, base_url="http://pi.local:29000/", api_token="monitor-token-xyz")

    assert data["ok"] is True
    assert data["base_url"] == "http://pi.local:29000"
    assert data["hostname"] == "raspi5"
    assert data["monitor_version"] == "9.1.0"
    assert data["cpu_temperature_c"] == 47.3
    assert data["ram_used_percent"] == 31.5
    assert data["uptime_seconds"] == 86400
    assert data["error"] is None and data["hint"] is None
    assert isinstance(data["latency_ms"], int)

    request = state["requests"][0]
    assert str(request.url) == "http://pi.local:29000/metrics"
    assert request.headers["Authorization"] == "Bearer monitor-token-xyz"


@pytest.mark.asyncio
async def test_wrong_token_is_reported_as_unauthorized(client, monitor_transport):
    monitor_transport(lambda request: httpx.Response(401, json={"detail": "Invalid token"}))

    data = await _post(client, base_url="http://pi.local:29000", api_token="wrong-token-value")

    assert data["ok"] is False
    assert data["error_kind"] == "unauthorized"
    assert "MONITOR_API_TOKEN" in data["hint"]
    assert data["hostname"] is None


@pytest.mark.asyncio
async def test_missing_metrics_route_is_not_found(client, monitor_transport):
    monitor_transport(lambda request: httpx.Response(404, text="Not Found"))

    data = await _post(client, base_url="http://pi.local:29000/api", api_token="monitor-token-xyz")

    assert data["ok"] is False
    assert data["error_kind"] == "not_found"


@pytest.mark.asyncio
async def test_html_response_is_invalid_response(client, monitor_transport):
    monitor_transport(lambda request: httpx.Response(200, text="<html><body>hi</body></html>"))

    data = await _post(client, base_url="http://pi.local:80", api_token="monitor-token-xyz")

    assert data["ok"] is False
    assert data["error_kind"] == "invalid_response"


@pytest.mark.asyncio
async def test_json_without_metrics_is_invalid_response(client, monitor_transport):
    monitor_transport(lambda request: httpx.Response(200, json={"status": "ok"}))

    data = await _post(client, base_url="http://pi.local:8000", api_token="monitor-token-xyz")

    assert data["ok"] is False
    assert data["error_kind"] == "invalid_response"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "expected_kind"),
    [
        (httpx.ConnectError("[Errno 61] Connection refused"), "refused"),
        (httpx.ConnectError("[Errno 111] Connection refused"), "refused"),
        (httpx.ConnectError("[Errno 8] nodename nor servname provided, or not known"), "dns"),
        (httpx.ConnectError("[Errno -2] Name or service not known"), "dns"),
        (httpx.ConnectError("[Errno 65] No route to host"), "unreachable"),
        (httpx.ConnectError("[SSL: WRONG_VERSION_NUMBER] wrong version number"), "tls"),
        (httpx.ConnectTimeout("timed out"), "timeout"),
        (httpx.ReadTimeout("timed out"), "timeout"),
    ],
)
async def test_transport_errors_are_classified(client, monitor_transport, exc, expected_kind):
    def raise_error(request):
        raise exc

    monitor_transport(raise_error)

    data = await _post(client, base_url="http://pi.local:29000", api_token="monitor-token-xyz")

    assert data["ok"] is False
    assert data["error_kind"] == expected_kind
    assert data["hint"]
    assert data["error"]


def _wrapped_connect_error() -> httpx.ConnectError:
    """Mimic httpx on a host with IPv4+IPv6: the refused errors sit inside an ExceptionGroup."""
    group = BaseExceptionGroup(
        "multiple connection attempts failed",
        [ConnectionRefusedError(61, "Connect call failed ('::1', 19001)"), ConnectionRefusedError(61, "Connect call failed")],
    )
    os_error = OSError("All connection attempts failed")
    os_error.__cause__ = group
    exc = httpx.ConnectError("All connection attempts failed")
    exc.__cause__ = os_error
    return exc


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "expected_kind"),
    [
        (_wrapped_connect_error(), "refused"),
        (_chained(httpx.ConnectError("boom"), socket.gaierror(8, "nodename nor servname provided")), "dns"),
        (_chained(httpx.ConnectError("boom"), OSError(errno.EHOSTUNREACH, "No route to host")), "unreachable"),
        (_chained(httpx.ConnectError("boom"), ssl.SSLError(1, "wrong version number")), "tls"),
    ],
)
async def test_wrapped_os_errors_are_classified_by_type(client, monitor_transport, exc, expected_kind):
    def raise_error(request):
        raise exc

    monitor_transport(raise_error)

    data = await _post(client, base_url="http://localhost:19001", api_token="monitor-token-xyz")

    assert data["error_kind"] == expected_kind


@pytest.mark.asyncio
async def test_stored_token_is_reused_when_editing(client, monitor_transport, db_session):
    backend = MonitoredBackend(name="pi", base_url="http://pi.local:29000", api_token="stored-token-123", display_order=0)
    db_session.add(backend)
    await db_session.commit()
    state = monitor_transport(lambda request: httpx.Response(200, json=SAMPLE_METRICS))

    data = await _post(client, base_url="http://pi.local:29000", backend_id=backend.id)

    assert data["ok"] is True
    assert state["requests"][0].headers["Authorization"] == "Bearer stored-token-123"


@pytest.mark.asyncio
async def test_missing_token_is_a_validation_error(client, monitor_transport):
    monitor_transport(lambda request: httpx.Response(200, json=SAMPLE_METRICS))

    response = await client.post("/backends/test-connection", json={"base_url": "http://pi.local:29000"})

    assert response.status_code == 422
    assert "MONITOR_API_TOKEN" in response.json()["detail"]


@pytest.mark.asyncio
async def test_url_without_scheme_is_rejected(client, monitor_transport):
    response = await client.post(
        "/backends/test-connection", json={"base_url": "pi.local:29000", "api_token": "monitor-token-xyz"}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_viewers_are_forbidden(db_session, monitor_transport):
    async def _session_override():
        yield db_session

    async def _viewer():
        return User(id=2, username="viewer", hashed_password="x", role="viewer")

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_current_user] = _viewer
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            response = await http.post(
                "/backends/test-connection", json={"base_url": "http://pi.local:29000", "api_token": "monitor-token-xyz"}
            )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403
    assert json.loads(response.text)["detail"] == "Admin privileges required"
