"""Input validation helpers. All raise ValueError with a user-friendly message."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


def clean(text: str, *, max_len: int, min_len: int = 1, field: str = "Text") -> str:
    text = _CTRL.sub("", (text or "")).strip()
    if len(text) < min_len:
        raise ValueError(f"{field} cannot be empty.")
    if len(text) > max_len:
        raise ValueError(f"{field} is too long (max {max_len} characters).")
    return text


def name_field(text: str, field: str = "Name", max_len: int = 80) -> str:
    return clean(text, max_len=max_len, field=field)


def priority(text: str) -> str:
    t = (text or "").strip().lower()
    aliases = {"h": "high", "m": "medium", "med": "medium", "l": "low", "1": "high", "2": "medium", "3": "low"}
    t = aliases.get(t, t)
    if t not in {"low", "medium", "high"}:
        raise ValueError("Priority must be low, medium or high.")
    return t


def currency(text: str) -> str:
    t = (text or "").strip().upper()
    if not _CURRENCY.match(t):
        raise ValueError("Currency must be a 3-letter code like INR, USD or EUR.")
    return t


def amount_minor(text: str, *, allow_zero: bool = False) -> int:
    """Parse '1500', '1,500.50', '₹2000' into minor units (paise/cents) using Decimal."""
    t = re.sub(r"[₹$€£,\s]|inr|usd|eur|rs\.?", "", (text or "").lower())
    try:
        d = Decimal(t)
    except InvalidOperation:
        raise ValueError("Please enter a valid amount, e.g. 1500 or 1500.50.") from None
    if not d.is_finite() or d < 0 or (d == 0 and not allow_zero):
        raise ValueError("Amount must be greater than zero.")
    if d > Decimal("999999999"):
        raise ValueError("Amount is too large.")
    return int((d * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def timezone_name(text: str) -> str:
    t = (text or "").strip()
    try:
        ZoneInfo(t)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ValueError("Unknown timezone. Use an IANA name like Asia/Kolkata or Europe/London.") from None
    return t


def hhmm(text: str) -> str:
    t = (text or "").strip()
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
        raise ValueError("Use 24-hour HH:MM, e.g. 07:00.")
    return t


def url(text: str) -> str:
    t = (text or "").strip()
    if not t or t.lower() in {"skip", "-"}:
        return ""
    if not re.fullmatch(r"https?://[^\s<>\"']{3,280}", t):
        raise ValueError("Please send a valid http(s) URL, or 'skip'.")
    return t


def positive_int(text: str, field: str = "ID") -> int:
    t = (text or "").strip().lstrip("#")
    if not t.isdigit() or int(t) <= 0 or int(t) > 2**53:
        raise ValueError(f"{field} must be a positive number.")
    return int(t)


def telegram_id(text: str) -> int | None:
    t = (text or "").strip()
    if t.lower() in {"skip", "-", ""}:
        return None
    if not t.isdigit():
        raise ValueError("Telegram ID must be numeric (or 'skip').")
    return int(t)


def csv_safe(value: object) -> str:
    """Neutralise spreadsheet formula injection in exported CSV cells."""
    s = "" if value is None else str(value)
    if s and s[0] in "=+-@\t\r":
        return "'" + s
    return s
