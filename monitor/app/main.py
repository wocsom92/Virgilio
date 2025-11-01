from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, status

from monitor.app import metrics
from monitor.app.config import settings
from monitor.app.schemas import MetricPayload, MetricResponse
from monitor.app.storage import repository


async def verify_token(authorization: str | None = Header(None)) -> None:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1]
    if token != settings.api_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await repository.initialize()
    yield
    await repository.close()


def create_app() -> FastAPI:
    application = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)

    @application.get("/healthz", tags=["meta"])
    async def health() -> dict[str, str]:  # pragma: no cover - simple endpoint
        return {"status": "ok"}

    @application.get("/metrics", response_model=MetricResponse, dependencies=[Depends(verify_token)])
    async def get_metrics() -> MetricResponse:
        raw = metrics.collect_metrics()
        payload = MetricPayload.model_validate({**raw, "raw_payload": raw})
        await repository.record(payload)
        return MetricResponse(metrics=payload)

    @application.post("/reboot", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(verify_token)])
    async def reboot_host() -> dict[str, str]:
        if not settings.allow_host_reboot:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Reboot disabled")
        try:
            await metrics.reboot_host()
            return {"status": "rebooting"}
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    @application.get("/metrics/latest", response_model=MetricResponse, dependencies=[Depends(verify_token)])
    async def latest_metrics() -> MetricResponse:
        latest = await repository.latest()
        if not latest:
            raw = metrics.collect_metrics()
            payload = MetricPayload.model_validate({**raw, "raw_payload": raw})
            return MetricResponse(metrics=payload)
        return MetricResponse(metrics=latest)

    return application


app = create_app()
