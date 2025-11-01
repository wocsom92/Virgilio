from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.core.security import require_admin_user
from backend.app.db.session import get_session
from backend.app.schemas.telegram import TelegramSettingsRead, TelegramSettingsUpdate, WarnThresholds
from backend.app.services.telegram_notifications import (
    resolve_message_context,
    send_stats_message,
    send_warn_message,
)
from backend.app.services.reboot_service import request_reboot
from backend.app.services.telegram_settings import get_or_create_settings
from backend.app.services.warnings import recalculate_latest_snapshot_warnings
from backend.app.services.telegram_service import TelegramError, send_message
from backend.app.models.monitors import MonitoredBackend
from backend.app.services.monitor_client import request_monitor_reboot, MonitorClientError


router = APIRouter(prefix="/telegram", tags=["telegram"])


@router.get(
    "/settings",
    response_model=TelegramSettingsRead,
    dependencies=[Depends(require_admin_user)],
)
async def read_settings(
    session: AsyncSession = Depends(get_session),
) -> TelegramSettingsRead:
    settings_model = await get_or_create_settings(session)
    return TelegramSettingsRead.model_validate(settings_model)


@router.put(
    "/settings",
    response_model=TelegramSettingsRead,
    dependencies=[Depends(require_admin_user)],
)
async def update_settings(
    payload: TelegramSettingsUpdate,
    session: AsyncSession = Depends(get_session),
) -> TelegramSettingsRead:
    settings_model = await get_or_create_settings(session)
    for key, value in payload.model_dump().items():
        setattr(settings_model, key, value)
    session.add(settings_model)
    await session.commit()
    await session.refresh(settings_model)

    response = TelegramSettingsRead.model_validate(settings_model)

    if payload.warn_thresholds is not None and settings_model.warn_thresholds:
        thresholds = WarnThresholds.model_validate(settings_model.warn_thresholds)
        await recalculate_latest_snapshot_warnings(session, thresholds)

    return response


class TelegramUpdate(BaseModel):
    message: dict | None = None
    edited_message: dict | None = None
    channel_post: dict | None = None
    edited_channel_post: dict | None = None


def _get_message_payload(update: TelegramUpdate) -> dict | None:
    return (
        update.message
        or update.edited_message
        or update.channel_post
        or update.edited_channel_post
    )


def _extract_text(message: dict | None) -> str | None:
    if not message:
        return None
    text = message.get("text")
    if isinstance(text, str):
        return text
    caption = message.get("caption")
    if isinstance(caption, str):
        return caption
    return None


def _extract_command(message: dict | None) -> str | None:
    if not message:
        return None
    text = _extract_text(message)
    if isinstance(text, str):
        parts = text.strip().split()
        if parts and parts[0].startswith("/"):
            command = parts[0].lower()
            if "@" in command:
                command = command.split("@", 1)[0]
            return command

    # Some updates include commands in captions (e.g. photo + command)
    # Fall back to entity parsing when text does not directly expose the command
    if isinstance(text, str):
        entities = message.get("entities")
        if isinstance(entities, list):
            for entity in entities:
                if (
                    isinstance(entity, dict)
                    and entity.get("type") == "bot_command"
                    and entity.get("offset") == 0
                ):
                    length = entity.get("length")
                    if isinstance(length, int) and length > 0:
                        command = text[:length].lower()
                        if "@" in command:
                            command = command.split("@", 1)[0]
                        if command.startswith("/"):
                            return command
    return None


def _extract_command_and_args(message: dict | None) -> tuple[str | None, list[str]]:
    text = _extract_text(message) or ""
    if isinstance(text, str):
        parts = text.strip().split()
        if parts:
            command = parts[0].lower()
            if "@" in command:
                command = command.split("@", 1)[0]
            args = parts[1:]
            if command.startswith("/"):
                return command, args
    return _extract_command(message), []


def _is_authorized_user(message: dict | None) -> bool:
    allowed = getattr(settings, "telegram_allowed_users", None) or []
    if not allowed:
        return True

    message = message or {}
    allowed_ids = {entry for entry in allowed if entry.isdigit()}
    allowed_usernames = {entry.lower() for entry in allowed if not entry.isdigit()}

    user = message.get("from") or {}
    user_id = user.get("id")
    username = user.get("username")

    if user_id is not None and str(user_id) in allowed_ids:
        return True
    if isinstance(username, str) and username.lower() in allowed_usernames:
        return True
    return False


