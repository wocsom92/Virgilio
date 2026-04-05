from __future__ import annotations

import uvicorn

from monitor.app.config import settings
from monitor.app.logging import build_logging_config


def main() -> None:
    uvicorn.run(
        "monitor.app.main:app",
        host="0.0.0.0",
        port=9000,
        access_log=False,
        log_config=build_logging_config(service_name=settings.log_service, level=settings.log_level),
    )


if __name__ == "__main__":
    main()
