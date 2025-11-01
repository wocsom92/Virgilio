from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.core.config import settings
from backend.app.db.session import async_session_factory, engine
from backend.app.models.base import Base
from backend.app.routers import auth, backends, dashboard, metrics, telegram
from backend.app.routers import system
from backend.app.services.backend_poller import BackendPoller
from backend.app.version import BACKEND_VERSION
from backend.app.services.reboot_service import notify_reboot_recovery
from backend.app.db.schema_compat import ensure_schema_compat


poller = BackendPoller(async_session_factory)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create database tables on startup for convenience."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(ensure_schema_compat)
    await poller.start()
    async with async_session_factory() as session:
        await notify_reboot_recovery(session)
    try:
        yield
    finally:
        await poller.stop()


def create_app() -> FastAPI:
    application = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)

    allow_origins = settings.cors_allow_origins or ["*"]
    application.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.include_router(auth.router)
    application.include_router(backends.router)
    application.include_router(metrics.router)
    application.include_router(dashboard.router)
    application.include_router(system.router)
    application.include_router(telegram.router)

    @application.get("/healthz", tags=["meta"])
    async def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/version", tags=["meta"])
    async def version() -> dict[str, str]:
        return {"version": BACKEND_VERSION}

    return application


app = create_app()
