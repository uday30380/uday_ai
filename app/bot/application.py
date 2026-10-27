"""Builds the python-telegram-bot Application and wires handlers, scheduler and shared services."""
from __future__ import annotations

import logging

from telegram import BotCommand, Update
from telegram.error import Conflict, NetworkError
from telegram.ext import Application, ApplicationBuilder, ContextTypes

from app.bot.handlers import ai, clubs, developer, freelance, pa_chat, reminders, settings as settings_h, start, tasks
from app.config import Settings
from app.database.session import session_scope
from app.scheduler.setup import setup_scheduler
from app.services.ai_service import AIService
from app.utils.formatting import esc
from app.utils.security import RateLimiter

import warnings

warnings.filterwarnings("ignore", message=".*per_message.*")  # expected: forms mix text and button steps
log = logging.getLogger(__name__)

COMMANDS = [("start", "Start"), ("menu", "Main menu"), ("help", "All commands"), ("ask", "Ask the AI"), ("task", "Add a task"),
            ("tasks", "List tasks"), ("today", "Today's tasks"), ("remind", "Set a reminder"), ("snippets", "Code snippets"),
            ("projects", "Dev projects"), ("freelance", "Freelance dashboard"), ("club", "My clubs"), ("settings", "Settings"),
            ("cancel", "Cancel current form")]


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, Conflict):
        log.error("Another instance of this bot is polling with the same token. Stop the other one.")
        return
    if isinstance(err, NetworkError):
        log.warning("Network problem talking to Telegram: %s", type(err).__name__)
        return
    log.error("Unhandled error", exc_info=err)  # stack trace goes to the (redacted) log only
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ Something went wrong. It has been logged; please try again.")
        except Exception:  # noqa: BLE001
            pass


def build_application(settings: Settings, *, with_scheduler: bool = True, form_timeout: int | None = 900, bot=None) -> Application:
    builder = ApplicationBuilder().token(settings.telegram_bot_token or "0:TEST")
    if bot is not None:
        builder = ApplicationBuilder().bot(bot)

    async def post_init(app: Application) -> None:
        try:
            await app.bot.set_my_commands([BotCommand(c, d) for c, d in COMMANDS])
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not set command menu: %s", type(exc).__name__)
        if with_scheduler:
            sched = setup_scheduler(session_scope, app.bot, settings.enable_daily_briefing)
            sched.start()
            app.bot_data["scheduler"] = sched
            log.info("Scheduler started; pending reminders will be delivered when due.")

    async def post_shutdown(app: Application) -> None:
        sched = app.bot_data.get("scheduler")
        if sched:
            sched.shutdown(wait=False)

    app = builder.post_init(post_init).post_shutdown(post_shutdown).build()
    app.bot_data.update(settings=settings, ai=AIService(settings), limiter=RateLimiter(settings.rate_limit_per_minute))
    # Order matters: conversation handlers first so they win over generic callback routers.
    for module in (ai, tasks, reminders, developer, freelance, clubs, settings_h):
        app.add_handlers(module.get_handlers(form_timeout))
    app.add_handlers(start.get_handlers())
    app.add_handlers(pa_chat.get_handlers())
    app.add_error_handler(error_handler)
    return app
