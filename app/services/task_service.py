"""Tasks: CRUD, today/upcoming views and the daily plan. Every query is scoped to user_id."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.database.models import Reminder, Task, utcnow
from app.services import reminder_service
from app.utils import validation as v
from app.utils.dates import local_day_bounds
from app.utils.security import audit

_PRIO = case({"high": 0, "medium": 1, "low": 2}, value=Task.priority, else_=3)


def create_task(session: Session, user_id: int, title: str, *, description: str = "", priority: str = "medium",
                due_at: datetime | None = None, remind: bool = True) -> Task:
    task = Task(user_id=user_id, title=v.clean(title, max_len=200, field="Title"),
                description=v.clean(description, max_len=2000, min_len=0, field="Description") if description else "",
                priority=v.priority(priority), due_at=due_at)
    session.add(task)
    session.flush()
    if due_at and remind:
        reminder_service.create_reminder(session, user_id, f"Task due: {task.title}", due_at, task_id=task.id)
    audit(session, user_id, "task.create", f"id={task.id}")
    return task


def get_task(session: Session, user_id: int, task_id: int) -> Task | None:
    return session.scalar(select(Task).where(Task.id == task_id, Task.user_id == user_id))


def list_tasks(session: Session, user_id: int, status: str | None = "pending") -> list[Task]:
    q = select(Task).where(Task.user_id == user_id)
    if status:
        q = q.where(Task.status == status)
    return list(session.scalars(q.order_by(Task.due_at.is_(None), Task.due_at, _PRIO, Task.id)))


def complete_task(session: Session, user_id: int, task_id: int) -> Task:
    task = get_task(session, user_id, task_id)
    if task is None:
        raise LookupError(f"Task #{task_id} not found.")
    if task.status == "done":
        return task
    task.status, task.completed_at = "done", utcnow()
    for r in session.scalars(select(Reminder).where(Reminder.task_id == task.id, Reminder.status == "pending")):
        r.status = "cancelled"
    audit(session, user_id, "task.done", f"id={task.id}")
    return task


def delete_task(session: Session, user_id: int, task_id: int) -> None:
    task = get_task(session, user_id, task_id)
    if task is None:
        raise LookupError(f"Task #{task_id} not found.")
    session.delete(task)
    audit(session, user_id, "task.delete", f"id={task_id}")


def edit_task(session: Session, user_id: int, task_id: int, field: str, value: str,
              tz: ZoneInfo) -> Task:
    from app.utils.dates import parse_when
    task = get_task(session, user_id, task_id)
    if task is None:
        raise LookupError(f"Task #{task_id} not found.")
    field = field.lower()
    if field == "title":
        task.title = v.clean(value, max_len=200, field="Title")
    elif field in {"description", "desc"}:
        task.description = v.clean(value, max_len=2000, min_len=0)
    elif field == "priority":
        task.priority = v.priority(value)
    elif field in {"due", "due_at", "date"}:
        if value.strip().lower() in {"none", "clear", "-"}:
            task.due_at = None
        else:
            res = parse_when(value, tz, require_time=False)
            if res.dt is None:
                raise ValueError(res.clarify or res.error)
            task.due_at = res.dt
        for r in session.scalars(select(Reminder).where(Reminder.task_id == task.id, Reminder.status == "pending")):
            r.status = "cancelled"
        if task.due_at:
            reminder_service.create_reminder(session, user_id, f"Task due: {task.title}", task.due_at, task_id=task.id)
    else:
        raise ValueError("Editable fields: title, description, priority, due.")
    audit(session, user_id, "task.edit", f"id={task.id} field={field}")
    return task


def today_tasks(session: Session, user_id: int, tz: ZoneInfo) -> tuple[list[Task], list[Task]]:
    """(due today, overdue) pending tasks."""
    start, end = local_day_bounds(tz)
    due = list(session.scalars(select(Task).where(Task.user_id == user_id, Task.status == "pending",
                                                  Task.due_at >= start, Task.due_at < end).order_by(Task.due_at)))
    overdue = list(session.scalars(select(Task).where(Task.user_id == user_id, Task.status == "pending",
                                                      Task.due_at < start).order_by(Task.due_at)))
    return due, overdue


def upcoming_tasks(session: Session, user_id: int, tz: ZoneInfo, days: int = 7) -> list[Task]:
    from datetime import timedelta
    _, end_today = local_day_bounds(tz)
    return list(session.scalars(select(Task).where(
        Task.user_id == user_id, Task.status == "pending", Task.due_at >= end_today,
        Task.due_at < end_today + timedelta(days=days)).order_by(Task.due_at)))


def daily_plan(session: Session, user_id: int, tz: ZoneInfo) -> dict:
    due, overdue = today_tasks(session, user_id, tz)
    no_date = list(session.scalars(select(Task).where(Task.user_id == user_id, Task.status == "pending",
                                                      Task.due_at.is_(None)).order_by(_PRIO, Task.id).limit(5)))
    return {"overdue": overdue, "today": due, "unscheduled": no_date,
            "upcoming": upcoming_tasks(session, user_id, tz, 3)}
