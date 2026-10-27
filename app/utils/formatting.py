"""Safe HTML formatting helpers (Telegram parse_mode=HTML) and money formatting."""
from __future__ import annotations

import html
from decimal import Decimal

TG_LIMIT = 4000


def esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


def code_block(text: str, lang: str = "") -> str:
    cls = f' class="language-{html.escape(lang, quote=True)}"' if lang and lang.replace("+", "").replace("#", "").isalnum() else ""
    return f"<pre><code{cls}>{esc(text)}</code></pre>"


def chunks(text: str, size: int = TG_LIMIT) -> list[str]:
    """Split long text on line boundaries (plain text only; never split inside HTML)."""
    out, cur = [], ""
    for line in text.split("\n"):
        while len(line) > size:
            if cur:
                out.append(cur); cur = ""
            out.append(line[:size]); line = line[size:]
        if len(cur) + len(line) + 1 > size:
            out.append(cur); cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out or [""]


_SYMBOLS = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}


def money(minor: int, currency: str = "INR") -> str:
    sign = "-" if minor < 0 else ""
    amount = Decimal(abs(minor)) / Decimal(100)
    return f"{sign}{_SYMBOLS.get(currency, currency + ' ')}{amount:,.2f}"


PRIORITY_ICON = {"high": "🔴", "medium": "🟡", "low": "🟢"}
