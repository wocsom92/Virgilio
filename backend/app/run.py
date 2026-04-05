from __future__ import annotations

import uvicorn

from backend.app.core.config import settings
from backend.app.core.logging import build_logging_config


def main() -> None:
    uvicorn.run(
        "backend.app.main:app",
        host="0.0.0.0",
        port=8000,
        access_log=False,
        log_config=build_logging_config(service_name=settings.log_service, level=settings.log_level),
    )


if __name__ == "__main__":
    main()
