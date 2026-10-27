"""Freelance manager: clients, projects, payments (append-only), follow-ups and invoices."""
from __future__ import annotations

from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from app.bot import keyboards
from app.bot.forms import Field, Form, build_form_handler
from app.bot.guards import action, confirm_action, guard, reply, usage
from app.database.session import session_scope
from app.services import freelance_service as fl
from app.utils import validation as v
from app.utils.dates import fmt_dt, parse_when
from app.utils.formatting import esc, money

Ctx = ContextTypes.DEFAULT_TYPE
HOME = keyboards.kb([[("🏠 Menu", "m:main")]])
PER = 6


def _client_lookup(text, data, user, settings):
    with session_scope() as s:
        c = fl.get_client(s, user.id, text)
        if c is None:
            raise ValueError("No such client. Create them first with /client")
        return c.id


def _when(require_time: bool):
    def parse(text, data, user, settings):
        res = parse_when(text, ZoneInfo(user.timezone), require_time=require_time)
        if res.dt is None:
            raise ValueError(res.clarify or res.error)
        return res.dt
    return parse


# ---- client ---------------------------------------------------------------------------------------
async def _client_fin(update, context, user, d):
    with session_scope() as s:
        c = fl.add_client(s, user.id, d["name"], d["email"] or "", d["phone"] or "", d["notes"] or "")
        return f"👤 Client <b>{esc(c.name)}</b> saved (ID {c.id}). Next: /newproject"


CLIENT_FORM = Form(name="client", commands=["client"], title="New client", finish=_client_fin,
                   fields=[Field("name", "Client name?", lambda t, d, u, s: v.clean(t, max_len=120, field="Name")),
                           Field("email", "Email?", lambda t, d, u, s: v.clean(t, max_len=160), optional=True, default=""),
                           Field("phone", "Phone?", lambda t, d, u, s: v.clean(t, max_len=40), optional=True, default=""),
                           Field("notes", "Notes?", lambda t, d, u, s: v.clean(t, max_len=1000), optional=True, default="")])


@action("clients")
@guard()
async def clients(update: Update, context: Ctx, user, page: int = 0) -> None:
    with session_scope() as s:
        rows = []
        for c in fl.list_clients(s, user.id):
            projs = [p for p in fl.list_projects(s, user.id) if p.client_id == c.id]
            owed = {}
            for p in projs:
                if p.status != "cancelled":
                    owed[p.currency] = owed.get(p.currency, 0) + fl.outstanding_minor(s, p)
            rows.append(f"• <b>{esc(c.name)}</b> (ID {c.id}) — {len(projs)} project(s), owes " + (", ".join(money(m, cur) for cur, m in owed.items()) or "nothing"))
    if not rows:
        return await reply(update, "No clients yet. Add one with /client", keyboards.kb([[("👤 New client", "f:client")], [("🏠 Menu", "m:main")]]))
    await reply(update, f"👥 <b>Clients</b> ({len(rows)})\n\n" + "\n".join(rows[page * PER:(page + 1) * PER]), keyboards.pager("clients", page, len(rows), PER))


# ---- project --------------------------------------------------------------------------------------
async def _proj_fin(update, context, user, d):
    with session_scope() as s:
        c = s.get(fl.Client, d["client"])
        p = fl.create_project(s, user.id, c, d["title"], d["desc"] or "", d["amount"], d["currency"] or user.default_currency, d["deadline"])
        return f"📌 Project <b>#{p.id}</b> “{esc(p.title)}” for {esc(c.name)} — quoted {money(p.quoted_minor, p.currency)}."


PROJECT_FORM = Form(name="newproject", commands=["newproject"], title="New freelance project", finish=_proj_fin,
                    fields=[Field("client", "Client name or ID?", _client_lookup),
                            Field("title", "Project title?", lambda t, d, u, s: v.clean(t, max_len=160, field="Title")),
                            Field("desc", "Description?", lambda t, d, u, s: v.clean(t, max_len=2000), optional=True, default=""),
                            Field("amount", "Quoted amount? (e.g. 15000)", lambda t, d, u, s: v.amount_minor(t)),
                            Field("currency", "Currency code? (default INR)", lambda t, d, u, s: v.currency(t), optional=True, default="INR"),
                            Field("deadline", "Deadline? (e.g. 30 oct)", _when(False), optional=True)])


