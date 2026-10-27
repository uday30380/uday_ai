"""Club management with owner/admin/member permissions enforced in club_service."""
from __future__ import annotations

import io
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from app.bot import keyboards
from app.bot.forms import Field, Form, build_form_handler
from app.bot.guards import action, guard, reply, usage
from app.database.models import Club, ClubMember
from app.database.session import session_scope
from app.services import club_service as cs
from app.services.club_service import PermissionDenied
from app.utils import validation as v
from app.utils.dates import fmt_dt, parse_when
from app.utils.formatting import esc

Ctx = ContextTypes.DEFAULT_TYPE
HOME = keyboards.kb([[("🏠 Menu", "m:main")]])


def _club(text, data, user, settings):
    with session_scope() as s:
        return cs.resolve_club(s, user, text).id


def _when(require_time: bool):
    def parse(text, data, user, settings):
        res = parse_when(text, ZoneInfo(user.timezone), require_time=require_time)
        if res.dt is None:
            raise ValueError(res.clarify or res.error)
        return res.dt
    return parse


def _club_name(club_id: int) -> str:
    with session_scope() as s:
        return s.get(Club, club_id).name


async def _club_fin(update, context, user, d):
    with session_scope() as s:
        c = cs.create_club(s, user, d["name"], d["desc"] or "")
        return f"🏫 Club <b>{esc(c.name)}</b> created (ID {c.id}). You are the owner."


CLUB_FORM = Form(name="club_new", commands=["newclub"], title="New club", finish=_club_fin, owner_only=False,
                 fields=[Field("name", "Club name?", lambda t, d, u, s: v.clean(t, max_len=100, field="Name")),
                         Field("desc", "Description?", lambda t, d, u, s: v.clean(t, max_len=1000), optional=True, default="")])


async def _event_fin(update, context, user, d):
    with session_scope() as s:
        e = cs.create_event(s, d["club"], user, d["title"], d["kind"] or "event", d["when"], d["location"] or "")
        return f"📅 {esc(e.kind.title())} <b>#{e.id}</b> “{esc(e.title)}” scheduled for {fmt_dt(e.starts_at, ZoneInfo(user.timezone))}."


EVENT_FORM = Form(name="event", commands=["event"], title="New club event", finish=_event_fin, owner_only=False,
                  fields=[Field("club", "Which club (name or ID)?", _club),
                          Field("title", "Title?", lambda t, d, u, s: v.clean(t, max_len=160, field="Title")),
                          Field("kind", "Type: event, workshop or meeting?", lambda t, d, u, s: t.strip().lower() if t.strip().lower() in {"event", "workshop", "meeting"} else (_ for _ in ()).throw(ValueError("Choose event, workshop or meeting.")), optional=True, default="event"),
                          Field("when", "When? e.g. <i>sat 4pm</i>", _when(True)),
                          Field("location", "Location?", lambda t, d, u, s: v.clean(t, max_len=160), optional=True, default="")])


async def _member_fin(update, context, user, d):
    with session_scope() as s:
        m = cs.add_member(s, d["club"], user, d["name"], d["role"] or "member", d["tg"], d["contact"] or "")
        note = "" if m.telegram_id else "\n<i>No Telegram ID: they can't use the bot or receive announcements.</i>"
        return f"🙋 Added <b>{esc(m.name)}</b> as {m.role} (ID {m.id}).{note}"


MEMBER_FORM = Form(name="member", commands=["member"], title="Add member", finish=_member_fin, owner_only=False,
                   fields=[Field("club", "Which club (name or ID)?", _club),
                           Field("name", "Member name?", lambda t, d, u, s: v.clean(t, max_len=100, field="Name")),
                           Field("role", "Role: member or admin?", lambda t, d, u, s: t.strip().lower() if t.strip().lower() in {"member", "admin"} else (_ for _ in ()).throw(ValueError("Choose member or admin.")), optional=True, default="member"),
                           Field("tg", "Their numeric Telegram ID? (needed for bot access/announcements)", lambda t, d, u, s: v.telegram_id(t), optional=True),
                           Field("contact", "Contact (email/phone)? Visible to admins only.", lambda t, d, u, s: v.clean(t, max_len=160), optional=True, default="")])


def _assignee(text, data, user, settings):
    with session_scope() as s:
        m = cs.find_member(s, data["club"], text)
        if m is None:
            raise ValueError("No member with that name/ID in this club.")
        return m.id


async def _ctask_fin(update, context, user, d):
    with session_scope() as s:
        m = s.get(ClubMember, d["assignee"]) if d["assignee"] else None
        t = cs.create_task(s, d["club"], user, d["title"], m, d["deadline"])
        who = f" → {esc(m.name)}" if m else ""
        return f"📋 Club task <b>#{t.id}</b> created{who}: {esc(t.title)}"


CLUBTASK_FORM = Form(name="clubtask", commands=["clubtask"], title="Assign club task", finish=_ctask_fin, owner_only=False,
                     fields=[Field("club", "Which club (name or ID)?", _club),
                             Field("title", "Task?", lambda t, d, u, s: v.clean(t, max_len=200, field="Task")),
                             Field("assignee", "Assign to (member name or ID)?", _assignee, optional=True),
                             Field("deadline", "Deadline? e.g. <i>friday</i>", _when(False), optional=True)])


def _ann_summary(d, user, settings):
    return f"Club: <b>{esc(_club_name(d['club']))}</b>\n\n{esc(d['text'])}\n\n⚠️ This will be sent to <b>every member with a Telegram ID</b>."


