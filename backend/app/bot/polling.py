from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import HTTPException
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.app.core.config import settings
from backend.app.db.session import async_session_factory
from backend.app.models.monitors import MonitoredBackend
from backend.app.services.monitor_client import MonitorClientError, request_monitor_reboot
from backend.app.services.reboot_service import request_reboot
from backend.app.services.telegram_service import TelegramError, send_message
from backend.app.services.telegram_notifications import send_stats_message, send_warn_message
from backend.app.bot.boot_tracker import BootTracker

logger = logging.getLogger(__name__)

REBOOT_STATE_FILE = Path(os.getenv("SERVER_MONITOR_REBOOT_STATE_FILE", "/app/data/reboot_state.json"))
boot_tracker = BootTracker(REBOOT_STATE_FILE)


def _allowed_user_ids() -> set[str]:
    return {entry for entry in (settings.telegram_allowed_users or []) if entry.isdigit()}


def _allowed_usernames() -> set[str]:
    return {entry.lower() for entry in (settings.telegram_allowed_users or []) if not entry.isdigit()}


def _is_authorized(update: Update) -> bool:
    allowed_ids = _allowed_user_ids()
    allowed_usernames = _allowed_usernames()
    if not allowed_ids and not allowed_usernames:
        return True

    user = update.effective_user
    if not user:
        return False

    if allowed_ids and user.id is not None and str(user.id) in allowed_ids:
        return True
    if allowed_usernames and user.username and user.username.lower() in allowed_usernames:
        return True
    return False


def with_session(
    func: Callable[[Update, ContextTypes.DEFAULT_TYPE, str, AsyncSession], Awaitable[None]],
) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]:
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_authorized(update):
            if update.effective_message:
                await update.effective_message.reply_text("You are not authorized to use this bot.")
            return

        chat = update.effective_chat
        if chat is None:
            return
        chat_id = str(chat.id)

        async with async_session_factory() as session:
            await func(update, context, chat_id, session)

    return wrapper


async def handle_stats(_: Update, __: ContextTypes.DEFAULT_TYPE, chat_id: str, session) -> None:
    await send_stats_message(session, chat_id=chat_id)


async def handle_warn(_: Update, __: ContextTypes.DEFAULT_TYPE, chat_id: str, session) -> None:
    await send_warn_message(session, chat_id=chat_id)


def _describe_user(update: Update) -> str:
    user = update.effective_user
    if user and user.username:
        return f"telegram:{user.username}"
    if user and user.id is not None:
        return f"telegram:{user.id}"
    return "telegram-user"


async def _find_backend(session: AsyncSession, token: str) -> MonitoredBackend | None:
    if token.isdigit():
        return await session.get(MonitoredBackend, int(token))
    result = await session.execute(
        select(MonitoredBackend).where(MonitoredBackend.name.ilike(token))
    )
    return result.scalars().first()


def _extract_args(update: Update) -> list[str]:
    message = update.effective_message
    text = None
    if message:
        if message.text:
            text = message.text
        elif message.caption:
            text = message.caption
    if not text:
        return []
    parts = text.strip().split()
    if len(parts) <= 1:
        return []
    return parts[1:]


async def handle_reboot_backend(update: Update, _: ContextTypes.DEFAULT_TYPE, chat_id: str, session) -> None:
    args = _extract_args(update)
    message = update.effective_message
    if not args:
        if message:
            await message.reply_text("Usage: /reboot <backend_id|name>")
        return

    target = args[0]
    backend = await _find_backend(session, target)
    if not backend:
        if message:
            await message.reply_text("Backend not found.")
        return

    if message:
        await message.reply_text(f"Requesting reboot for {backend.name}…")

    try:
        await request_monitor_reboot(backend.base_url, backend.api_token)
        if message:
            await message.reply_text(f"Requested reboot for {backend.name}.")
    except MonitorClientError as exc:
        if message:
            await message.reply_text(f"Monitor request failed: {exc}")
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Unexpected reboot request error")
        if message:
            await message.reply_text(f"Unable to request reboot: {exc}")


async def handle_host_reboot(update: Update, _: ContextTypes.DEFAULT_TYPE, chat_id: str, session) -> None:
    message = update.effective_message
    if not settings.allow_host_reboot:
        if message:
            await message.reply_text("Reboot disabled in configuration.")
        return
    if message:
        await message.reply_text("Requesting reboot…")
    try:
        await request_reboot(session, requested_by=_describe_user(update), chat_id=chat_id, reason="Telegram bot")
        if message:
            await message.reply_text("Reboot requested. You will be notified when back online.")
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        if message:
            await message.reply_text(detail)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Unexpected reboot request error")
        if message:
            await message.reply_text(f"Unable to request reboot: {exc}")


async def handle_start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    if update.effective_message:
        keyboard = ReplyKeyboardMarkup([["/stats", "/warn", "/reboot", "/hostreboot"]], resize_keyboard=True)
        await update.effective_message.reply_text(
            "Server Monitor bot ready. Use the buttons or type /stats /warn /reboot <backend> /hostreboot",
            reply_markup=keyboard,
        )


async def notify_on_reboot(_: Application) -> None:
    if not settings.telegram_bot_token or not settings.telegram_default_chat_id:
        return
    try:
        if not boot_tracker.should_notify_reboot():
            return
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to check reboot state: %s", exc)
        return

    try:
        await send_message(
            settings.telegram_bot_token,
            str(settings.telegram_default_chat_id),
            "Server rebooted and bot is back online.",
        )
    except TelegramError as exc:
        logger.warning("Failed to send reboot notice: %s", exc)


def build_application() -> Application:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

    application = ApplicationBuilder().token(settings.telegram_bot_token).build()
    application.add_handler(CommandHandler("start", handle_start))
    application.add_handler(CommandHandler("stats", with_session(handle_stats)))
    application.add_handler(CommandHandler("warn", with_session(handle_warn)))
    application.add_handler(CommandHandler(["reboot", "restart"], with_session(handle_reboot_backend)))
    application.add_handler(CommandHandler("hostreboot", with_session(handle_host_reboot)))
    application.post_init = notify_on_reboot
    return application


def main() -> None:
    app = build_application()
    app.run_polling()


if __name__ == "__main__":
    main()
