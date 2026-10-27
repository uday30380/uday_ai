"""Tasks: create, list, complete, edit, delete and the today/upcoming/daily-plan views."""
from __future__ import annotations

import re

from telegram import Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from app.bot import keyboards
from app.bot.forms import Field, Form, build_form_handler
from app.bot.guards import action, confirm_action, guard, reply, usage
from app.database.session import session_scope
from app.scheduler.setup import format_plan
from app.services import task_service
from app.utils import validation as v
from app.utils.dates import fmt_dt, parse_when
from app.utils.formatting import PRIORITY_ICON, esc
from app.utils.security import audit

Ctx = ContextTypes.DEFAULT_TYPE
PER = 8
HOME = keyboards.kb([[("🏠 Menu", "m:main")]])


def _line(t, tz) -> str:
    due = f" — 📆 {fmt_dt(t.due_at, tz)}" if t.due_at else ""
    return f"{PRIORITY_ICON.get(t.priority, '')} <b>#{t.id}</b> {esc(t.title)}{due}"


def _due(text, data, user, settings):
    from zoneinfo import ZoneInfo
    res = parse_when(text, ZoneInfo(user.timezone), require_time=False)
    if res.dt is None:
        raise ValueError(res.clarify or res.error)
    return res.dt


async def _task_finish(update, context, user, data):
    with session_scope() as s:
        t = task_service.create_task(s, user.id, data["title"], description=data.get("description") or "",
                                     priority=data.get("priority") or "medium", due_at=data.get("due"))
        from zoneinfo import ZoneInfo
        due = f"\n📆 Due {fmt_dt(t.due_at, ZoneInfo(user.timezone))} (I'll remind you)" if t.due_at else ""
        return f"✅ Task <b>#{t.id}</b> created: {esc(t.title)}{due}"


TASK_FORM = Form(
    name="task", commands=["task"], title="New task", finish=_task_finish,
    fields=[Field("title", "What's the task?", lambda t, d, u, s: v.clean(t, max_len=200, field="Title")),
            Field("description", "Add details?", lambda t, d, u, s: v.clean(t, max_len=2000), optional=True, default=""),
            Field("priority", "Priority: <b>low</b>, <b>medium</b> or <b>high</b>?", lambda t, d, u, s: v.priority(t), optional=True, default="medium"),
            Field("due", "When is it due? e.g. <i>tomorrow 5pm</i>, <i>30 oct</i>", _due, optional=True)])


@action("tasks")
@guard()
async def tasks_cmd(update: Update, context: Ctx, user, page: int = 0) -> None:
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(user.timezone)
    with session_scope() as s:
        items = task_service.list_tasks(s, user.id)
    if not items:
        await reply(update, "🎉 No pending tasks. Add one with /task", keyboards.kb([[("➕ New task", "f:task")], [("🏠 Menu", "m:main")]]))
        return
    chunk = items[page * PER:(page + 1) * PER]
    text = f"📋 <b>Pending tasks</b> ({len(items)})\n\n" + "\n".join(_line(t, tz) for t in chunk)
    done_btns = [(f"✅ {t.id}", f"t:done:{t.id}") for t in chunk]
    extra = [done_btns[i:i + 4] for i in range(0, len(done_btns), 4)]
    await reply(update, text + "\n\nTap ✅ to complete.", keyboards.pager("tasks", page, len(items), PER, extra))


async def _complete(update: Update, user, task_id: int) -> None:
    try:
        with session_scope() as s:
            t = task_service.complete_task(s, user.id, task_id)
            title = t.title
        await reply(update, f"✅ Done: <b>#{task_id}</b> {esc(title)}", HOME, edit=not update.callback_query is None)
    except LookupError as exc:
        await reply(update, f"❌ {esc(exc)}", edit=False)


@guard()
async def done_cmd(update: Update, context: Ctx, user) -> None:
    try:
        tid = v.positive_int(context.args[0] if context.args else "", "Task ID")
    except ValueError:
        return await usage(update, "Usage: /done &lt;task_id&gt;")
    await _complete(update, user, tid)


