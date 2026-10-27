"""Developer tools: snippets, projects, work log and dev help. User code is only stored, never executed."""
from __future__ import annotations

import io

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from app.bot import keyboards
from app.bot.forms import Field, Form, build_form_handler
from app.bot.guards import action, confirm_action, guard, reply, usage
from app.bot.handlers.ai import run_ai
from app.database.session import session_scope
from app.services import ai_service, developer_service as dev
from app.utils import validation as v
from app.utils.dates import fmt_dt
from app.utils.formatting import code_block, esc

Ctx = ContextTypes.DEFAULT_TYPE
HOME = keyboards.kb([[("🏠 Menu", "m:main")]])
PER = 10


# ---- snippets -------------------------------------------------------------------------------------
async def _snippet_pre(update: Update, context: Ctx, user) -> bool:
    if not context.args:
        return False
    with session_scope() as s:
        sn = dev.get_snippet(s, user.id, " ".join(context.args))
    if sn is None:
        return False
    head = f"💾 <b>{esc(sn.name)}</b> ({esc(sn.language)})\n{esc(sn.description)}\n"
    markup = keyboards.kb([[("🗑 Delete", f"ask:delsnip:{sn.id}")], [("🏠 Menu", "m:main")]])
    if len(sn.code) > 3000:
        await reply(update, head + "<i>Code is long — sent as a file.</i>", markup, edit=False)
        await update.effective_message.reply_document(io.BytesIO(sn.code.encode()), filename=f"{sn.name}.txt")
    else:
        await reply(update, head + code_block(sn.code, sn.language), markup, edit=False)
    return True


async def _snippet_fin(update, context, user, d):
    with session_scope() as s:
        sn = dev.save_snippet(s, user.id, d["name"], d["language"], d["description"] or "", d["code"])
        return f"💾 Snippet <b>{esc(sn.name)}</b> saved. Retrieve it with /snippet {esc(sn.name)}"


SNIPPET_FORM = Form(
    name="snippet_new", commands=["snippet"], title="Save snippet", finish=_snippet_fin, pre=_snippet_pre,
    fields=[Field("name", "Snippet name (letters, numbers, dashes)?", lambda t, d, u, s: v.clean(t, max_len=60, field="Name")),
            Field("language", "Language? (python, java, js, sql, bash…)", lambda t, d, u, s: v.clean(t, max_len=30, field="Language"), optional=True, default="text"),
            Field("description", "Short description?", lambda t, d, u, s: v.clean(t, max_len=300), optional=True, default=""),
            Field("code", "Now send the code (it will be stored, never run).", lambda t, d, u, s: v.clean(t, max_len=20000, field="Code"))])


@action("snippets")
@guard()
async def snippets(update: Update, context: Ctx, user, page: int = 0) -> None:
    with session_scope() as s:
        items = dev.list_snippets(s, user.id)
    if not items:
        return await reply(update, "No snippets yet. Save one with /snippet &lt;name&gt;", keyboards.kb([[("💾 Save snippet", "f:snippet_new")], [("🏠 Menu", "m:main")]]))
    chunk = items[page * PER:(page + 1) * PER]
    text = f"📚 <b>Snippets</b> ({len(items)})\n\n" + "\n".join(f"• <code>{esc(x.name)}</code> [{esc(x.language)}] {esc(x.description)}" for x in chunk)
    await reply(update, text + "\n\nOpen one: /snippet &lt;name&gt;", keyboards.pager("snippets", page, len(items), PER))


@confirm_action("delsnip")
@guard()
async def delsnip(update: Update, context: Ctx, user, item_id: int) -> None:
    try:
        with session_scope() as s:
            dev.delete_snippet(s, user.id, item_id)
        await reply(update, "🗑 Snippet deleted.", HOME)
    except LookupError as exc:
        await reply(update, f"❌ {esc(exc)}", HOME)


# ---- projects -------------------------------------------------------------------------------------
async def _project_pre(update: Update, context: Ctx, user) -> bool:
    if not context.args:
        return False
    with session_scope() as s:
        p = dev.get_project(s, user.id, " ".join(context.args))
        if p is None:
            return False
        acts = "\n".join(f"  • {fmt_dt(a.created_at, __import__('zoneinfo').ZoneInfo(user.timezone))}: {esc(a.description)}" for a in p.activity[:5])
        text = (f"📁 <b>{esc(p.name)}</b> — {esc(p.status)}\nTech: {esc(p.technologies) or '—'}\nRepo: {esc(p.repo_url) or '—'}\n"
                f"Notes: {esc(p.notes) or '—'}\n\n<b>Recent work</b>\n{acts or '  (none yet — /logwork)'}")
    await reply(update, text, HOME, edit=False)
    return True


async def _project_fin(update, context, user, d):
    with session_scope() as s:
        p = dev.create_project(s, user.id, d["name"], d["tech"] or "", d["repo"] or "", d["notes"] or "")
        return f"📁 Project <b>{esc(p.name)}</b> created. Log progress with /logwork {esc(p.name.split()[0])} &lt;what you did&gt;"


