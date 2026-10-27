"""Timezone helpers and natural-language date parsing with clarification."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import dateparser

_MONTHS = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
_DATE_PATTERNS = [
    r"\d{4}-\d{1,2}-\d{1,2}", r"\d{1,2}/\d{1,2}(/\d{2,4})?", r"\d{1,2}-\d{1,2}-\d{2,4}",
    rf"\d{{1,2}}(st|nd|rd|th)?\s+{_MONTHS}(\s+\d{{4}})?", rf"{_MONTHS}\s+\d{{1,2}}(st|nd|rd|th)?(,?\s+\d{{4}})?",
]
_PART_OF_DAY = {"morning": "9:00 am", "afternoon": "2:00 pm", "evening": "6:00 pm", "tonight": "8:00 pm",
                "noon": "12:00 pm", "midnight": "12:00 am"}
_RELATIVE = re.compile(r"\bin\s+(an?|\d+)\s*(min|minute|hour|hr|h|day|week)s?\b|\b(an?|\d+)\s*(min|minute|hour|hr)s?\s+from now\b")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_utc_naive(aware: datetime) -> datetime:
    return aware.astimezone(timezone.utc).replace(tzinfo=None)


def from_utc(naive: datetime, tz: ZoneInfo) -> datetime:
    return naive.replace(tzinfo=timezone.utc).astimezone(tz)


def fmt_dt(naive: datetime | None, tz: ZoneInfo) -> str:
    if naive is None:
        return "—"
    return from_utc(naive, tz).strftime("%a %d %b %Y, %I:%M %p")


def fmt_date(naive: datetime | None, tz: ZoneInfo) -> str:
    return "—" if naive is None else from_utc(naive, tz).strftime("%d %b %Y")


def local_day_bounds(tz: ZoneInfo, now_utc: datetime | None = None) -> tuple[datetime, datetime]:
    """UTC-naive [start, end) of the current local day."""
    now_local = from_utc(now_utc or utcnow(), tz)
    start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    from datetime import timedelta
    return to_utc_naive(start), to_utc_naive(start + timedelta(days=1))


@dataclass
class ParsedWhen:
    dt: datetime | None = None          # naive UTC
    clarify: str | None = None          # question to ask the user
    error: str | None = None            # unrecoverable problem


def _has_explicit_time(text: str) -> bool:
    return bool(re.search(r"\b\d{1,2}[:.]\d{2}\b|\b\d{1,2}\s*(am|pm)\b|\bat\s+\d{1,2}\b|noon|midnight", text)) \
        or bool(_RELATIVE.search(text)) or any(k in text for k in ("morning", "afternoon", "evening", "tonight"))


def _ambiguous_hour(text: str) -> bool:
    """True for things like 'at 5' / '5:30' with no am/pm and no 24h marker."""
    stripped = text
    for pat in _DATE_PATTERNS:
        stripped = re.sub(pat, " ", stripped)
    if re.search(r"\b(am|pm)\b|\d\s*(am|pm)", stripped) or _RELATIVE.search(stripped):
        return False
    for m in re.finditer(r"\bat\s+(\d{1,2})(?::(\d{2}))?\b|\b(\d{1,2}):(\d{2})\b", stripped):
        raw = m.group(1) or m.group(3)
        hour = int(raw)
        if hour == 0 or hour >= 13 or raw.startswith("0"):
            continue
        return True
    return False


def parse_when(text: str, tz: ZoneInfo, *, require_time: bool = True, now_utc: datetime | None = None) -> ParsedWhen:
    raw = (text or "").strip().lower()
    if not raw:
        return ParsedWhen(error="Please tell me when (e.g. 'tomorrow 5pm', 'in 2 hours', '30 oct 18:00').")
    for word, repl in _PART_OF_DAY.items():
        if word in raw and not re.search(r"\d\s*(am|pm)|\d:\d{2}", raw):
            raw = raw.replace(word, repl if word not in ("morning", "afternoon", "evening", "tonight") else repl)
            if word == "tonight":
                raw = raw.replace("8:00 pm", "today 8:00 pm") if "today" not in raw else raw
    now_utc = now_utc or utcnow()
    if _ambiguous_hour(raw):
        return ParsedWhen(clarify="That time is ambiguous. Please add am/pm or use 24-hour time (e.g. '5pm' or '17:00').")
    if require_time and not _has_explicit_time(raw):
        return ParsedWhen(clarify="What time? Please include a time (e.g. 'tomorrow 5pm').")
    now_local = from_utc(now_utc, tz)
    parsed = dateparser.parse(
        raw,
        settings={"TIMEZONE": str(tz), "RETURN_AS_TIMEZONE_AWARE": True, "PREFER_DATES_FROM": "future",
                  "RELATIVE_BASE": now_local, "DATE_ORDER": "DMY"},
    )
    if parsed is None:
        return ParsedWhen(error="I couldn't understand that date. Try 'tomorrow 5pm', 'in 2 hours' or '30 oct 2026 18:00'.")
    if not _has_explicit_time(raw):
        parsed = parsed.replace(hour=9, minute=0, second=0, microsecond=0)  # date-only → 9 AM local
    parsed = parsed.replace(second=0, microsecond=0) if not _RELATIVE.search(raw) else parsed.replace(microsecond=0)
    result = to_utc_naive(parsed)
    if result <= now_utc:
        return ParsedWhen(error="That time is in the past. Please give a future date/time.")
    return ParsedWhen(dt=result)
