"""Clients, freelance projects, append-only payment ledger, follow-ups and invoices."""
from __future__ import annotations

import io
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.models import Client, FollowUp, FreelanceProject, Payment, utcnow
from app.services import reminder_service
from app.utils import validation as v
from app.utils.dates import fmt_date
from app.utils.formatting import money
from app.utils.security import audit


# ---- clients ------------------------------------------------------------------------------------
def add_client(session: Session, user_id: int, name: str, email: str = "", phone: str = "", notes: str = "") -> Client:
    name = v.clean(name, max_len=120, field="Client name")
    if get_client(session, user_id, name):
        raise ValueError(f"Client '{name}' already exists.")
    c = Client(user_id=user_id, name=name, email=v.clean(email, max_len=160, min_len=0),
               phone=v.clean(phone, max_len=40, min_len=0), notes=v.clean(notes, max_len=1000, min_len=0))
    session.add(c)
    session.flush()
    audit(session, user_id, "client.add", name)
    return c


def get_client(session: Session, user_id: int, name_or_id: str) -> Client | None:
    t = str(name_or_id).strip().lstrip("#")
    q = select(Client).where(Client.user_id == user_id)
    if t.isdigit():
        return session.scalar(q.where(Client.id == int(t)))
    return session.scalar(q.where(Client.name.ilike(t)))


def list_clients(session: Session, user_id: int) -> list[Client]:
    return list(session.scalars(select(Client).where(Client.user_id == user_id).order_by(Client.name)))


# ---- projects & payments --------------------------------------------------------------------------
def create_project(session: Session, user_id: int, client: Client, title: str, description: str,
                   quoted_minor: int, currency: str = "INR", deadline: datetime | None = None) -> FreelanceProject:
    if client.user_id != user_id:
        raise PermissionError("Not your client.")
    p = FreelanceProject(user_id=user_id, client_id=client.id, title=v.clean(title, max_len=160, field="Title"),
                         description=v.clean(description, max_len=2000, min_len=0), quoted_minor=quoted_minor,
                         currency=v.currency(currency), deadline=deadline)
    session.add(p)
    session.flush()
    audit(session, user_id, "fproject.create", f"id={p.id} amount={quoted_minor}")
    return p


def get_project(session: Session, user_id: int, project_id: int) -> FreelanceProject | None:
    return session.scalar(select(FreelanceProject).where(FreelanceProject.id == project_id, FreelanceProject.user_id == user_id))


def list_projects(session: Session, user_id: int, status: str | None = None) -> list[FreelanceProject]:
    q = select(FreelanceProject).where(FreelanceProject.user_id == user_id)
    if status:
        q = q.where(FreelanceProject.status == status)
    return list(session.scalars(q.order_by(FreelanceProject.id)))


def paid_minor(session: Session, project_id: int) -> int:
    return int(session.scalar(select(func.coalesce(func.sum(Payment.amount_minor), 0)).where(Payment.project_id == project_id)) or 0)


def outstanding_minor(session: Session, p: FreelanceProject) -> int:
    return p.quoted_minor - paid_minor(session, p.id)


def record_payment(session: Session, user_id: int, project_id: int, amount_minor: int, method: str = "", note: str = "") -> Payment:
    p = get_project(session, user_id, project_id)
    if p is None:
        raise LookupError(f"Project #{project_id} not found.")
    if p.status == "cancelled":
        raise ValueError("Project is cancelled.")
    if amount_minor <= 0:
        raise ValueError("Payment must be positive.")
    due = outstanding_minor(session, p)
    if amount_minor > due:
        raise ValueError(f"That is more than the outstanding balance ({money(due, p.currency)}).")
    pay = Payment(user_id=user_id, project_id=p.id, amount_minor=amount_minor, method=v.clean(method, max_len=40, min_len=0),
                  note=v.clean(note, max_len=300, min_len=0))
    session.add(pay)
    session.flush()
    audit(session, user_id, "payment.record", f"id={pay.id} project={p.id} amount={amount_minor}")
    return pay


def reverse_payment(session: Session, user_id: int, payment_id: int, note: str = "") -> Payment:
    """Corrections never edit history: add a negative 'reversal' row."""
    orig = session.scalar(select(Payment).where(Payment.id == payment_id, Payment.user_id == user_id))
    if orig is None:
        raise LookupError(f"Payment #{payment_id} not found.")
    if orig.kind != "payment":
        raise ValueError("Only original payments can be reversed.")
    if session.scalar(select(Payment.id).where(Payment.reverses_id == orig.id)):
        raise ValueError("This payment was already reversed.")
    rev = Payment(user_id=user_id, project_id=orig.project_id, amount_minor=-orig.amount_minor, kind="reversal",
                  reverses_id=orig.id, note=v.clean(note or f"Reversal of payment #{orig.id}", max_len=300))
    session.add(rev)
    session.flush()
    audit(session, user_id, "payment.reverse", f"id={orig.id}")
    return rev


def payment_history(session: Session, user_id: int, project_id: int) -> list[Payment]:
    return list(session.scalars(select(Payment).where(Payment.user_id == user_id, Payment.project_id == project_id).order_by(Payment.id)))


