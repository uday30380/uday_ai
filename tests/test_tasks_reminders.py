from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.database import session as sess
from app.database.models import Reminder, ReminderAttempt, Task
from app.services import reminder_service as rs, task_service as ts
from app.utils.dates import from_utc, parse_when, to_utc_naive, utcnow

IST = ZoneInfo("Asia/Kolkata")


def test_task_create_complete_and_isolation(db, owner, other):
    with db.session_scope() as s:
        t = ts.create_task(s, owner.id, "Write report", priority="high")
        tid = t.id
    with db.session_scope() as s:
        assert [x.title for x in ts.list_tasks(s, owner.id)] == ["Write report"]
        assert ts.list_tasks(s, other.id) == []
        with pytest.raises(LookupError):
            ts.complete_task(s, other.id, tid)       # other user cannot touch it
        with pytest.raises(LookupError):
            ts.delete_task(s, other.id, tid)
        ts.complete_task(s, owner.id, tid)
    with db.session_scope() as s:
        assert s.get(Task, tid).status == "done" and ts.list_tasks(s, owner.id) == []


def test_task_validation(db, owner):
    with db.session_scope() as s:
        with pytest.raises(ValueError):
            ts.create_task(s, owner.id, "   ")
        with pytest.raises(ValueError):
            ts.create_task(s, owner.id, "x", priority="urgent")


def test_task_due_creates_reminder_and_completion_cancels_it(db, owner):
    due = utcnow() + timedelta(hours=2)
    with db.session_scope() as s:
        t = ts.create_task(s, owner.id, "Submit", due_at=due)
        tid = t.id
        assert len(rs.list_reminders(s, owner.id)) == 1
    with db.session_scope() as s:
        ts.complete_task(s, owner.id, tid)
        assert rs.list_reminders(s, owner.id) == []


def test_edit_task_due_replaces_reminder(db, owner):
    with db.session_scope() as s:
        t = ts.create_task(s, owner.id, "A", due_at=utcnow() + timedelta(hours=1))
        ts.edit_task(s, owner.id, t.id, "due", "in 3 hours", IST)
        s.flush()
        assert len(rs.list_reminders(s, owner.id)) == 1
        with pytest.raises(ValueError):
            ts.edit_task(s, owner.id, t.id, "color", "red", IST)


def test_duplicate_reminder_not_created(db, owner):
    when = utcnow() + timedelta(hours=1)
    with db.session_scope() as s:
        a = rs.create_reminder(s, owner.id, "Call mom", when)
        b = rs.create_reminder(s, owner.id, "Call mom", when)
        assert a.id == b.id and len(rs.list_reminders(s, owner.id)) == 1


async def test_reminder_marked_sent_only_after_successful_send(db, owner):
    with db.session_scope() as s:
        r = rs.create_reminder(s, owner.id, "Ping", utcnow() - timedelta(seconds=5))
        rid = r.id
    seen = []

    async def ok(tid, text):
        with db.session_scope() as s:   # at send time the reminder must still be pending
            seen.append(s.get(Reminder, rid).status)
    stats = await rs.dispatch_due(db.session_scope, ok)
    assert stats["sent"] == 1 and seen == ["pending"]
    with db.session_scope() as s:
        r = s.get(Reminder, rid)
        assert r.status == "sent" and r.sent_at and r.attempts == 1
        assert s.scalar(select(ReminderAttempt.success).where(ReminderAttempt.reminder_id == rid)) is True
    assert (await rs.dispatch_due(db.session_scope, ok))["sent"] == 0      # never delivered twice


async def test_failed_delivery_is_retried_not_lost(db, owner):
    with db.session_scope() as s:
        rid = rs.create_reminder(s, owner.id, "Ping", utcnow() - timedelta(seconds=5)).id

    async def boom(tid, text):
        raise RuntimeError("Telegram down")
    stats = await rs.dispatch_due(db.session_scope, boom)
    assert stats["retry"] == 1
    with db.session_scope() as s:
        r = s.get(Reminder, rid)
        assert r.status == "pending" and r.attempts == 1 and "Telegram down" in r.last_error
        assert r.next_attempt_at > utcnow()          # backoff
        r.next_attempt_at = utcnow() - timedelta(seconds=1)

    async def ok(tid, text): pass
    assert (await rs.dispatch_due(db.session_scope, ok))["sent"] == 1
    with db.session_scope() as s:
        assert len(s.scalars(select(ReminderAttempt).where(ReminderAttempt.reminder_id == rid)).all()) == 2


async def test_gives_up_after_max_attempts_and_on_permanent_error(db, owner):
    with db.session_scope() as s:
        rid = rs.create_reminder(s, owner.id, "A", utcnow() - timedelta(seconds=5)).id
        rid2 = rs.create_reminder(s, owner.id, "B", utcnow() - timedelta(seconds=5)).id

    async def blocked(tid, text):
        if "B" in text:
            raise rs.PermanentDeliveryError("blocked")
        raise RuntimeError("x")
    for i in range(rs.MAX_ATTEMPTS):
        await rs.dispatch_due(db.session_scope, blocked, now=utcnow() + timedelta(days=i + 1))
    with db.session_scope() as s:
        assert s.get(Reminder, rid).status == "failed" and s.get(Reminder, rid2).status == "failed"
        assert s.get(Reminder, rid2).attempts == 1


async def test_pending_reminders_survive_restart_and_are_delivered_late(db, owner, settings):
    with db.session_scope() as s:
        rs.create_reminder(s, owner.id, "Missed while offline", utcnow() - timedelta(hours=3))
    db.get_engine().dispose()
    db.init_db(settings.database_url)             # simulate process restart
    sent = []

    async def ok(tid, text): sent.append((tid, text))
    await rs.dispatch_due(db.session_scope, ok)
    assert sent and sent[0][0] == 111 and "delivered late" in sent[0][1]


def test_cancel_and_reschedule(db, owner, other):
    with db.session_scope() as s:
        r = rs.create_reminder(s, owner.id, "X", utcnow() + timedelta(hours=1))
        rid = r.id
        with pytest.raises(LookupError):
            rs.cancel_reminder(s, other.id, rid)
        new = utcnow() + timedelta(days=1)
        rs.reschedule_reminder(s, owner.id, rid, new)
        assert s.get(Reminder, rid).remind_at == new
        rs.cancel_reminder(s, owner.id, rid)
        with pytest.raises(ValueError):
            rs.cancel_reminder(s, owner.id, rid)


def test_timezone_conversion_roundtrip():
    res = parse_when("tomorrow 5pm", IST)
    assert res.dt is not None
    local = from_utc(res.dt, IST)
    assert (local.hour, local.minute) == (17, 0)
    assert to_utc_naive(local) == res.dt
    assert res.dt.hour == 11 and res.dt.minute == 30       # 17:00 IST = 11:30 UTC


@pytest.mark.parametrize("text,key", [("at 5", "clarify"), ("tomorrow", "clarify"), ("yesterday 5pm", "error"), ("", "error")])
def test_ambiguous_or_invalid_dates_ask_for_clarification(text, key):
    assert getattr(parse_when(text, IST), key)


def test_today_and_overdue_views(db, owner):
    with db.session_scope() as s:
        ts.create_task(s, owner.id, "Late")
        s.flush()
        t = s.scalar(select(Task))
        t.due_at = utcnow() - timedelta(days=2)
        due, overdue = ts.today_tasks(s, owner.id, IST)
        assert [x.title for x in overdue] == ["Late"] and due == []
