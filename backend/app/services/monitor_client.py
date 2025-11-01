import httpx

from backend.app.core.config import settings


class MonitorClientError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


async def fetch_metrics(base_url: str, token: str) -> dict:
    """Fetch live metrics from a BackendMonitor instance."""
    base = base_url.rstrip('/')
    url = f"{base}/metrics"
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=settings.monitor_request_timeout_seconds) as client:
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
    async with httpx.AsyncClient(timeout=settings.monitor_request_timeout_seconds) as client:
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
