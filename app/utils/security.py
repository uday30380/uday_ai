"""Authorization, rate limiting, audit logging and log redaction."""
from __future__ import annotations

import logging
import re
import time
from collections import defaultdict, deque

from sqlalchemy.orm import Session

from app.config import Settings
from app.database.models import AuditLog


def is_owner(settings: Settings, telegram_id: int, username: str | None = None) -> bool:
    if telegram_id in settings.admin_id_list:
        return True
    if username and hasattr(settings, "admin_username_list"):
        return username.lstrip("@").lower() in settings.admin_username_list
    return False


def is_authorized(settings: Settings, telegram_id: int, username: str | None = None) -> bool:
    if is_owner(settings, telegram_id, username):
        return True
    if telegram_id in settings.allowed_id_list:
        return True
    if username and hasattr(settings, "allowed_username_list"):
        return username.lstrip("@").lower() in settings.allowed_username_list
    return False


class RateLimiter:
    """Sliding-window per-user limiter (in memory)."""

    def __init__(self, per_minute: int, clock=time.monotonic):
        self.per_minute = per_minute
        self._hits: dict[int, deque[float]] = defaultdict(deque)
        self._clock = clock

    def allow(self, key: int) -> bool:
        now = self._clock()
        q = self._hits[key]
        while q and now - q[0] >= 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return False
        q.append(now)
        return True


def audit(session: Session, user_id: int | None, action: str, detail: str = "") -> None:
    session.add(AuditLog(user_id=user_id, action=action[:60], detail=detail[:500]))


_PATTERNS = [
    (re.compile(r"\d{5,}:[A-Za-z0-9_-]{30,}"), "<redacted-bot-token>"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._-]{10,}"), r"\1<redacted>"),
    (re.compile(r"(?i)(api[_-]?key['\"=: ]+)[A-Za-z0-9._-]{8,}"), r"\1<redacted>"),
    (re.compile(r"/bot\d+:[^/\s]+"), "/bot<redacted>"),
]


def redact(text: str) -> str:
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
        except Exception:  # pragma: no cover
            pass
        return True


def setup_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs full URLs incl. bot token
    logging.getLogger("httpcore").setLevel(logging.WARNING)