@guard()
async def done_button(update: Update, context: Ctx, user) -> None:
    await _complete(update, user, int(update.callback_query.data.split(":")[2]))


@guard()
async def delete_cmd(update: Update, context: Ctx, user) -> None:
    try:
        tid = v.positive_int(context.args[0] if context.args else "", "Task ID")
    except ValueError:
        return await usage(update, "Usage: /delete_task &lt;task_id&gt;")
    with session_scope() as s:
        t = task_service.get_task(s, user.id, tid)
    if t is None:
        return await reply(update, f"❌ Task #{tid} not found.", edit=False)
    await reply(update, f"🗑 Delete task <b>#{t.id}</b> “{esc(t.title)}”? This also removes its reminders.", keyboards.yes_no("deltask", tid), edit=False)


@confirm_action("deltask")
@guard()
async def deltask_confirmed(update: Update, context: Ctx, user, item_id: int) -> None:
    try:
        with session_scope() as s:
            task_service.delete_task(s, user.id, item_id)
        await reply(update, f"🗑 Task #{item_id} deleted.", HOME)
    except LookupError as exc:
        await reply(update, f"❌ {esc(exc)}", HOME)


@guard()
async def edit_cmd(update: Update, context: Ctx, user) -> None:
    from zoneinfo import ZoneInfo
    args = context.args or []
    if len(args) < 3:
        return await usage(update, "Usage: /edit_task &lt;id&gt; &lt;title|description|priority|due&gt; &lt;new value&gt;\n"
                                   "Example: /edit_task 3 due tomorrow 6pm")
    try:
        with session_scope() as s:
            t = task_service.edit_task(s, user.id, v.positive_int(args[0], "Task ID"), args[1], " ".join(args[2:]), ZoneInfo(user.timezone))
            line = _line(t, ZoneInfo(user.timezone))
        await reply(update, f"✏️ Updated:\n{line}", edit=False)
    except (ValueError, LookupError) as exc:
        await reply(update, f"❌ {esc(exc)}", edit=False)


async def _view(update, user, which: str) -> None:
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(user.timezone)
    with session_scope() as s:
        if which == "today":
            due, overdue = task_service.today_tasks(s, user.id, tz)
            body = ""
            if overdue:
                body += "⚠️ <b>Overdue</b>\n" + "\n".join(_line(t, tz) for t in overdue) + "\n\n"
            body += ("📅 <b>Today</b>\n" + "\n".join(_line(t, tz) for t in due)) if due else "📅 Nothing else due today."
            title = "📅 <b>Today</b>\n\n"
        elif which == "upcoming":
            items = task_service.upcoming_tasks(s, user.id, tz)
            title, body = "🔜 <b>Next 7 days</b>\n\n", "\n".join(_line(t, tz) for t in items) or "Nothing scheduled."
        else:
            return await reply(update, format_plan(task_service.daily_plan(s, user.id, tz), tz, "🗓 Your daily plan"), HOME)
    await reply(update, title + body, HOME)


@action("today")
@guard()
async def today(update: Update, context: Ctx, user, page: int = 0) -> None:
    await _view(update, user, "today")


@action("upcoming")
@guard()
async def upcoming(update: Update, context: Ctx, user, page: int = 0) -> None:
    await _view(update, user, "upcoming")


@action("dailyplan")
@guard()
async def dailyplan(update: Update, context: Ctx, user, page: int = 0) -> None:
    await _view(update, user, "plan")


def get_handlers(timeout: int | None = 900) -> list:
    return [build_form_handler(TASK_FORM, timeout=timeout), CommandHandler("tasks", tasks_cmd), CommandHandler("done", done_cmd),
            CommandHandler("delete_task", delete_cmd), CommandHandler("edit_task", edit_cmd), CommandHandler("today", today),
            CommandHandler("upcoming", upcoming), CommandHandler("dailyplan", dailyplan),
            CallbackQueryHandler(done_button, pattern=r"^t:done:\d+$")]
