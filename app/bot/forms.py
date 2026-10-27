"""Tiny declarative engine for guided multi-step forms built on ConversationHandler."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from telegram import Update
from telegram.ext import (CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters)

from app.bot import keyboards
from app.bot.guards import guard, reply
from app.services.club_service import PermissionDenied
from app.utils.formatting import esc

log = logging.getLogger(__name__)
Ctx = ContextTypes.DEFAULT_TYPE
CONFIRM = 99
SKIP = object()


@dataclass
class Field:
    key: str
    prompt: str
    parse: Callable[[str, dict, Any, Any], Any]    # (text, data, user, settings) -> value; raise ValueError
    optional: bool = False
    default: Any = None


@dataclass
class Form:
    name: str
    commands: list[str]
    title: str
    fields: list[Field]
    finish: Callable[[Update, Ctx, Any, dict], Awaitable[str]]   # returns HTML to show
    summary: Callable[[dict, Any, Any], str] | None = None       # present → ask for confirmation first
    owner_only: bool = True
    from_reply: bool = False                                     # prefill first field from a replied-to message
    pre: Callable[[Update, Ctx, Any], Awaitable[bool]] | None = None  # return True to short-circuit (already handled)
    extra: dict = field(default_factory=dict)


def _state(context: Ctx) -> dict:
    return context.user_data.setdefault("form", {})


async def _ask(update: Update, context: Ctx, form: Form) -> int:
    st = _state(context)
    i = st["i"]
    f = form.fields[i]
    text = f"<b>{esc(form.title)}</b> — step {i + 1}/{len(form.fields)}\n{f.prompt}"
    if f.optional:
        text += "\n<i>(optional — tap Skip)</i>"
    await reply(update, text, keyboards.cancel_row(f.optional), edit=False)
    return i


async def _advance(update: Update, context: Ctx, user, form: Form) -> int:
    st = _state(context)
    st["i"] += 1
    if st["i"] < len(form.fields):
        return await _ask(update, context, form)
    if form.summary:
        data = {k: v for k, v in st["data"].items()}
        await reply(update, f"<b>Please confirm</b>\n{form.summary(data, user, context.bot_data['settings'])}",
                    keyboards.confirm_form(), edit=False)
        return CONFIRM
    return await _finish(update, context, user, form)


async def _finish(update: Update, context: Ctx, user, form: Form) -> int:
    data = dict(_state(context).get("data", {}))
    context.user_data.pop("form", None)
    try:
        text = await form.finish(update, context, user, data)
    except (ValueError, LookupError, PermissionDenied) as exc:
        text = f"❌ {esc(exc)}"
    await reply(update, text, keyboards.kb([[("🏠 Menu", "m:main")]]), edit=False)
    return ConversationHandler.END


def build_form_handler(form: Form, *, timeout: int | None = 900) -> ConversationHandler:
    async def start(update: Update, context: Ctx, user) -> int:
        if form.pre is not None and await form.pre(update, context, user):
            return ConversationHandler.END
        context.user_data["form"] = {"i": 0, "data": {}}
        first = form.fields[0]
        text = " ".join(context.args) if getattr(context, "args", None) else ""
        if not text and form.from_reply and update.effective_message and update.effective_message.reply_to_message:
            text = update.effective_message.reply_to_message.text or ""
        if text:
            try:
                _state(context)["data"][first.key] = first.parse(text, {}, user, context.bot_data["settings"])
                return await _advance(update, context, user, form)
            except ValueError as exc:
                await reply(update, f"❌ {esc(exc)}", edit=False)
        return await _ask(update, context, form)

    async def on_text(update: Update, context: Ctx, user) -> int:
        st = _state(context)
        if "i" not in st:
            return ConversationHandler.END
        f = form.fields[st["i"]]
        raw = (update.effective_message.text or "").strip()
        if f.optional and raw.lower() in {"skip", "-"}:
            st["data"][f.key] = f.default
            return await _advance(update, context, user, form)
        try:
            st["data"][f.key] = f.parse(raw, st["data"], user, context.bot_data["settings"])
        except ValueError as exc:
            await reply(update, f"❌ {esc(exc)}\nTry again, or /cancel.", edit=False)
            return st["i"]
        except (LookupError, PermissionDenied) as exc:
            await reply(update, f"❌ {esc(exc)}\nTry again, or /cancel.", edit=False)
            return st["i"]
        return await _advance(update, context, user, form)

    async def on_skip(update: Update, context: Ctx, user) -> int:
        st = _state(context)
        if "i" not in st:
            return ConversationHandler.END
        f = form.fields[st["i"]]
        st["data"][f.key] = f.default
        return await _advance(update, context, user, form)

    async def on_confirm(update: Update, context: Ctx, user) -> int:
        if update.callback_query.data == "form:yes":
            return await _finish(update, context, user, form)
        context.user_data.pop("form", None)
        await reply(update, "✖️ Cancelled. Nothing was changed.", keyboards.kb([[("🏠 Menu", "m:main")]]))
        return ConversationHandler.END

    async def cancel(update: Update, context: Ctx, user) -> int:
        context.user_data.pop("form", None)
        await reply(update, "✖️ Cancelled. Nothing was changed.", keyboards.kb([[("🏠 Menu", "m:main")]]), edit=False)
        return ConversationHandler.END

    async def other_command(update: Update, context: Ctx, user) -> int:
        context.user_data.pop("form", None)
        await reply(update, "✖️ Form cancelled. Please send your command again.", edit=False)
        return ConversationHandler.END

    g = lambda fn: guard(owner_only=form.owner_only)(fn)  # noqa: E731
    # Answers inside an already-started form are not rate-limited (entering a form is).
    gs = lambda fn: guard(owner_only=form.owner_only, rate_limit=False)(fn)  # noqa: E731
    per_state = [MessageHandler(filters.TEXT & ~filters.COMMAND, gs(on_text)),
                 CallbackQueryHandler(gs(on_skip), pattern=r"^form:skip$")]
    states: dict[int, list] = {i: list(per_state) for i in range(len(form.fields))}
    states[CONFIRM] = [CallbackQueryHandler(gs(on_confirm), pattern=r"^form:(yes|no)$")]
    return ConversationHandler(
        name=form.name,
        entry_points=([CommandHandler(form.commands, g(start))] if form.commands else [])
        + [CallbackQueryHandler(g(start), pattern=rf"^f:{form.name}$")],
        states=states,
        fallbacks=[CommandHandler("cancel", gs(cancel)), CallbackQueryHandler(gs(cancel), pattern=r"^form:cancel$"),
                   MessageHandler(filters.COMMAND, g(other_command))],
        conversation_timeout=timeout, allow_reentry=True,
    )