@action("freelance")
@guard()
async def freelance(update: Update, context: Ctx, user, page: int = 0) -> None:
    tz = ZoneInfo(user.timezone)
    with session_scope() as s:
        sm = fl.summary(s, user.id)
        active = fl.list_projects(s, user.id, "active")
        proj_lines = [f"• <b>#{p.id}</b> {esc(p.title)} ({esc(p.client.name)}) — balance {money(fl.outstanding_minor(s, p), p.currency)}" for p in active[:10]]
    rev = ", ".join(money(m, c) for c, m in sm["revenue"].items()) or "—"
    out = ", ".join(money(m, c) for c, m in sm["outstanding"].items()) or "—"
    od = "\n".join(f"  • #{f.id} {esc(f.client.name)} (due {fmt_dt(f.due_at, tz)}) {esc(f.note)}" for f in sm["overdue_followups"]) or "  none"
    await reply(update, f"📊 <b>Freelance dashboard</b>\nRevenue recorded: <b>{rev}</b>\nOutstanding: <b>{out}</b>\n"
                        f"Active projects: {sm['active_projects']}\n\n<b>Overdue follow-ups</b>\n{od}\n\n<b>Active projects</b>\n" + ("\n".join(proj_lines) or "  none"), HOME)


# ---- payments -------------------------------------------------------------------------------------
def _project_for_payment(text, data, user, settings):
    pid = v.positive_int(text, "Project ID")
    with session_scope() as s:
        p = fl.get_project(s, user.id, pid)
        if p is None:
            raise ValueError("No such project ID. See /freelance for IDs.")
        return p.id


def _amount_for_project(text, data, user, settings):
    amt = v.amount_minor(text)
    with session_scope() as s:
        p = fl.get_project(s, user.id, data["project"])
        due = fl.outstanding_minor(s, p)
        if amt > due:
            raise ValueError(f"That exceeds the outstanding balance ({money(due, p.currency)}).")
    return amt


def _pay_summary(d, user, settings):
    with session_scope() as s:
        p = fl.get_project(s, user.id, d["project"])
        return (f"Project #{p.id} {esc(p.title)} ({esc(p.client.name)})\nPayment: <b>{money(d['amount'], p.currency)}</b>"
                f" via {esc(d['method'] or '—')}\nBalance after: {money(fl.outstanding_minor(s, p) - d['amount'], p.currency)}\n"
                "This is recorded in your ledger only; I can't verify real bank payments.")


async def _pay_fin(update, context, user, d):
    with session_scope() as s:
        pay = fl.record_payment(s, user.id, d["project"], d["amount"], d["method"] or "", d["note"] or "")
        p = fl.get_project(s, user.id, d["project"])
        return f"💵 Payment <b>#{pay.id}</b> recorded. Balance: {money(fl.outstanding_minor(s, p), p.currency)}"


PAYMENT_FORM = Form(name="payment", commands=["payment"], title="Record payment", finish=_pay_fin, summary=_pay_summary,
                    fields=[Field("project", "Project ID? (see /freelance)", _project_for_payment),
                            Field("amount", "Amount received?", _amount_for_project),
                            Field("method", "Method? (UPI, bank, cash…)", lambda t, d, u, s: v.clean(t, max_len=40), optional=True, default=""),
                            Field("note", "Note?", lambda t, d, u, s: v.clean(t, max_len=300), optional=True, default="")])


@guard()
async def reverse_payment(update: Update, context: Ctx, user) -> None:
    try:
        pid = v.positive_int(context.args[0] if context.args else "", "Payment ID")
    except ValueError:
        return await usage(update, "Usage: /reverse_payment &lt;payment_id&gt; — adds a reversal entry; history is kept.")
    await reply(update, f"↩️ Reverse payment <b>#{pid}</b>? A negative entry will be added to the ledger.", keyboards.yes_no("revpay", pid), edit=False)