def _describe_user(message: dict | None) -> str:
    user = message.get("from") if isinstance(message, dict) else None
    if not isinstance(user, dict):
        return "telegram-user"
    username = user.get("username")
    if isinstance(username, str) and username:
        return f"telegram:{username}"
    user_id = user.get("id")
    if user_id is not None:
        return f"telegram:{user_id}"
    return "telegram-user"


@router.post(
    "/send/stats",
    dependencies=[Depends(require_admin_user)],
)
async def send_stats_via_telegram(session: AsyncSession = Depends(get_session)) -> dict:
    text = await send_stats_message(session)
    return {"status": "sent", "message": text}


@router.post(
    "/send/warn",
    dependencies=[Depends(require_admin_user)],
)
async def send_warnings_via_telegram(session: AsyncSession = Depends(get_session)) -> dict:
    text = await send_warn_message(session)
    return {"status": "sent", "message": text}


@router.post("/webhook")
async def telegram_webhook(
    update: TelegramUpdate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    message = _get_message_payload(update)
    command, args = _extract_command_and_args(message)
    if not command:
        return {"ok": True}

    chat = message.get("chat") if isinstance(message, dict) else None
    chat_id = chat.get("id") if isinstance(chat, dict) else None

    if not _is_authorized_user(message):
        settings_model = await get_or_create_settings(session)
        if settings_model.bot_token and chat_id is not None:
            try:
                await send_message(
                    settings_model.bot_token,
                    str(chat_id),
                    "You are not authorized to use this bot.",
                )
            except TelegramError:
                pass
        return {"ok": False, "error": "unauthorized"}

    if command == "/stats":
        backend_id = None
        backend_name = None
        if args:
            token = args[0].strip()
            if token.lower().startswith("backend_") or token.lower().startswith("backend-"):
                token = token.split("_", 1)[1] if "_" in token else token.split("-", 1)[1]
            if token.isdigit():
                backend_id = int(token)
            else:
                backend_name = token
        try:
            await send_stats_message(
                session,
                chat_id=str(chat_id) if chat_id is not None else None,
                backend_id=backend_id,
                backend_name=backend_name,
            )
            return {"ok": True}
        except Exception as exc:
            text = "Unable to send stats."
            if isinstance(exc, TelegramError):
                text = "Telegram delivery failed."
            elif isinstance(exc, HTTPException):
                text = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            _, target_chat = await resolve_message_context(session, chat_id, strict=False)
            return {
                "method": "sendMessage",
                "chat_id": target_chat or chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }
    if command == "/warn":
        try:
            await send_warn_message(session, chat_id=str(chat_id) if chat_id is not None else None)
            return {"ok": True}
        except Exception as exc:
            text = "Unable to send warnings."
            if isinstance(exc, TelegramError):
                text = "Telegram delivery failed."
            elif isinstance(exc, HTTPException):
                text = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            _, target_chat = await resolve_message_context(session, chat_id, strict=False)
            return {
                "method": "sendMessage",
                "chat_id": target_chat or chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }
    if command in ("/reboot", "/restart"):
        # Expecting backend identifier as first argument
        if not args:
            return {
                "ok": True,
                "method": "sendMessage",
                "chat_id": chat_id,
                "text": "Usage: /reboot <backend_id|name>",
            }
        target = args[0].strip()
        backend: MonitoredBackend | None = None
        if target.isdigit():
            backend = await session.get(MonitoredBackend, int(target))
        else:
            result = await session.execute(
                select(MonitoredBackend).where(MonitoredBackend.name.ilike(target))
            )
            backend = result.scalars().first()
        if not backend:
            return {
                "ok": True,
                "method": "sendMessage",
                "chat_id": chat_id,
                "text": "Backend not found.",
            }
        try:
            await request_monitor_reboot(backend.base_url, backend.api_token)
            return {
                "ok": True,
                "method": "sendMessage",
                "chat_id": chat_id,
                "text": f"Requested reboot for {backend.name}.",
            }
        except MonitorClientError as exc:
            text = f"Failed to reach monitor: {exc}"
        except Exception as exc:  # pragma: no cover - defensive
            text = f"Failed to request reboot: {exc}"
        return {
            "ok": True,
            "method": "sendMessage",
            "chat_id": chat_id,
            "text": text,
        }

    text = "Command not recognized."
    _, target_chat = await resolve_message_context(session, chat_id, strict=True)
    return {
        "method": "sendMessage",
        "chat_id": target_chat,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
