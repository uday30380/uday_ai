"""Clubs with role-based permissions (owner > admin > member). Every function takes the acting User."""
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Announcement, Club, ClubEvent, ClubMember, ClubTask, User, utcnow
from app.utils import validation as v
from app.utils.security import audit

RANK = {"member": 1, "admin": 2, "owner": 3}


class PermissionDenied(Exception):
    pass


def _membership(session: Session, club_id: int, user: User) -> ClubMember | None:
    return session.scalar(select(ClubMember).where(ClubMember.club_id == club_id, ClubMember.telegram_id == user.telegram_id))


def require(session: Session, club_id: int, user: User, min_role: str = "member") -> ClubMember:
    m = _membership(session, club_id, user)
    if m is None or RANK[m.role] < RANK[min_role]:
        # same message for "no such club" and "not allowed": do not leak existence of other clubs
        raise PermissionDenied("You don't have access to that club or action.")
    return m


def create_club(session: Session, user: User, name: str, description: str = "") -> Club:
    name = v.clean(name, max_len=100, field="Club name")
    if session.scalar(select(Club.id).where(Club.name.ilike(name))):
        raise ValueError("A club with that name already exists.")
    club = Club(name=name, description=v.clean(description, max_len=1000, min_len=0), created_by=user.id)
    session.add(club)
    session.flush()
    session.add(ClubMember(club_id=club.id, name=user.first_name or user.username or "Owner", role="owner", telegram_id=user.telegram_id))
    audit(session, user.id, "club.create", name)
    return club


def my_clubs(session: Session, user: User) -> list[tuple[Club, str]]:
    rows = session.execute(select(Club, ClubMember.role).join(ClubMember, ClubMember.club_id == Club.id)
                           .where(ClubMember.telegram_id == user.telegram_id).order_by(Club.name)).all()
    return [(c, r) for c, r in rows]


def resolve_club(session: Session, user: User, text: str) -> Club:
    t = text.strip().lstrip("#")
    for club, _ in my_clubs(session, user):
        if (t.isdigit() and club.id == int(t)) or club.name.lower() == t.lower():
            return club
    raise PermissionDenied("You don't have access to a club with that name/ID.")


def add_member(session: Session, club_id: int, actor: User, name: str, role: str = "member",
               telegram_id: int | None = None, contact: str = "") -> ClubMember:
    me = require(session, club_id, actor, "admin")
    role = role.strip().lower()
    if role not in RANK or role == "owner":
        raise ValueError("Role must be member or admin.")
    if role == "admin" and me.role != "owner":
        raise PermissionDenied("Only the club owner can create administrators.")
    if telegram_id and session.scalar(select(ClubMember.id).where(ClubMember.club_id == club_id, ClubMember.telegram_id == telegram_id)):
        raise ValueError("That Telegram user is already a member.")
    m = ClubMember(club_id=club_id, name=v.clean(name, max_len=100, field="Name"), role=role, telegram_id=telegram_id,
                   contact=v.clean(contact, max_len=160, min_len=0))
    session.add(m)
    session.flush()
    audit(session, actor.id, "club.member.add", f"club={club_id} member={m.id} role={role}")
    return m


def remove_member(session: Session, club_id: int, actor: User, member_id: int) -> None:
    me = require(session, club_id, actor, "admin")
    m = session.scalar(select(ClubMember).where(ClubMember.id == member_id, ClubMember.club_id == club_id))
    if m is None:
        raise LookupError("Member not found in this club.")
    if m.role == "owner":
        raise PermissionDenied("The club owner cannot be removed.")
    if m.role == "admin" and me.role != "owner":
        raise PermissionDenied("Only the owner can remove administrators.")
    session.delete(m)
    audit(session, actor.id, "club.member.remove", f"club={club_id} member={member_id}")


def list_members(session: Session, club_id: int, actor: User) -> list[dict]:
    me = require(session, club_id, actor, "member")
    show_contact = RANK[me.role] >= RANK["admin"]
    members = session.scalars(select(ClubMember).where(ClubMember.club_id == club_id).order_by(ClubMember.id))
    return [{"id": m.id, "name": m.name, "role": m.role, "telegram_id": m.telegram_id if show_contact else None,
             "contact": m.contact if show_contact else ""} for m in members]


def find_member(session: Session, club_id: int, text: str) -> ClubMember | None:
    t = text.strip().lstrip("#")
    q = select(ClubMember).where(ClubMember.club_id == club_id)
    return session.scalar(q.where(ClubMember.id == int(t))) if t.isdigit() else session.scalar(q.where(ClubMember.name.ilike(t)))


def create_event(session: Session, club_id: int, actor: User, title: str, kind: str, starts_at: datetime,
                 location: str = "", description: str = "") -> ClubEvent:
    require(session, club_id, actor, "admin")
    kind = kind.strip().lower()
    if kind not in {"event", "workshop", "meeting"}:
        raise ValueError("Type must be event, workshop or meeting.")
    e = ClubEvent(club_id=club_id, title=v.clean(title, max_len=160, field="Title"), kind=kind, starts_at=starts_at,
                  location=v.clean(location, max_len=160, min_len=0), description=v.clean(description, max_len=1000, min_len=0),
                  created_by=actor.id)
    session.add(e)
    session.flush()
    audit(session, actor.id, "club.event.create", f"club={club_id} id={e.id}")
    return e


