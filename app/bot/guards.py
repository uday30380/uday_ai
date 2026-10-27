"""Authorization/rate-limit decorator, reply helper and action/confirmation registries."""
from __future__ import annotations

import logging
from functools import wraps
from typing import Awaitable, Callable

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes, ConversationHandler

from app.database.repositories import get_or_create_user
from app.database.session import session_scope
from app.utils.formatting import chunks, esc
from app.utils.security import is_authorized, is_owner

log = logging.getLogger(__name__)
Ctx = ContextTypes.DEFAULT_TYPE
ACTIONS: dict[str, Callable[..., Awaitable]] = {}
CONFIRMS: dict[str, Callable[..., Awaitable]] = {}


def action(name: str):
    def deco(fn):
        ACTIONS[name] = fn
        return fn
    return deco


def confirm_action(name: str):
    def deco(fn):
        CONFIRMS[name] = fn
        return fn
    return deco


async def reply(update: Update, text: str, markup: InlineKeyboardMarkup | None = None, *, edit: bool = True) -> None:
    """Edit the callback message in place if possible, else send new message(s). HTML parse mode."""
    q = update.callback_query
    if q is not None and q.message is not None and edit and len(text) <= 4000:
        try:
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup, disable_web_page_preview=True)
            return
        except BadRequest as exc:
            if "not modified" in str(exc).lower():
                return
            log.debug("edit failed, sending new message: %s", exc)
    msg = update.effective_message
    if msg is None:
        return
    parts = [text] if len(text) <= 4000 else chunks(text)
    for i, part in enumerate(parts):
        await msg.reply_text(part, parse_mode=ParseMode.HTML if len(parts) == 1 else None,
                             reply_markup=markup if i == len(parts) - 1 else None, disable_web_page_preview=True)


def guard(owner_only: bool = True, rate_limit: bool = True):
    """Wrap a handler: authorise, rate-limit, register the user and inject it as the 3rd argument."""
    def deco(fn):
        @wraps(fn)
        async def wrapper(update: Update, context: Ctx, *args, **kwargs):
            settings = context.bot_data["settings"]
            tg = update.effective_user
            if tg is None:
                return ConversationHandler.END
            if update.callback_query:
                try:
                    await update.callback_query.answer()
                except BadRequest:
                    pass
            if not is_authorized(settings, tg.id, tg.username):
                await reply(update, "🔒 This is a private assistant. Ask the owner to add your Telegram ID "
                                    f"(<code>{tg.id}</code>) to the allow-list.", edit=False)
                return ConversationHandler.END
            if owner_only and not is_owner(settings, tg.id, tg.username):
                await reply(update, "🔒 This feature is available to the bot owner only.", edit=False)
                return ConversationHandler.END
            if rate_limit and not context.bot_data["limiter"].allow(tg.id):
                await reply(update, "⏳ You're sending commands too fast. Please wait a minute.", edit=False)
                return ConversationHandler.END
            with session_scope() as s:
                user = get_or_create_user(s, settings, tg.id, tg.username, tg.first_name)
            return await fn(update, context, user, *args, **kwargs)
        return wrapper
    return deco


async def usage(update: Update, text: str) -> None:
    await reply(update, f"ℹ️ {text}", edit=False)


def err(exc: Exception) -> str:
    return f"❌ {esc(exc)}"
