import errno
import socket
import ssl
import time

import httpx

from backend.app.core.config import settings
from backend.app.services.http_clients import get_monitor_http_client


class MonitorClientError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


async def fetch_metrics(base_url: str, token: str) -> dict:
    """Fetch live metrics from a BackendMonitor instance."""
    base = base_url.rstrip('/')
    url = f"{base}/metrics"
    headers = {"Authorization": f"Bearer {token}"}
    client = get_monitor_http_client()
    try:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:200]
        raise MonitorClientError(
            f"Monitor responded with {exc.response.status_code}: {body}",
            status_code=exc.response.status_code,
        ) from exc
    except httpx.RequestError as exc:
        raise MonitorClientError(f"Could not reach monitor: {exc}") from exc
    return response.json()


async def fetch_ping(base_url: str, token: str, target: str, timeout_seconds: int) -> dict:
    """Fetch ping result from a BackendMonitor instance."""
    base = base_url.rstrip('/')
    url = f"{base}/ping"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"target": target, "timeout_seconds": timeout_seconds}
    client = get_monitor_http_client()
    try:
        response = await client.get(url, headers=headers, params=params)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:200]
        raise MonitorClientError(
            f"Monitor responded with {exc.response.status_code}: {body}",
            status_code=exc.response.status_code,
        ) from exc
    except httpx.RequestError as exc:
        raise MonitorClientError(f"Could not reach monitor: {exc}") from exc
    return response.json()


