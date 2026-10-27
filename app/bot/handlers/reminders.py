"""Reminders: create (guided), list, cancel and reschedule."""
from __future__ import annotations

from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from app.bot import keyboards
from app.bot.forms import Field, Form, build_form_handler
from app.bot.guards import action, guard, reply, usage
from app.database.session import session_scope
from app.services import reminder_service
from app.utils import validation as v
from app.utils.dates import fmt_dt, parse_when
from app.utils.formatting import esc

Ctx = ContextTypes.DEFAULT_TYPE
HOME = keyboards.kb([[("🏠 Menu", "m:main")]])


def _when(text, data, user, settings):
    res = parse_when(text, ZoneInfo(user.timezone), require_time=True)
    if res.dt is None:
        raise ValueError(res.clarify or res.error)
    return res.dt


async def _finish(update, context, user, data):
    with session_scope() as s:
        r = reminder_service.create_reminder(s, user.id, data["text"], data["when"])
        return f"⏰ Reminder <b>#{r.id}</b> set for {fmt_dt(r.remind_at, ZoneInfo(user.timezone))}:\n{esc(r.message)}"


REMIND_FORM = Form(
    name="remind", commands=["remind"], title="New reminder", finish=_finish,
    summary=lambda d, u, s: f"{esc(d['text'])}\n🕒 {fmt_dt(d['when'], ZoneInfo(u.timezone))}",
    fields=[Field("text", "What should I remind you about?", lambda t, d, u, s: v.clean(t, max_len=500, field="Reminder")),
            Field("when", "When? e.g. <i>tomorrow 5pm</i>, <i>in 2 hours</i>, <i>30 oct 18:00</i>", _when)])


@action("reminders")
@guard()
async def reminders_cmd(update: Update, context: Ctx, user, page: int = 0) -> None:
    tz = ZoneInfo(user.timezone)
    with session_scope() as s:
        items = reminder_service.list_reminders(s, user.id)
    if not items:
        return await reply(update, "🔕 No pending reminders. Create one with /remind", HOME)
    lines = [f"<b>#{r.id}</b> {fmt_dt(r.remind_at, tz)} — {esc(r.message)}" + (f"  ⚠️ retrying ({esc(r.last_error)})" if r.last_error else "")
             for r in items[:20]]
    await reply(update, "🔔 <b>Pending reminders</b>\n\n" + "\n".join(lines) +
                "\n\n/cancel_reminder &lt;id&gt; · /reschedule &lt;id&gt; &lt;when&gt;", HOME)


@guard()
async def cancel_reminder(update: Update, context: Ctx, user) -> None:
    try:
        rid = v.positive_int(context.args[0] if context.args else "", "Reminder ID")
        with session_scope() as s:
            reminder_service.cancel_reminder(s, user.id, rid)
        await reply(update, f"🔕 Reminder #{rid} cancelled.", edit=False)
    except ValueError as exc:
        await reply(update, f"❌ {esc(exc)}\nUsage: /cancel_reminder &lt;id&gt;", edit=False)
    except LookupError as exc:
        await reply(update, f"❌ {esc(exc)}", edit=False)


@guard()
async def reschedule(update: Update, context: Ctx, user) -> None:
    args = context.args or []
    if len(args) < 2:
        return await usage(update, "Usage: /reschedule &lt;id&gt; &lt;when&gt;, e.g. /reschedule 4 tomorrow 9am")
    try:
        rid = v.positive_int(args[0], "Reminder ID")
        res = parse_when(" ".join(args[1:]), ZoneInfo(user.timezone))
        if res.dt is None:
            raise ValueError(res.clarify or res.error)
        with session_scope() as s:
            reminder_service.reschedule_reminder(s, user.id, rid, res.dt)
        await reply(update, f"🔁 Reminder #{rid} moved to {fmt_dt(res.dt, ZoneInfo(user.timezone))}.", edit=False)
    except (ValueError, LookupError) as exc:
        await reply(update, f"❌ {esc(exc)}", edit=False)


def get_handlers(timeout: int | None = 900) -> list:
    return [build_form_handler(REMIND_FORM, timeout=timeout), CommandHandler("reminders", reminders_cmd),
            CommandHandler("cancel_reminder", cancel_reminder), CommandHandler("reschedule", reschedule)]
