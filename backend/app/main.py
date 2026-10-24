from contextlib import asynccontextmanager
import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import ORJSONResponse
from sqlalchemy import text

from backend.app.core.config import settings
from backend.app.db.session import async_session_factory, engine
from backend.app.db.schema_compat import ensure_schema_compat
from backend.app.models.base import Base
from backend.app.routers import auth, backends, dashboard, metrics, notifications, telegram
from backend.app.routers import system
from backend.app.services.backend_poller import BackendPoller
from backend.app.services.http_metrics import install_http_metrics
from backend.app.services.http_clients import close_http_clients
from backend.app.version import BACKEND_VERSION
from backend.app.services.reboot_service import notify_reboot_recovery
from backend.app.services.system_settings import get_system_settings
from backend.app.services.telegram_settings import get_or_create_settings


poller = BackendPoller(async_session_factory)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create database tables on startup for convenience."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(ensure_schema_compat)
    # Seed the singleton settings rows once here so dashboard reads never have to create them.
    async with async_session_factory() as session:
        await get_system_settings(session)
        await get_or_create_settings(session)
    await poller.start()
    async with async_session_factory() as session:
        try:
            await notify_reboot_recovery(session)
        except Exception as exc:  # pragma: no cover - startup resilience
            logger.warning("Reboot recovery notification failed during startup: %s", exc)

    bot_stop = asyncio.Event()
    bot_task: asyncio.Task | None = None
    if settings.telegram_bot_enabled and settings.telegram_bot_token:
        from backend.app.bot import health as bot_health
        from backend.app.bot.polling import run_bot_with_retry

        bot_health.mark_enabled(True)
        bot_task = asyncio.create_task(run_bot_with_retry(bot_stop), name="telegram-bot")
    try:
        yield
    finally:
        bot_stop.set()
        if bot_task is not None:
            try:
                await asyncio.wait_for(bot_task, timeout=15)
            except (asyncio.TimeoutError, asyncio.CancelledError):  # pragma: no cover - shutdown resilience
                bot_task.cancel()
            except Exception as exc:  # pragma: no cover - shutdown resilience
                logger.warning("Telegram bot task ended with an error: %s", exc)
        await poller.stop()
        await close_http_clients()


def create_app() -> FastAPI:
    application = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        lifespan=lifespan,
        default_response_class=ORJSONResponse,
    )
    # Outermost first: gzip wraps everything, the request context middleware logs/counts inside it.
    application.add_middleware(GZipMiddleware, minimum_size=1024)
    install_http_metrics(application, service_name="backend")

    allow_origins = settings.cors_allow_origins
    if allow_origins:
        allow_credentials = "*" not in allow_origins
        application.add_middleware(
            CORSMiddleware,
            allow_origins=allow_origins,
            allow_credentials=allow_credentials,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    application.include_router(auth.router)
    application.include_router(backends.router)
    application.include_router(metrics.router)
    application.include_router(dashboard.router)
    application.include_router(notifications.router)
    application.include_router(system.router)
    application.include_router(telegram.router)

    @application.get("/healthz", tags=["meta"])
    async def healthcheck() -> dict[str, str]:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            db_status = "ok"
        except Exception as exc:  # pragma: no cover - surfaced through the health payload
            logger.warning("Health check database probe failed: %s", exc)
            db_status = "error"
        return {"status": "ok" if db_status == "ok" else "degraded", "db": db_status}

    @application.get("/version", tags=["meta"])
    async def version() -> dict[str, str]:
        return {"version": BACKEND_VERSION}

    return application


app = create_app()
