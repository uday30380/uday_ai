"""/settings: timezone, daily briefing, AI toggle and default currency."""
from __future__ import annotations

from telegram import Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from app.bot import keyboards
from app.bot.forms import Field, Form, build_form_handler
from app.bot.guards import action, guard, reply
from app.database.models import User
from app.database.session import session_scope
from app.utils import validation as v
from app.utils.formatting import esc

Ctx = ContextTypes.DEFAULT_TYPE


def _panel(user: User) -> tuple[str, object]:
    text = ("⚙️ <b>Settings</b>\n"
            f"• Timezone: <code>{esc(user.timezone)}</code>\n"
            f"• Morning briefing: {'ON' if user.briefing_enabled else 'OFF'} at {esc(user.briefing_time)}\n"
            f"• AI assistant: {'ON' if user.ai_enabled else 'OFF'}\n"
            f"• Default currency: {esc(user.default_currency)}")
    rows = [[("🌍 Timezone", "f:set_tz"), ("🕖 Briefing time", "f:set_brief")],
            [(f"☀️ Briefing: {'turn off' if user.briefing_enabled else 'turn on'}", "s:briefing"),
             (f"🤖 AI: {'turn off' if user.ai_enabled else 'turn on'}", "s:ai")],
            [("💱 Currency", "f:set_cur")]]
    return text, keyboards.kb(rows, back=True)


@action("settings")
@guard(owner_only=False)
async def settings_cmd(update: Update, context: Ctx, user: User, page: int = 0) -> None:
    text, markup = _panel(user)
    await reply(update, text, markup)


@guard(owner_only=False)
async def toggle(update: Update, context: Ctx, user: User) -> None:
    which = update.callback_query.data.split(":")[1]
    with session_scope() as s:
        u = s.get(User, user.id)
        if which == "briefing":
            u.briefing_enabled = not u.briefing_enabled
        elif which == "ai":
            u.ai_enabled = not u.ai_enabled
        s.flush(); s.refresh(u)
        text, markup = _panel(u)
    note = ("\n\n<i>Briefings also need ENABLE_DAILY_BRIEFING=true in .env.</i>" if which == "briefing" else "")
    await reply(update, text + note, markup)


async def _simple_finish(update, context, user, data, attr: str, label: str) -> str:
    with session_scope() as s:
        setattr(s.get(User, user.id), attr, data["value"])
    return f"✅ {label} updated to <b>{esc(data['value'])}</b>."


def _form(name, cmd, title, prompt, parse, attr, label) -> Form:
    async def fin(update, context, user, data):
        return await _simple_finish(update, context, user, data, attr, label)
    return Form(name=name, commands=[cmd], title=title, fields=[Field("value", prompt, parse)], finish=fin, owner_only=False)


FORMS = [
    _form("set_tz", "timezone", "Timezone", "Send an IANA timezone, e.g. <code>Asia/Kolkata</code>.", lambda t, d, u, s: v.timezone_name(t), "timezone", "Timezone"),
    _form("set_brief", "briefing_time", "Briefing time", "Send the time as 24-hour HH:MM, e.g. <code>07:00</code>.", lambda t, d, u, s: v.hhmm(t), "briefing_time", "Briefing time"),
    _form("set_cur", "currency", "Currency", "Send a 3-letter currency code, e.g. <code>INR</code>.", lambda t, d, u, s: v.currency(t), "default_currency", "Default currency"),
]


def get_handlers(timeout: int | None = 900) -> list:
    return [CommandHandler("settings", settings_cmd), CallbackQueryHandler(toggle, pattern=r"^s:(briefing|ai)$"),
            *[build_form_handler(f, timeout=timeout) for f in FORMS]]
