"""Durable reminders stored in SQLite and delivered by a polling dispatcher.

A reminder is only marked 'sent' after the send callback returns without raising, i.e. after
Telegram confirmed the message. Failures are logged per attempt and retried with backoff.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Reminder, ReminderAttempt, User, utcnow
from app.utils import validation as v

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 5
SendFunc = Callable[[int, str], Awaitable[None]]


class PermanentDeliveryError(Exception):
    """Raised by the send callback when retrying is pointless (e.g. user blocked the bot)."""


def create_reminder(session: Session, user_id: int, message: str, remind_at: datetime, *,
                    task_id: int | None = None) -> Reminder:
    """Create a pending reminder; returns the existing one if an identical pending reminder exists."""
    message = v.clean(message, max_len=500, field="Reminder text")
    existing = session.scalar(select(Reminder).where(
        Reminder.user_id == user_id, Reminder.status == "pending", Reminder.remind_at == remind_at,
        Reminder.message == message, Reminder.task_id.is_(task_id) if task_id is None else Reminder.task_id == task_id))
    if existing:
        return existing
    r = Reminder(user_id=user_id, task_id=task_id, message=message, remind_at=remind_at, next_attempt_at=remind_at)
    session.add(r)
    session.flush()
    return r


def get_reminder(session: Session, user_id: int, reminder_id: int) -> Reminder | None:
    return session.scalar(select(Reminder).where(Reminder.id == reminder_id, Reminder.user_id == user_id))


def list_reminders(session: Session, user_id: int, include_closed: bool = False) -> list[Reminder]:
    q = select(Reminder).where(Reminder.user_id == user_id)
    if not include_closed:
        q = q.where(Reminder.status == "pending")
    return list(session.scalars(q.order_by(Reminder.remind_at)))


def cancel_reminder(session: Session, user_id: int, reminder_id: int) -> Reminder:
    r = get_reminder(session, user_id, reminder_id)
    if r is None:
        raise LookupError(f"Reminder #{reminder_id} not found.")
    if r.status != "pending":
        raise ValueError(f"Reminder #{reminder_id} is already {r.status}.")
    r.status = "cancelled"
    return r


def reschedule_reminder(session: Session, user_id: int, reminder_id: int, new_time: datetime) -> Reminder:
    r = get_reminder(session, user_id, reminder_id)
    if r is None:
        raise LookupError(f"Reminder #{reminder_id} not found.")
    if r.status == "cancelled":
        raise ValueError("That reminder was cancelled. Create a new one instead.")
    r.remind_at = r.next_attempt_at = new_time
    r.status, r.attempts, r.last_error, r.sent_at = "pending", 0, None, None
    return r


def due_reminders(session: Session, now: datetime | None = None, limit: int = 50) -> list[Reminder]:
    now = now or utcnow()
    return list(session.scalars(select(Reminder).where(
        Reminder.status == "pending", Reminder.next_attempt_at <= now).order_by(Reminder.next_attempt_at).limit(limit)))


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(60 * 2 ** (attempts - 1), 3600))


async def dispatch_due(session_scope, send: SendFunc, now: datetime | None = None) -> dict[str, int]:
    """Deliver due reminders. Safe to call repeatedly; also restores missed reminders after downtime."""
    stats = {"sent": 0, "retry": 0, "failed": 0}
    with session_scope() as s:
        work = [(r.id, r.user_id, r.message, r.remind_at) for r in due_reminders(s, now)]
    for rid, user_id, message, remind_at in work:
        with session_scope() as s:
            tg_id = s.scalar(select(User.telegram_id).where(User.id == user_id))
        current = now or utcnow()
        late = (current - remind_at) > timedelta(minutes=2)
        text = f"⏰ Reminder: {message}" + ("\n(delivered late — the bot was offline)" if late else "")
        error: str | None = None
        permanent = False
        try:
            await send(tg_id, text)
        except PermanentDeliveryError as exc:
            error, permanent = str(exc)[:300], True
        except Exception as exc:  # noqa: BLE001 - any send failure must be recorded, not crash the loop
            error = f"{type(exc).__name__}: {exc}"[:300]
        with session_scope() as s:
            r = s.get(Reminder, rid)
            if r is None or r.status != "pending":
                continue  # cancelled/rescheduled while sending
            r.attempts += 1
            s.add(ReminderAttempt(reminder_id=rid, success=error is None, error=error))
            if error is None:
                r.status, r.sent_at, r.last_error = "sent", utcnow(), None
                stats["sent"] += 1
            else:
                r.last_error = error
                if permanent or r.attempts >= MAX_ATTEMPTS:
                    r.status = "failed"; stats["failed"] += 1
                else:
                    r.next_attempt_at = (now or utcnow()) + _backoff(r.attempts); stats["retry"] += 1
                log.warning("Reminder %s delivery failed (attempt %s): %s", rid, r.attempts, error)
    return stats
