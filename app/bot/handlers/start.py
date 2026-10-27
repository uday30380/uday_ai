"""Core commands, main menu, and the generic callback routers (menus, actions, confirmations)."""
from __future__ import annotations

import re
import time

from sqlalchemy import func, select
from telegram import Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from app.bot import keyboards
from app.bot.guards import ACTIONS, CONFIRMS, action, guard, reply
from app.database.models import Client, DevProject, Snippet, Task
from app.database.session import session_scope
from app.utils.formatting import esc
from app.utils.security import is_authorized, is_owner

Ctx = ContextTypes.DEFAULT_TYPE

HELP = """<b>Uday AI — commands</b>

<b>General</b>: /start /menu /help /about /id /ping /profile /settings /privacy /cancel
<b>AI</b>: /ask /summarize /rewrite /explain /clear
<b>Tasks</b>: /task /tasks /done &lt;id&gt; /delete_task &lt;id&gt; /edit_task &lt;id&gt; &lt;field&gt; &lt;value&gt; /today /upcoming /dailyplan
<b>Reminders</b>: /remind /reminders /cancel_reminder &lt;id&gt; /reschedule &lt;id&gt; &lt;when&gt;
<b>Developer</b>: /snippet &lt;name&gt; /snippets /project &lt;name&gt; /projects /project_status &lt;name&gt; &lt;status&gt; /logwork /devhelp
<b>Freelance</b>: /client /clients /newproject /freelance /payment /reverse_payment &lt;id&gt; /followup /invoice
<b>Clubs</b>: /club /event /events /member /members /clubtask /clubtasks /clubdone &lt;id&gt; /announce /clubexport

Tip: most commands accept the first answer inline, e.g. <code>/task Buy milk</code>. Dates understand
things like <i>tomorrow 5pm</i>, <i>in 2 hours</i>, <i>30 oct 18:00</i>. Use /cancel at any time."""

PRIVACY = """<b>Privacy</b>
• Your data lives in a local SQLite file on the machine running this bot — nothing is sent to analytics or ad services.
• Telegram itself can see messages sent through the Bot API (bots are not end-to-end encrypted).
• AI prompts go to your local Ollama model only. If you enabled an optional hosted provider, prompts go to that provider.
• The bot never runs code or shell commands you send.
• Club contact details are visible only to club owners/admins. Use /clear to wipe AI conversation memory."""


@guard(owner_only=False)
async def start(update: Update, context: Ctx, user) -> None:
    name = esc(user.first_name or "Uday")
    await reply(update, f"👋 Hi {name}! I am your <b>24/7 Executive Personal Assistant (PA)</b>.\n\n"
                        "I am here to manage your daily schedule, reminders, tasks, software projects, clients and notes.\n"
                        "You can chat with me naturally (e.g. <i>'Add task: ...'</i>, <i>'Remind me at 4pm to ...'</i>) or tap a button below.", keyboards.main_menu(), edit=False)


@guard(owner_only=False)
async def help_cmd(update: Update, context: Ctx, user) -> None:
    await reply(update, HELP, keyboards.kb([[("🏠 Menu", "m:main")]]), edit=False)


@guard(owner_only=False)
async def menu(update: Update, context: Ctx, user) -> None:
    await reply(update, "📍 <b>Main menu</b>", keyboards.main_menu())


@guard(owner_only=False)
async def about(update: Update, context: Ctx, user) -> None:
    s = context.bot_data["settings"]
    ai = await context.bot_data["ai"].status()
    await reply(update, f"<b>Uday AI</b> v1.0 — free, private, owner-operated.\nAI: <b>{esc(ai)}</b> "
                        f"(provider: {esc(s.ai_provider)})\nTimezone: {esc(s.app_timezone)}", edit=False)


async def id_cmd(update: Update, context: Ctx) -> None:  # intentionally unguarded: lets you discover your ID
    u = update.effective_user
    await update.effective_message.reply_text(f"Your Telegram user ID: {u.id}\nChat ID: {update.effective_chat.id}")


async def ping(update: Update, context: Ctx) -> None:
    t0 = time.perf_counter()
    msg = await update.effective_message.reply_text("🏓 Pong!")
    ms = int((time.perf_counter() - t0) * 1000)
    await msg.edit_text(f"🏓 Pong! round-trip ≈ {ms} ms")


