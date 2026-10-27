"""AI assistant commands. Every command degrades to a deterministic helper when the model is offline."""
from __future__ import annotations

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from app.bot import keyboards
from app.bot.forms import Field, Form, build_form_handler
from app.bot.guards import action, guard, reply
from app.database.session import session_scope
from app.services import ai_service as ai_mod
from app.services.developer_service import builtin_help
from app.services.ollama_service import AIError, AITimeout, AIUnavailable
from app.utils import validation as v
from app.utils.formatting import esc

Ctx = ContextTypes.DEFAULT_TYPE


async def run_ai(context: Ctx, user, text: str, mode: str) -> str:
    """Return HTML. Falls back to built-in helpers when the model is unavailable."""
    ai = context.bot_data["ai"]
    if not user.ai_enabled:
        return "🤖 AI is turned off in /settings."
    try:
        with session_scope() as s:
            out = await ai.respond(s, user.id, text, mode, use_history=(mode == "ask"))
        return f"🤖 {esc(out)}"
    except (AIUnavailable, AITimeout) as exc:
        note = f"{esc(ai_mod.OFFLINE_NOTE)}\n<i>({esc(exc)})</i>\n\n"
        if mode == "summarize":
            return note + "<b>Extractive summary</b>\n" + esc(ai_mod.fallback_summary(text))
        if mode == "rewrite":
            return note + "<b>Basic clean-up</b>\n" + esc(ai_mod.fallback_rewrite(text))
        if mode in {"explain", "devhelp"}:
            return note + esc(builtin_help(text))
        return note + "I can't answer free-form questions without the AI model, but tasks, reminders, snippets, freelance and club tools all work."
    except AIError as exc:
        return f"⚠️ {esc(exc)}"


def _text(max_len: int):
    return lambda t, d, u, s: v.clean(t, max_len=max_len, field="Text")


def _form(name: str, title: str, prompt: str, mode: str, from_reply: bool = False) -> Form:
    async def fin(update, context, user, data):
        try:
            await context.bot.send_chat_action(update.effective_chat.id, "typing")
        except Exception:  # noqa: BLE001 - cosmetic only
            pass
        return await run_ai(context, user, data["text"], mode)
    return Form(name=name, commands=[name], title=title, fields=[Field("text", prompt, _text(20000))], finish=fin, from_reply=from_reply)


FORMS = [
    _form("ask", "Ask AI", "What would you like to ask?", "ask"),
    _form("summarize", "Summarize", "Send (or reply with /summarize to) the text to summarize.", "summarize", True),
    _form("rewrite", "Rewrite", "Send (or reply with /rewrite to) the text to rewrite.", "rewrite", True),
    _form("explain", "Explain", "What topic or code should I explain?", "explain"),
]


@action("clear")
@guard()
async def clear(update: Update, context: Ctx, user, page: int = 0) -> None:
    with session_scope() as s:
        n = ai_mod.clear_history(s, user.id)
    await reply(update, f"🧹 AI conversation memory cleared ({n} messages).", keyboards.kb([[("🏠 Menu", "m:main")]]), edit=False)


def get_handlers(timeout: int | None = 900) -> list:
    return [CommandHandler("clear", clear), *[build_form_handler(f, timeout=timeout) for f in FORMS]]