async def request_monitor_reboot(base_url: str, token: str) -> None:
    """Request a reboot on the monitor agent."""
    base = base_url.rstrip('/')
    base_variants = [base]
    if base.endswith("/api"):
        base_variants.append(base[:-4] or "/")
    # Deduplicate bases
    seen_bases = set()
    normalized_bases: list[str] = []
    for candidate in base_variants:
        if candidate not in seen_bases:
            seen_bases.add(candidate)
            normalized_bases.append(candidate)

    targets = []
    for root in normalized_bases:
        targets.append(f"{root}/reboot")
        targets.append(f"{root}/api/reboot")

    headers = {"Authorization": f"Bearer {token}"}
    client = get_monitor_http_client()
    errors: list[str] = []
    for target in targets:
        try:
            response = await client.post(target, headers=headers)
            response.raise_for_status()
            return
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                errors.append(f"{target} returned 404")
                continue
            body = exc.response.text[:200]
            raise MonitorClientError(
                f"Monitor reboot failed {exc.response.status_code}: {body}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.RequestError as exc:
            raise MonitorClientError(f"Could not reach monitor: {exc}") from exc

    detail = "; ".join(errors) if errors else "monitor reboot endpoint missing"
    raise MonitorClientError(f"Monitor reboot failed 404: {detail}", status_code=404)


MONITOR_PROBE_TIMEOUT_SECONDS = 8.0

ProbeErrorKind = str  # dns | refused | unreachable | timeout | tls | unauthorized | not_found | http | invalid_response


class MonitorProbeResult:
    """Outcome of a one-off connection test against a monitor agent."""

    __slots__ = ("ok", "latency_ms", "payload", "error_kind", "error", "hint")

    def __init__(
        self,
        *,
        ok: bool,
        latency_ms: int | None = None,
        payload: dict | None = None,
        error_kind: ProbeErrorKind | None = None,
        error: str | None = None,
        hint: str | None = None,
    ) -> None:
        self.ok = ok
        self.latency_ms = latency_ms
        self.payload = payload
        self.error_kind = error_kind
        self.error = error
        self.hint = hint


_PROBE_HINTS: dict[str, str] = {
    "dns": "The hostname could not be resolved. Check the spelling, or use the server's IP address instead.",
    "refused": "The host answered but nothing is listening on that port. Make sure the monitor agent container is running "
    "and that the port in the URL matches MONITOR_PORT (29000 by default).",
    "unreachable": "No route to the host. Check that the server is online and reachable from the backend's network "
    "(VPN, firewall, Docker network).",
    "timeout": "The host did not answer in time. A firewall may be dropping the connection, or the URL points to the "
    "wrong address.",
    "tls": "The TLS handshake failed. Use http:// unless the agent sits behind an HTTPS proxy with a valid certificate.",
    "unauthorized": "The agent rejected the token. It must match MONITOR_API_TOKEN in the agent's environment.",
    "not_found": "The URL answered but has no /metrics endpoint. Point it at the agent's root, e.g. http://host:29000 "
    "(no trailing path).",
    "http": "The agent returned an unexpected HTTP status. Check the agent logs with `docker compose logs monitor`.",
    "invalid_response": "The URL answered but did not return monitor metrics. It is probably a different service "
    "(web server, dashboard) rather than the monitor agent.",
}


def _iter_exception_chain(exc: BaseException):
    """Yield `exc` and everything it wraps: causes, contexts and ExceptionGroup members."""
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        members = getattr(current, "exceptions", None)
        if isinstance(members, (list, tuple)):
            stack.extend(member for member in members if isinstance(member, BaseException))
        for attr in ("__cause__", "__context__"):
            nested = getattr(current, attr, None)
            if isinstance(nested, BaseException):
                stack.append(nested)


_UNREACHABLE_ERRNOS = {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN}


def classify_request_error(exc: httpx.RequestError) -> ProbeErrorKind:
    """Map an httpx transport error onto a user-facing category.

    httpx wraps the OS error several layers deep (and, when a hostname resolves
    to several addresses, inside an ExceptionGroup), so the whole chain is
    inspected by exception type before falling back to message heuristics.
    """
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"

    texts: list[str] = []
    for item in _iter_exception_chain(exc):
        if isinstance(item, ssl.SSLError):
            return "tls"
        if isinstance(item, socket.gaierror):
            return "dns"
        if isinstance(item, ConnectionRefusedError):
            return "refused"
        if isinstance(item, (socket.timeout, TimeoutError)):
            return "timeout"
        if isinstance(item, OSError) and item.errno in _UNREACHABLE_ERRNOS:
            return "unreachable"
        texts.append(str(item).lower())

    combined = " ".join(texts)
    if "ssl" in combined or "certificate" in combined or "tls" in combined:
        return "tls"
    if any(
        marker in combined
        for marker in (
            "name or service not known",
            "nodename nor servname",
            "getaddrinfo",
            "temporary failure in name resolution",
            "name resolution",
            "no address associated",
            "[errno 8]",
            "[errno -2]",
            "[errno -3]",
            "[errno -5]",
        )
    ):
        return "dns"
    if "connection refused" in combined or "[errno 61]" in combined or "[errno 111]" in combined:
        return "refused"
    if "timed out" in combined:
        return "timeout"
    return "unreachable"


def probe_hint(kind: ProbeErrorKind | None) -> str | None:
    return _PROBE_HINTS.get(kind or "")


async def probe_monitor(
    base_url: str,
    token: str,
    *,
    timeout_seconds: float = MONITOR_PROBE_TIMEOUT_SECONDS,
) -> MonitorProbeResult:
    """Try to fetch `/metrics` once and explain precisely what went wrong if it failed.

    Unlike `fetch_metrics` this never raises for network or HTTP problems: the
    admin UI shows the categorised outcome inline while the user fixes the form.
    """
    base = base_url.rstrip("/")
    url = f"{base}/metrics"
    headers = {"Authorization": f"Bearer {token}"}
    client = get_monitor_http_client()
    started = time.perf_counter()
    try:
        response = await client.get(url, headers=headers, timeout=timeout_seconds)
    except httpx.RequestError as exc:
        kind = classify_request_error(exc)
        detail = str(exc).strip() or exc.__class__.__name__
        return MonitorProbeResult(ok=False, error_kind=kind, error=detail[:300], hint=probe_hint(kind))
    latency_ms = int((time.perf_counter() - started) * 1000)

    status_code = response.status_code
    if status_code in (401, 403):
        return MonitorProbeResult(
            ok=False,
            latency_ms=latency_ms,
            error_kind="unauthorized",
            error=f"Monitor responded with {status_code}",
            hint=probe_hint("unauthorized"),
        )
    if status_code == 404:
        return MonitorProbeResult(
            ok=False,
            latency_ms=latency_ms,
            error_kind="not_found",
            error="Monitor responded with 404 for /metrics",
            hint=probe_hint("not_found"),
        )
    if status_code >= 400:
        body = response.text[:200].strip()
        return MonitorProbeResult(
            ok=False,
            latency_ms=latency_ms,
            error_kind="http",
            error=f"Monitor responded with {status_code}" + (f": {body}" if body else ""),
            hint=probe_hint("http"),
        )

    try:
        data = response.json()
    except ValueError:
        return MonitorProbeResult(
            ok=False,
            latency_ms=latency_ms,
            error_kind="invalid_response",
            error="The response was not JSON",
            hint=probe_hint("invalid_response"),
        )
    metrics = data.get("metrics") if isinstance(data, dict) else None
    if not isinstance(metrics, dict) or "reported_at" not in metrics:
        return MonitorProbeResult(
            ok=False,
            latency_ms=latency_ms,
            error_kind="invalid_response",
            error="The response did not contain a metrics payload",
            hint=probe_hint("invalid_response"),
        )
    return MonitorProbeResult(ok=True, latency_ms=latency_ms, payload=metrics)