PROJECT_FORM = Form(
    name="project_new", commands=["project"], title="New project", finish=_project_fin, pre=_project_pre,
    fields=[Field("name", "Project name?", lambda t, d, u, s: v.clean(t, max_len=80, field="Name")),
            Field("tech", "Technologies? (e.g. Python, React)", lambda t, d, u, s: v.clean(t, max_len=200), optional=True, default=""),
            Field("repo", "Repository URL?", lambda t, d, u, s: v.url(t), optional=True, default=""),
            Field("notes", "Notes?", lambda t, d, u, s: v.clean(t, max_len=2000), optional=True, default="")])


@action("projects")
@guard()
async def projects(update: Update, context: Ctx, user, page: int = 0) -> None:
    with session_scope() as s:
        items = dev.list_projects(s, user.id)
    if not items:
        return await reply(update, "No projects yet. Create one with /project &lt;name&gt;", keyboards.kb([[("🆕 New project", "f:project_new")], [("🏠 Menu", "m:main")]]))
    chunk = items[page * PER:(page + 1) * PER]
    text = f"📁 <b>Projects</b> ({len(items)})\n\n" + "\n".join(f"• <b>{esc(p.name)}</b> [{esc(p.status)}] {esc(p.technologies)}" for p in chunk)
    await reply(update, text, keyboards.pager("projects", page, len(items), PER))


@guard()
async def project_status(update: Update, context: Ctx, user) -> None:
    args = context.args or []
    if len(args) < 2:
        return await usage(update, "Usage: /project_status &lt;name&gt; &lt;" + "|".join(sorted(dev.PROJECT_STATUSES)) + "&gt; (name without spaces, or quote-free single word)")
    try:
        with session_scope() as s:
            p = dev.set_status(s, user.id, " ".join(args[:-1]), args[-1])
            name, st = p.name, p.status
        await reply(update, f"✅ {esc(name)} is now <b>{esc(st)}</b>.", edit=False)
    except (ValueError, LookupError) as exc:
        await reply(update, f"❌ {esc(exc)}", edit=False)


async def _logwork_fin(update, context, user, d):
    with session_scope() as s:
        dev.log_work(s, user.id, d["project"], d["desc"])
    return f"🕒 Logged for <b>{esc(d['project'])}</b>: {esc(d['desc'])}"


def _project_exists(text, data, user, settings):
    with session_scope() as s:
        p = dev.get_project(s, user.id, text)
        if p is None:
            raise ValueError("No such project. Create it first with /project &lt;name&gt;".replace("&lt;", "<").replace("&gt;", ">"))
        return p.name


LOGWORK_FORM = Form(name="logwork", commands=[], title="Log work", finish=_logwork_fin,
                    fields=[Field("project", "Which project?", _project_exists),
                            Field("desc", "What did you do?", lambda t, d, u, s: v.clean(t, max_len=500, field="Description"))])


@guard()
async def logwork(update: Update, context: Ctx, user) -> None:
    args = context.args or []
    if len(args) < 2:
        return await usage(update, "Usage: /logwork &lt;project&gt; &lt;description&gt; (project = first word)\nor use the 🕒 Log work button for a guided form.")
    try:
        with session_scope() as s:
            p = dev.get_project(s, user.id, args[0])
            if p is None:
                raise LookupError(f"Project '{args[0]}' not found. Multi-word project? Use the Log work button.")
            dev.log_work(s, user.id, p.name, " ".join(args[1:]))
            name = p.name
        await reply(update, f"🕒 Logged for <b>{esc(name)}</b>.", edit=False)
    except (ValueError, LookupError) as exc:
        await reply(update, f"❌ {esc(exc)}", edit=False)


async def _devhelp_fin(update, context, user, d):
    ai = context.bot_data["ai"]
    if ai.client is not None and user.ai_enabled and await ai.client.is_available():
        return await run_ai(context, user, d["q"], "devhelp")
    return "🤖 <i>AI offline — built-in help</i>\n\n" + esc(dev.builtin_help(d["q"]))


DEVHELP_FORM = Form(name="devhelp", commands=["devhelp"], title="Dev help", finish=_devhelp_fin,
                    fields=[Field("q", "What do you need help with? (Git, Java, Python, JavaScript, SQL, workflow, or paste an error)",
                                  lambda t, d, u, s: v.clean(t, max_len=4000, field="Question"))])


def get_handlers(timeout: int | None = 900) -> list:
    return [*[build_form_handler(f, timeout=timeout) for f in (SNIPPET_FORM, PROJECT_FORM, LOGWORK_FORM, DEVHELP_FORM)],
            CommandHandler("snippets", snippets), CommandHandler("projects", projects),
            CommandHandler("project_status", project_status), CommandHandler("logwork", logwork)]