def list_events(session: Session, club_id: int, actor: User, upcoming_only: bool = True) -> list[ClubEvent]:
    require(session, club_id, actor, "member")
    q = select(ClubEvent).where(ClubEvent.club_id == club_id)
    if upcoming_only:
        q = q.where(ClubEvent.starts_at >= utcnow())
    return list(session.scalars(q.order_by(ClubEvent.starts_at)))


def create_task(session: Session, club_id: int, actor: User, title: str, assignee: ClubMember | None,
                deadline: datetime | None) -> ClubTask:
    require(session, club_id, actor, "admin")
    if assignee is not None and assignee.club_id != club_id:
        raise PermissionDenied("Assignee is not in this club.")
    t = ClubTask(club_id=club_id, title=v.clean(title, max_len=200, field="Title"), assignee_id=assignee.id if assignee else None,
                 deadline=deadline, created_by=actor.id)
    session.add(t)
    session.flush()
    audit(session, actor.id, "club.task.create", f"club={club_id} id={t.id}")
    return t


def list_tasks(session: Session, club_id: int, actor: User, include_done: bool = False) -> list[ClubTask]:
    me = require(session, club_id, actor, "member")
    q = select(ClubTask).where(ClubTask.club_id == club_id)
    if not include_done:
        q = q.where(ClubTask.status == "open")
    if RANK[me.role] < RANK["admin"]:  # plain members only see tasks assigned to them
        q = q.where(ClubTask.assignee_id == me.id)
    return list(session.scalars(q.order_by(ClubTask.deadline.is_(None), ClubTask.deadline, ClubTask.id)))


def complete_task(session: Session, actor: User, task_id: int) -> ClubTask:
    t = session.get(ClubTask, task_id)
    if t is None:
        raise PermissionDenied("You don't have access to that club or action.")
    me = require(session, t.club_id, actor, "member")
    if RANK[me.role] < RANK["admin"] and t.assignee_id != me.id:
        raise PermissionDenied("You don't have access to that club or action.")
    t.status = "done"
    return t


def draft_announcement(session: Session, club_id: int, actor: User, text: str) -> Announcement:
    require(session, club_id, actor, "admin")
    a = Announcement(club_id=club_id, text=v.clean(text, max_len=3000, field="Announcement"), created_by=actor.id)
    session.add(a)
    session.flush()
    audit(session, actor.id, "club.announce.draft", f"club={club_id} id={a.id}")
    return a


SendFunc = Callable[[int, str], Awaitable[None]]


async def send_announcement(session_scope, actor: User, ann_id: int, send: SendFunc) -> Announcement:
    """Send a confirmed draft to members with a Telegram ID. Each announcement can only be sent once."""
    with session_scope() as s:
        a = s.get(Announcement, ann_id)
        if a is None:
            raise PermissionDenied("You don't have access to that club or action.")
        require(s, a.club_id, actor, "admin")
        if a.status != "draft":
            raise ValueError("This announcement was already sent.")
        a.status, a.sent_at = "sending", utcnow()  # claim it so a double-tap cannot send twice
        club_name = s.get(Club, a.club_id).name
        text = f"📢 {club_name}\n\n{a.text}"
        targets = [m.telegram_id for m in s.scalars(select(ClubMember).where(ClubMember.club_id == a.club_id, ClubMember.telegram_id.is_not(None)))]
    ok = bad = 0
    for tid in targets:
        try:
            await send(tid, text)
            ok += 1
        except Exception:  # noqa: BLE001
            bad += 1
    with session_scope() as s:
        a = s.get(Announcement, ann_id)
        a.status, a.delivered, a.failed = "sent", ok, bad
        audit(s, actor.id, "club.announce.send", f"club={a.club_id} id={a.id} ok={ok} failed={bad}")
        s.flush()
        s.expunge(a)
    return a


def export_csv(session: Session, club_id: int, actor: User, what: str) -> tuple[str, bytes]:
    require(session, club_id, actor, "admin")
    out = io.StringIO()
    w = csv.writer(out)
    if what == "members":
        w.writerow(["id", "name", "role", "telegram_id", "contact"])
        for m in session.scalars(select(ClubMember).where(ClubMember.club_id == club_id)):
            w.writerow([m.id, v.csv_safe(m.name), m.role, m.telegram_id or "", v.csv_safe(m.contact)])
    elif what == "events":
        w.writerow(["id", "title", "type", "starts_at_utc", "location"])
        for e in session.scalars(select(ClubEvent).where(ClubEvent.club_id == club_id)):
            w.writerow([e.id, v.csv_safe(e.title), e.kind, e.starts_at.isoformat(), v.csv_safe(e.location)])
    elif what == "tasks":
        w.writerow(["id", "title", "assignee_id", "deadline_utc", "status"])
        for t in session.scalars(select(ClubTask).where(ClubTask.club_id == club_id)):
            w.writerow([t.id, v.csv_safe(t.title), t.assignee_id or "", t.deadline.isoformat() if t.deadline else "", t.status])
    else:
        raise ValueError("Export what? members, events or tasks.")
    audit(session, actor.id, "club.export", f"club={club_id} what={what}")
    return f"club{club_id}_{what}.csv", out.getvalue().encode("utf-8-sig")
