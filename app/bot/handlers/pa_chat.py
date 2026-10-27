"""Personal Assistant (PA) natural-language conversational handler.

Allows Uday to chat naturally with his 24/7 Personal Assistant:
- Natural language task creation ("Add task: ...", "Todo: ...", "Task: ...")
- Natural language reminder scheduling ("Remind me to ... at 5pm")
- Daily agenda queries ("What are my tasks today?", "My agenda")
- General AI chat & questions without needing to prefix with /ask
"""
from __future__ import annotations

import re
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

from app.bot import keyboards
from app.bot.guards import guard, reply
from app.bot.handlers.ai import run_ai
from app.database.session import session_scope
from app.services import reminder_service, task_service
from app.utils.dates import fmt_dt, parse_when
from app.utils.formatting import esc

Ctx = ContextTypes.DEFAULT_TYPE


@guard(owner_only=False)
async def handle_pa_chat(update: Update, context: Ctx, user) -> None:
    text = (update.effective_message.text or "").strip()
    if not text:
        return

    tz = ZoneInfo(user.timezone)
    raw = text.lower().strip()

    # 1. Greeting & Assistant Status
    if raw in {"hi", "hello", "hey", "good morning", "good evening", "gm", "ge", "pa", "help", "who are you", "who r u"}:
        with session_scope() as s:
            pending = task_service.list_tasks(s, user.id, "pending")
            due_today, overdue = task_service.today_tasks(s, user.id, tz)
            reminders = reminder_service.list_reminders(s, user.id)

        name = esc(user.first_name or "Uday")
        briefing = f"• 📋 <b>Pending Tasks:</b> {len(pending)}"
        if overdue:
            briefing += f" (⚠️ {len(overdue)} overdue)"
        if due_today:
            briefing += f" (📅 {len(due_today)} due today)"
        briefing += f"\n• ⏰ <b>Active Reminders:</b> {len(reminders)}"

        msg = (
            f"👋 <b>Good day, {name}!</b> I am your <b>Executive Personal Assistant (PA)</b>.\n\n"
            f"📊 <b>Today's Briefing:</b>\n{briefing}\n\n"
            "⚡ <b>How may I assist you, sir?</b>\n"
            "• Type naturally: <i>'Add task: submit project tomorrow 5pm'</i>\n"
            "• Or schedule alerts: <i>'Remind me to call client at 4pm'</i>\n"
            "• Or check agenda: <i>'What are my tasks today?'</i>\n"
            "• Or ask any coding or freelance questions directly!"
        )
        kb = keyboards.kb([
            [("📅 Today's Agenda", "a:today"), ("📋 All Tasks", "a:tasks")],
            [("⏰ Reminders", "a:reminders"), ("🏠 Main Menu", "m:main")],
        ])
        await reply(update, msg, kb, edit=False)
        return

    # 2. Agenda / Today's tasks inquiry
    if any(q in raw for q in ["what are my tasks", "today tasks", "today's tasks", "my agenda", "schedule for today",
                             "today's schedule", "what do i have today", "daily plan", "show tasks", "my schedule"]):
        with session_scope() as s:
            due, overdue = task_service.today_tasks(s, user.id, tz)
            reminders = reminder_service.list_reminders(s, user.id)

        parts = ["📋 <b>Your Agenda for Today, Uday:</b>\n"]
        if overdue:
            parts.append("⚠️ <b>Overdue Tasks:</b>")
            for t in overdue:
                parts.append(f"• #{t.id} {esc(t.title)}" + (f" (due {fmt_dt(t.due_at, tz)})" if t.due_at else ""))
            parts.append("")
        if due:
            parts.append("📅 <b>Due Today:</b>")
            for t in due:
                parts.append(f"• #{t.id} {esc(t.title)}" + (f" (at {fmt_dt(t.due_at, tz)})" if t.due_at else ""))
            parts.append("")
        elif not overdue:
            parts.append("✨ <i>No pending tasks due today. You are on track!</i>\n")

        if reminders:
            parts.append("⏰ <b>Upcoming Reminders:</b>")
            for r in reminders[:5]:
                parts.append(f"• 🔔 {esc(r.message)} — {fmt_dt(r.remind_at, tz)}")
            parts.append("")

        kb = keyboards.kb([[("✅ Add Task", "a:task"), ("⏰ Set Reminder", "a:remind")], [("🏠 Menu", "m:main")]])
        await reply(update, "\n".join(parts), kb, edit=False)
        return

    # 3. Quick Task Creation
    # Matches: "add task ...", "task: ...", "todo: ...", "create task ...", "new task ..."
    task_match = re.match(r"^(?:add\s+task|new\s+task|create\s+task|task|todo)[:\s]+(.+)$", text, re.IGNORECASE)
    if task_match:
        content = task_match.group(1).strip()
        priority = "medium"
        if re.search(r"\b(urgent|asap|high priority|important)\b", content, re.IGNORECASE):
            priority = "high"
            content = re.sub(r"\b(urgent|asap|high priority|important)\b", "", content, flags=re.IGNORECASE).strip()

        due_dt = None
        due_match = re.search(r"\b(?:due|by|at|on)\s+(tomorrow.*|today.*|in\s+\d+.*|\d{1,2}\s+[a-zA-Z]+.*|\d{1,2}:\d{2}.*)$", content, re.IGNORECASE)
        if due_match:
            when_str = due_match.group(1).strip()
            parsed_when = parse_when(when_str, tz, require_time=False)
            if parsed_when.dt:
                due_dt = parsed_when.dt
                content = content[:due_match.start()].strip(" -:,")

        if not content:
            content = "New task"

        with session_scope() as s:
            t = task_service.create_task(s, user.id, content, priority=priority, due_at=due_dt)
            due_str = f"\n📆 Due: <b>{fmt_dt(t.due_at, tz)}</b> (reminder scheduled)" if t.due_at else ""

        msg = (
            f"✅ <b>Task Added to Your Agenda, Uday!</b>\n\n"
            f"📌 <b>#{t.id} {esc(t.title)}</b>\n"
            f"🎯 Priority: <b>{t.priority.capitalize()}</b>{due_str}\n\n"
            f"<i>Type <code>/done {t.id}</code> when completed.</i>"
        )
        kb = keyboards.kb([[("📋 View Tasks", "a:tasks"), ("🏠 Menu", "m:main")]])
        await reply(update, msg, kb, edit=False)
        return

    # 4. Quick Reminder Scheduling
    # Matches: "remind me to ... at/in/tomorrow/today..." or "reminder: ..."
    remind_match = re.match(r"^(?:remind\s+me(?:\s+to)?|reminder[:\s])\s+(.+)$", text, re.IGNORECASE)
    if remind_match:
        content = remind_match.group(1).strip()
        time_split = re.search(r"\b(at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?|in\s+\d+\s+(?:minutes?|hours?|days?)|tomorrow.*|tonight.*|today.*|\d{1,2}\s+[a-zA-Z]+.*)$", content, re.IGNORECASE)
        if time_split:
            time_part = time_split.group(0).strip()
            action_part = content[:time_split.start()].strip(" -:,")
            parsed = parse_when(time_part, tz, require_time=True)
            if parsed.dt:
                if not action_part:
                    action_part = "Reminder"
                with session_scope() as s:
                    r = reminder_service.create_reminder(s, user.id, action_part, parsed.dt)
                msg = (
                    f"⏰ <b>Reminder Scheduled, Uday!</b>\n\n"
                    f"🔔 <i>\"{esc(r.message)}\"</i>\n"
                    f"🕒 Alert at: <b>{fmt_dt(r.remind_at, tz)}</b>\n\n"
                    f"I will alert you right here on Telegram when it's time!"
                )
                kb = keyboards.kb([[("⏰ All Reminders", "a:reminders"), ("🏠 Menu", "m:main")]])
                await reply(update, msg, kb, edit=False)
                return

    # 5. Executive AI Assistant Chat
    try:
        await context.bot.send_chat_action(update.effective_chat.id, "typing")
    except Exception:
        pass

    out = await run_ai(context, user, text, "ask")
    if out.startswith("🤖 "):
        out = "🤖 <b>PA:</b> " + out[3:]
    await reply(update, out, keyboards.kb([[("🏠 Menu", "m:main")]]), edit=False)


def get_handlers() -> list:
    return [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_pa_chat)]