@action("profile")
@guard(owner_only=False)
async def profile(update: Update, context: Ctx, user, page: int = 0) -> None:
    s = context.bot_data["settings"]
    with session_scope() as db:
        counts = {
            "open tasks": db.scalar(select(func.count()).select_from(Task).where(Task.user_id == user.id, Task.status == "pending")),
            "snippets": db.scalar(select(func.count()).select_from(Snippet).where(Snippet.user_id == user.id)),
            "dev projects": db.scalar(select(func.count()).select_from(DevProject).where(DevProject.user_id == user.id)),
            "clients": db.scalar(select(func.count()).select_from(Client).where(Client.user_id == user.id)),
        }
    role = "owner" if is_owner(s, user.telegram_id, user.username) else "allowed user"
    lines = "\n".join(f"• {k}: {v}" for k, v in counts.items())
    await reply(update, f"👤 <b>Profile</b>\nName: {esc(user.first_name)}\nUsername: @{esc(user.username or '—')}\n"
                        f"Telegram ID: <code>{user.telegram_id}</code>\nRole: {role}\nTimezone: {esc(user.timezone)}\n\n{lines}",
                keyboards.kb([[("🏠 Menu", "m:main")]]))


@action("privacy")
@guard(owner_only=False)
async def privacy(update: Update, context: Ctx, user, page: int = 0) -> None:
    await reply(update, PRIVACY, keyboards.kb([[("🏠 Menu", "m:main")]]))


@guard(owner_only=False)
async def cancel_idle(update: Update, context: Ctx, user) -> None:
    context.user_data.pop("form", None)
    await reply(update, "Nothing to cancel right now. 👍", edit=False)


ACTIONS["help"] = lambda update, context, page=0: help_cmd(update, context)


# ---- generic callback routers -----------------------------------------------------------------
@guard(owner_only=False)
async def on_menu(update: Update, context: Ctx, user) -> None:
    key = update.callback_query.data.split(":", 1)[1]
    if key == "main":
        await reply(update, "📍 <b>Main menu</b>", keyboards.main_menu())
    elif key in keyboards.SUB:
        title, markup = keyboards.submenu(key)
        await reply(update, title, markup)


async def on_action(update: Update, context: Ctx) -> None:
    m = re.fullmatch(r"a:(\w+)(?::(\d+))?", update.callback_query.data or "")
    fn = ACTIONS.get(m.group(1)) if m else None
    if fn is None:
        await update.callback_query.answer("Unknown action")
        return
    await fn(update, context, page=int(m.group(2) or 0))


async def on_confirm(update: Update, context: Ctx) -> None:
    m = re.fullmatch(r"cf:(\w+):(\d+)", update.callback_query.data or "")
    if not m:
        return
    if m.group(1) == "no":
        await update.callback_query.answer()
        await reply(update, "✖️ Cancelled. Nothing was changed.", keyboards.kb([[("🏠 Menu", "m:main")]]))
        return
    fn = CONFIRMS.get(m.group(1))
    if fn is None:
        await update.callback_query.answer("Unknown action")
        return
    await fn(update, context, item_id=int(m.group(2)))


@guard(owner_only=False)
async def on_ask(update: Update, context: Ctx, user) -> None:
    _, act, item = update.callback_query.data.split(":")
    await reply(update, "⚠️ Are you sure? This cannot be undone.", keyboards.yes_no(act, item))


def get_handlers() -> list:
    return [CommandHandler("start", start), CommandHandler("help", help_cmd), CommandHandler("menu", menu),
            CommandHandler("about", about), CommandHandler("id", id_cmd), CommandHandler("ping", ping),
            CommandHandler("profile", profile), CommandHandler("privacy", privacy), CommandHandler("cancel", cancel_idle),
            CallbackQueryHandler(on_menu, pattern=r"^m:\w+$"), CallbackQueryHandler(on_action, pattern=r"^a:\w+(:\d+)?$"),
            CallbackQueryHandler(on_confirm, pattern=r"^cf:\w+:\d+$"), CallbackQueryHandler(on_ask, pattern=r"^ask:\w+:\d+$")]
