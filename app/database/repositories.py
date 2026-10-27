"""Small data-access helpers shared by services (all queries are parameterised ORM queries)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database.models import User
from app.utils.security import is_owner


def get_or_create_user(session: Session, settings: Settings, telegram_id: int,
                       username: str | None = None, first_name: str | None = None) -> User:
    user = session.scalar(select(User).where(User.telegram_id == telegram_id))
    owner = is_owner(settings, telegram_id, username)
    if user is None:
        user = User(telegram_id=telegram_id, username=username, first_name=first_name, is_owner=owner,
                    timezone=settings.app_timezone, briefing_enabled=settings.enable_daily_briefing and owner,
                    briefing_time=settings.daily_briefing_time)
        session.add(user)
        session.flush()
    else:
        user.username, user.first_name, user.is_owner = username, first_name, owner
    return user


def get_user(session: Session, telegram_id: int) -> User | None:
    return session.scalar(select(User).where(User.telegram_id == telegram_id))
