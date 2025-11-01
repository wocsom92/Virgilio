from __future__ import annotations

import asyncio
import os
import logging
import shlex
import shutil
import subprocess
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.models.monitors import RebootEvent
from backend.app.services.telegram_notifications import resolve_message_context
from backend.app.services.telegram_service import TelegramError, send_message


logger = logging.getLogger(__name__)


def _try_sysrq_reboot() -> bool:
    """Fallback reboot mechanism using sysrq trigger (host must allow it)."""
    try:
        with open("/proc/sysrq-trigger", "w") as handle:
            handle.write("b")
        return True
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Sysrq reboot trigger failed: %s", exc)
        return False


async def _run_reboot_command() -> None:
    """Spawn the configured reboot command in a worker thread."""
    if not settings.reboot_command:
        if _try_sysrq_reboot():
            return
        raise RuntimeError("No reboot command configured")

    args = shlex.split(settings.reboot_command)
    host_root = os.getenv("MONITOR_HOST_ROOT_TARGET", "/hostfs")

    def _candidate_commands() -> list[list[str]]:
        commands: list[list[str]] = []
        if args:
            commands.append(args)

        common = [
            "/sbin/shutdown",
            "/usr/sbin/shutdown",
            "/sbin/reboot",
            "/usr/sbin/reboot",
        ]

        for base in common:
            commands.append([base, *args[1:]])
            if host_root:
                commands.append([os.path.join(host_root, base.lstrip("/")), *args[1:]])

        chroot_bin = shutil.which("chroot")
        if chroot_bin and host_root:
            for base in common:
                commands.append([chroot_bin, host_root, base, *args[1:]])

        seen = set()
        unique: list[list[str]] = []
        for cmd in commands:
            key = " ".join(cmd)
            if key not in seen:
                seen.add(key)
                unique.append(cmd)
        return unique

    candidates = _candidate_commands()
    errors: list[str] = []

    for attempt_args in candidates:
        def _execute():
            return subprocess.run(
                attempt_args,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )

        result = await asyncio.to_thread(_execute)
        if result.returncode in (0, None):
            return
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        output = stderr or stdout or str(result.returncode)
        errors.append(f"{' '.join(attempt_args)}: {output}")

    if _try_sysrq_reboot():
        return

    detail = "; ".join(errors) if errors else "No executable reboot command found."
    raise RuntimeError(f"Reboot command failed. Attempts: {len(candidates)}. Details: {detail}")


async def _notify_reboot_requested(
    session: AsyncSession,
    event: RebootEvent,
    reason: str | None,
) -> None:
    settings_model, target_chat = await resolve_message_context(
        session,
        event.chat_id,
        strict=False,
    )
    if not settings_model or not target_chat:
        return

    note_parts = [f"Reboot requested by {event.requested_by}."]
    if reason:
        note_parts.append(f"Reason: {reason}.")
    note_parts.append("Server is restarting now.")
    text = " ".join(note_parts)

    try:
        await send_message(settings_model.bot_token, target_chat, text)
    except TelegramError as exc:
        logger.warning("Failed to send reboot request notice: %s", exc)


async def request_reboot(
    session: AsyncSession,
    *,
    requested_by: str,
    chat_id: str | None = None,
    reason: str | None = None,
) -> RebootEvent:
    """Persist a reboot request, notify via Telegram if configured, and trigger the host reboot."""
    if not settings.allow_host_reboot:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Host reboot is disabled by configuration",
        )

    event = RebootEvent(requested_by=requested_by, chat_id=chat_id, note=reason)
    session.add(event)
    await session.commit()
    await session.refresh(event)

    await _notify_reboot_requested(session, event, reason)

    try:
        await _run_reboot_command()
    except Exception as exc:
        logger.error("Reboot command failed: %s", exc)
        event.back_notified_at = datetime.now(tz=timezone.utc)
        session.add(event)
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Reboot command failed: {exc}",
        ) from exc

    return event


async def notify_reboot_recovery(session: AsyncSession) -> None:
    """Send a Telegram message for any reboot events that have not been acknowledged yet."""
    result = await session.execute(
        select(RebootEvent).where(RebootEvent.back_notified_at.is_(None)).order_by(RebootEvent.created_at.asc())
    )
    settings_model, default_chat = await resolve_message_context(session, None, strict=False)

    for event in result.scalars():
        target_chat = event.chat_id or default_chat
        if not settings_model or not target_chat or not settings_model.bot_token:
            # Nothing to notify; mark as handled to avoid repeating on every startup.
            event.back_notified_at = datetime.now(tz=timezone.utc)
            session.add(event)
            await session.commit()
            continue

        text = (
            f"Server is back online after reboot requested by {event.requested_by} "
            f"at {event.created_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}."
        )
        try:
            await send_message(settings_model.bot_token, str(target_chat), text)
            event.back_notified_at = datetime.now(tz=timezone.utc)
            session.add(event)
            await session.commit()
        except TelegramError as exc:
            logger.warning("Failed to send reboot recovery notice: %s", exc)
            # Do not mark as notified; will retry on next startup.