async def _ann_fin(update, context, user, d):
    async def send(tid: int, text: str) -> None:
        await context.bot.send_message(tid, text)
    with session_scope() as s:
        a = cs.draft_announcement(s, d["club"], user, d["text"])
        aid = a.id
    a = await cs.send_announcement(session_scope, user, aid, send)
    return f"📢 Announcement sent. Delivered: <b>{a.delivered}</b>, failed: <b>{a.failed}</b> (members must have started the bot)."


ANNOUNCE_FORM = Form(name="announce", commands=["announce"], title="Announcement", finish=_ann_fin, summary=_ann_summary, owner_only=False,
                     fields=[Field("club", "Which club (name or ID)?", _club),
                             Field("text", "Announcement text?", lambda t, d, u, s: v.clean(t, max_len=3000, field="Text"))])


# ---- listings -------------------------------------------------------------------------------------
@action("clubs")
@guard(owner_only=False)
async def clubs(update: Update, context: Ctx, user, page: int = 0) -> None:
    with session_scope() as s:
        rows = cs.my_clubs(s, user)
    if not rows:
        return await reply(update, "You're not in any club yet.", keyboards.kb([[("➕ New club", "f:club_new")], [("🏠 Menu", "m:main")]]))
    text = "🏫 <b>Your clubs</b>\n\n" + "\n".join(f"• <b>{esc(c.name)}</b> (ID {c.id}) — {r}" for c, r in rows)
    await reply(update, text + "\n\n/members · /events · /clubtasks &lt;club&gt;", keyboards.kb([[("➕ New club", "f:club_new")], [("🏠 Menu", "m:main")]]))


def _pick_club(session, user, args) -> Club:
    if args:
        return cs.resolve_club(session, user, " ".join(args))
    rows = cs.my_clubs(session, user)
    if len(rows) == 1:
        return rows[0][0]
    raise ValueError("Specify the club name or ID, e.g. /members 1")


async def _listing(update, context, user, kind: str) -> None:
    tz = ZoneInfo(user.timezone)
    try:
        with session_scope() as s:
            club = _pick_club(s, user, context.args)
            if kind == "members":
                lines = [f"#{m['id']} <b>{esc(m['name'])}</b> ({m['role']})" + (f" · {esc(m['contact'])}" if m["contact"] else "")
                         for m in cs.list_members(s, club.id, user)]
            elif kind == "events":
                lines = [f"#{e.id} {fmt_dt(e.starts_at, tz)} — <b>{esc(e.title)}</b> [{e.kind}] {esc(e.location)}" for e in cs.list_events(s, club.id, user)]
            else:
                names = {m.id: m.name for m in s.query(ClubMember).filter(ClubMember.club_id == club.id)}
                lines = [f"#{t.id} <b>{esc(t.title)}</b> → {esc(names.get(t.assignee_id, 'unassigned'))} · due {fmt_dt(t.deadline, tz)}" for t in cs.list_tasks(s, club.id, user)]
            name = club.name
        await reply(update, f"🏫 <b>{esc(name)}</b> — {kind}\n\n" + ("\n".join(lines) or "Nothing here yet."), HOME, edit=False)
    except (ValueError, PermissionDenied) as exc:
        await reply(update, f"❌ {esc(exc)}", edit=False)


@guard(owner_only=False)
async def members(update, context, user):
    await _listing(update, context, user, "members")


@guard(owner_only=False)
async def events(update, context, user):
    await _listing(update, context, user, "events")


@guard(owner_only=False)
async def clubtasks(update, context, user):
    await _listing(update, context, user, "tasks")


@guard(owner_only=False)
async def clubdone(update: Update, context: Ctx, user) -> None:
    try:
        tid = v.positive_int(context.args[0] if context.args else "", "Task ID")
        with session_scope() as s:
            cs.complete_task(s, user, tid)
        await reply(update, f"✅ Club task #{tid} done.", edit=False)
    except (ValueError, PermissionDenied) as exc:
        await reply(update, f"❌ {esc(exc)}\nUsage: /clubdone &lt;task_id&gt;", edit=False)


@guard(owner_only=False)
async def clubexport(update: Update, context: Ctx, user) -> None:
    args = context.args or []
    if not args:
        return await usage(update, "Usage: /clubexport &lt;club&gt; &lt;members|events|tasks&gt;")
    try:
        with session_scope() as s:
            club = cs.resolve_club(s, user, " ".join(args[:-1])) if len(args) > 1 else _pick_club(s, user, [])
            fname, data = cs.export_csv(s, club.id, user, args[-1].lower())
        await update.effective_message.reply_document(io.BytesIO(data), filename=fname)
    except (ValueError, PermissionDenied) as exc:
        await reply(update, f"❌ {esc(exc)}", edit=False)


@guard(owner_only=False)
async def club_cmd(update: Update, context: Ctx, user) -> None:
    await clubs(update, context)


def get_handlers(timeout: int | None = 900) -> list:
    return [*[build_form_handler(f, timeout=timeout) for f in (CLUB_FORM, EVENT_FORM, MEMBER_FORM, CLUBTASK_FORM, ANNOUNCE_FORM)],
            CommandHandler("members", members), CommandHandler("events", events),
            CommandHandler("club", clubs), CommandHandler("clubtasks", clubtasks), CommandHandler("clubdone", clubdone), CommandHandler("clubexport", clubexport)]