def set_project_status(session: Session, user_id: int, project_id: int, status: str) -> FreelanceProject:
    if status not in {"active", "completed", "cancelled"}:
        raise ValueError("Status must be active, completed or cancelled.")
    p = get_project(session, user_id, project_id)
    if p is None:
        raise LookupError(f"Project #{project_id} not found.")
    p.status = status
    return p


# ---- follow-ups ------------------------------------------------------------------------------------
def add_followup(session: Session, user_id: int, client: Client, due_at: datetime, note: str,
                 project_id: int | None = None) -> FollowUp:
    if client.user_id != user_id:
        raise PermissionError("Not your client.")
    note = v.clean(note, max_len=300, min_len=0)
    f = FollowUp(user_id=user_id, client_id=client.id, project_id=project_id, due_at=due_at, note=note)
    session.add(f)
    session.flush()
    r = reminder_service.create_reminder(session, user_id, f"Follow up with {client.name}" + (f": {note}" if note else ""), due_at)
    f.reminder_id = r.id
    return f


def complete_followup(session: Session, user_id: int, followup_id: int) -> FollowUp:
    f = session.scalar(select(FollowUp).where(FollowUp.id == followup_id, FollowUp.user_id == user_id))
    if f is None:
        raise LookupError(f"Follow-up #{followup_id} not found.")
    f.status = "done"
    return f


def list_followups(session: Session, user_id: int, only_open: bool = True) -> list[FollowUp]:
    q = select(FollowUp).where(FollowUp.user_id == user_id)
    if only_open:
        q = q.where(FollowUp.status == "open")
    return list(session.scalars(q.order_by(FollowUp.due_at)))


def overdue_followups(session: Session, user_id: int, now: datetime | None = None) -> list[FollowUp]:
    return [f for f in list_followups(session, user_id) if f.due_at < (now or utcnow())]


# ---- dashboard -------------------------------------------------------------------------------------
def summary(session: Session, user_id: int, now: datetime | None = None) -> dict:
    """Totals per currency: revenue recorded (net of reversals), outstanding on non-cancelled projects."""
    revenue: dict[str, int] = defaultdict(int)
    outstanding: dict[str, int] = defaultdict(int)
    for p in list_projects(session, user_id):
        paid = paid_minor(session, p.id)
        revenue[p.currency] += paid
        if p.status != "cancelled":
            outstanding[p.currency] += p.quoted_minor - paid
    return {"revenue": dict(revenue), "outstanding": dict(outstanding),
            "active_projects": len(list_projects(session, user_id, "active")),
            "overdue_followups": overdue_followups(session, user_id, now)}


# ---- invoices --------------------------------------------------------------------------------------
def invoice_number(p: FreelanceProject) -> str:
    return f"INV-{p.created_at:%Y%m}-{p.id:04d}"


def invoice_preview(session: Session, p: FreelanceProject, tz: ZoneInfo) -> str:
    paid = paid_minor(session, p.id)
    return (f"Invoice {invoice_number(p)}\nClient: {p.client.name}\nProject: {p.title}\n"
            f"Quoted: {money(p.quoted_minor, p.currency)}\nPaid: {money(paid, p.currency)}\n"
            f"Balance due: {money(p.quoted_minor - paid, p.currency)}\nDeadline: {fmt_date(p.deadline, tz)}")


def invoice_pdf(session: Session, p: FreelanceProject, tz: ZoneInfo, issuer: str = "Uday") -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    paid = paid_minor(session, p.id)
    styles = getSampleStyleSheet()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title=f"Invoice {invoice_number(p)}", author=issuer)
    cur = p.currency
    # PDF core fonts lack the ₹ glyph, so the PDF uses the ISO currency code instead of a symbol.
    fmt = lambda minor: f"{cur} {minor / 100:,.2f}"  # noqa: E731
    rows = [["Description", "Amount"], [Paragraph(escape(p.title), styles["Normal"]), fmt(p.quoted_minor)]]
    pays = [x for x in p.payments]
    for x in pays:
        rows.append([f"{'Payment' if x.kind == 'payment' else 'Reversal'} {x.created_at:%d %b %Y}", fmt(-x.amount_minor)])
    rows.append(["Balance due", fmt(p.quoted_minor - paid)])
    table = Table(rows, colWidths=[330, 120])
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey), ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                               ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold")]))
    story = [Paragraph(f"INVOICE {escape(invoice_number(p))}", styles["Title"]),
             Paragraph(f"From: {escape(issuer)}", styles["Normal"]),
             Paragraph(f"To: {escape(p.client.name)}", styles["Normal"]),
             Paragraph(f"Date: {utcnow():%d %b %Y}", styles["Normal"]),
             Paragraph(f"Due by: {fmt_date(p.deadline, tz)}", styles["Normal"]), Spacer(1, 18), table, Spacer(1, 18),
             Paragraph("Generated by Uday AI. This invoice is a record only; payments are not verified with any bank.",
                       styles["Italic"])]
    doc.build(story)
    return buf.getvalue()