@confirm_action("revpay")
@guard()
async def revpay(update: Update, context: Ctx, user, item_id: int) -> None:
    try:
        with session_scope() as s:
            fl.reverse_payment(s, user.id, item_id)
        await reply(update, f"↩️ Payment #{item_id} reversed (ledger entry added).", HOME)
    except (ValueError, LookupError) as exc:
        await reply(update, f"❌ {esc(exc)}", HOME)


@guard()
async def payments(update: Update, context: Ctx, user) -> None:
    try:
        pid = v.positive_int(context.args[0] if context.args else "", "Project ID")
    except ValueError:
        return await usage(update, "Usage: /payments &lt;project_id&gt;")
    with session_scope() as s:
        p = fl.get_project(s, user.id, pid)
        if p is None:
            return await reply(update, "❌ Project not found.", edit=False)
        rows = [f"#{x.id} {x.created_at:%d %b %Y} {money(x.amount_minor, p.currency)} {x.kind} {esc(x.method)} {esc(x.note)}" for x in fl.payment_history(s, user.id, pid)]
        tail = f"\nPaid {money(fl.paid_minor(s, pid), p.currency)} of {money(p.quoted_minor, p.currency)}"
    await reply(update, f"🧾 <b>Ledger — {esc(p.title)}</b>\n" + ("\n".join(rows) or "No payments yet.") + tail, edit=False)


# ---- follow-up ------------------------------------------------------------------------------------
async def _fu_fin(update, context, user, d):
    with session_scope() as s:
        c = s.get(fl.Client, d["client"])
        f = fl.add_followup(s, user.id, c, d["when"], d["note"] or "")
        return f"📞 Follow-up <b>#{f.id}</b> with {esc(c.name)} set for {fmt_dt(f.due_at, ZoneInfo(user.timezone))}. I'll remind you."


FOLLOWUP_FORM = Form(name="followup", commands=["followup"], title="Client follow-up", finish=_fu_fin,
                     fields=[Field("client", "Client name or ID?", _client_lookup),
                             Field("when", "When? e.g. <i>friday 11am</i>", _when(True)),
                             Field("note", "Note?", lambda t, d, u, s: v.clean(t, max_len=300), optional=True, default="")])


@guard()
async def followup_done(update: Update, context: Ctx, user) -> None:
    try:
        fid = v.positive_int(context.args[0] if context.args else "", "Follow-up ID")
        with session_scope() as s:
            fl.complete_followup(s, user.id, fid)
        await reply(update, f"✅ Follow-up #{fid} done.", edit=False)
    except (ValueError, LookupError) as exc:
        await reply(update, f"❌ {esc(exc)}\nUsage: /followup_done &lt;id&gt;", edit=False)


# ---- invoice --------------------------------------------------------------------------------------
async def _invoice_fin(update, context, user, d):
    import io
    tz = ZoneInfo(user.timezone)
    with session_scope() as s:
        p = fl.get_project(s, user.id, d["project"])
        text = fl.invoice_preview(s, p, tz)
        pdf = fl.invoice_pdf(s, p, tz, issuer=user.first_name or "Uday")
        name = fl.invoice_number(p)
    await update.effective_message.reply_document(io.BytesIO(pdf), filename=f"{name}.pdf", caption=f"Invoice {name}")
    return f"🧾 <b>Invoice preview</b>\n<pre>{esc(text)}</pre>\nPDF attached above."


INVOICE_FORM = Form(name="invoice", commands=["invoice"], title="Invoice", finish=_invoice_fin,
                    fields=[Field("project", "Project ID?", _project_for_payment)])


def get_handlers(timeout: int | None = 900) -> list:
    return [*[build_form_handler(f, timeout=timeout) for f in (CLIENT_FORM, PROJECT_FORM, PAYMENT_FORM, FOLLOWUP_FORM, INVOICE_FORM)],
            CommandHandler("clients", clients), CommandHandler("freelance", freelance), CommandHandler("reverse_payment", reverse_payment),
            CommandHandler("payments", payments), CommandHandler("followup_done", followup_done)]
