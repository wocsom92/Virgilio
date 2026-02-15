from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import ping3


ping3.EXCEPTIONS = True


async def check_ping(target: str, timeout_seconds: int) -> tuple[bool, float | None, datetime]:
    def _run_ping() -> float | None:
        return ping3.ping(target, timeout=timeout_seconds)

    now = datetime.now(tz=timezone.utc)
    try:
        result = await asyncio.to_thread(_run_ping)
    except Exception:
        result = None

    success = result is not None
    latency_ms = float(result) * 1000 if result is not None else None
    return success, latency_ms, now
