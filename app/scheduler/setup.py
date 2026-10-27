"""APScheduler wiring: reminder dispatcher (every 30 s) and optional daily briefing."""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from telegram import Bot
from telegram.error import Forbidden

from app.database.models import User
from app.services import reminder_service, task_service
from app.utils.dates import from_utc, utcnow
from app.utils.formatting import esc

log = logging.getLogger(__name__)


def build_sender(bot: Bot):
    async def send(telegram_id: int, text: str) -> None:
        try:
            await bot.send_message(telegram_id, text)  # plain text: reminder content is user-supplied
        except Forbidden as exc:
            raise reminder_service.PermanentDeliveryError("User blocked the bot or never started it") from exc
    return send


async def briefing_tick(session_scope, bot: Bot) -> int:
    """Send the morning summary to users whose local time matches and who have not got one today."""
    from zoneinfo import ZoneInfo
    sent = 0
    with session_scope() as s:
        users = list(s.scalars(select(User).where(User.briefing_enabled.is_(True))))
    for u in users:
        tz = ZoneInfo(u.timezone)
        local = from_utc(utcnow(), tz)
        today = local.strftime("%Y-%m-%d")
        if local.strftime("%H:%M") < u.briefing_time or u.last_briefing_on == today:
            continue
        with session_scope() as s:
            plan = task_service.daily_plan(s, u.id, tz)
            text = format_plan(plan, tz, "☀️ Good morning! Your plan for today")
        try:
            await bot.send_message(u.telegram_id, text, parse_mode="HTML")
        except Exception as exc:  # noqa: BLE001
            log.warning("Briefing failed for user %s: %s", u.id, type(exc).__name__)
            continue
        with session_scope() as s:
            s.get(User, u.id).last_briefing_on = today
        sent += 1
    return sent


def format_plan(plan: dict, tz, title: str) -> str:
    from app.utils.dates import fmt_dt
    from app.utils.formatting import PRIORITY_ICON

    def line(t):
        return f"{PRIORITY_ICON.get(t.priority, '')} #{t.id} {esc(t.title)}" + (f" — {fmt_dt(t.due_at, tz)}" if t.due_at else "")
    parts = [f"<b>{esc(title)}</b>"]
    for key, label in (("overdue", "⚠️ Overdue"), ("today", "📅 Today"), ("unscheduled", "📝 No date"), ("upcoming", "🔜 Next 3 days")):
        if plan.get(key):
            parts.append(f"\n<b>{label}</b>\n" + "\n".join(line(t) for t in plan[key]))
    if len(parts) == 1:
        parts.append("\nNothing pending. Enjoy your day! 🎉")
    return "\n".join(parts)


def setup_scheduler(session_scope, bot: Bot, enable_briefing: bool) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    sender = build_sender(bot)

    async def dispatch():
        try:
            await reminder_service.dispatch_due(session_scope, sender)
        except Exception:  # noqa: BLE001
            log.exception("Reminder dispatch crashed (will retry next tick)")

    scheduler.add_job(dispatch, "interval", seconds=30, id="reminder-dispatch", max_instances=1, coalesce=True,
                      replace_existing=True, next_run_time=utcnow_aware())
    if enable_briefing:
        async def brief():
            try:
                await briefing_tick(session_scope, bot)
            except Exception:  # noqa: BLE001
                log.exception("Briefing tick failed")
        scheduler.add_job(brief, "interval", seconds=60, id="daily-briefing", max_instances=1, coalesce=True, replace_existing=True)
    return scheduler


def utcnow_aware():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)
