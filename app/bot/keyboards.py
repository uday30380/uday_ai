"""Inline keyboards: main menu, sub-menus, pagination and confirmations."""
from __future__ import annotations

from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M

MAIN = [
    [("🤖 AI Assistant", "m:ai"), ("✅ Tasks & Reminders", "m:tasks")],
    [("🛠 Developer Tools", "m:dev"), ("💼 Freelance Manager", "m:free")],
    [("🏫 Club Management", "m:club"), ("📅 Daily Summary", "a:dailyplan")],
    [("⚙️ Settings", "a:settings"), ("❓ Help", "a:help")],
]

SUB: dict[str, tuple[str, list[list[tuple[str, str]]]]] = {
    "ai": ("🤖 <b>AI Assistant</b>", [
        [("💬 Ask", "f:ask"), ("📝 Summarize", "f:summarize")],
        [("✍️ Rewrite", "f:rewrite"), ("🔎 Explain", "f:explain")],
        [("🧹 Clear memory", "a:clear")]]),
    "tasks": ("✅ <b>Tasks &amp; Reminders</b>", [
        [("➕ New task", "f:task"), ("📋 My tasks", "a:tasks")],
        [("⏰ New reminder", "f:remind"), ("🔔 Reminders", "a:reminders")],
        [("📅 Today", "a:today"), ("🔜 Upcoming", "a:upcoming")]]),
    "dev": ("🛠 <b>Developer Tools</b>", [
        [("💾 Save snippet", "f:snippet_new"), ("📚 Snippets", "a:snippets")],
        [("🆕 New project", "f:project_new"), ("📁 Projects", "a:projects")],
        [("🕒 Log work", "f:logwork"), ("🆘 Dev help", "f:devhelp")]]),
    "free": ("💼 <b>Freelance Manager</b>", [
        [("👤 New client", "f:client"), ("👥 Clients", "a:clients")],
        [("📌 New project", "f:newproject"), ("📊 Dashboard", "a:freelance")],
        [("💵 Record payment", "f:payment"), ("📞 Follow-up", "f:followup")],
        [("🧾 Invoice", "f:invoice")]]),
    "club": ("🏫 <b>Club Management</b>", [
        [("🏫 My clubs", "a:clubs"), ("➕ New club", "f:club_new")],
        [("📅 Add event", "f:event"), ("🙋 Add member", "f:member")],
        [("📋 Assign task", "f:clubtask"), ("📢 Announce", "f:announce")]]),
}


def kb(rows: list[list[tuple[str, str]]], back: bool = False) -> M:
    out = [[B(t, callback_data=d) for t, d in row] for row in rows]
    if back:
        out.append([B("⬅️ Back", callback_data="m:main")])
    return M(out)


def main_menu() -> M:
    return kb(MAIN)


def submenu(key: str) -> tuple[str, M]:
    title, rows = SUB[key]
    return title, kb(rows, back=True)


def pager(name: str, page: int, total: int, per: int, extra: list[list[tuple[str, str]]] | None = None) -> M:
    rows = list(extra or [])
    nav = []
    if page > 0:
        nav.append(("⬅️ Prev", f"a:{name}:{page - 1}"))
    if (page + 1) * per < total:
        nav.append(("Next ➡️", f"a:{name}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([("🏠 Menu", "m:main")])
    return kb(rows)


def yes_no(action_name: str, item_id: int | str) -> M:
    return M([[B("✅ Yes, do it", callback_data=f"cf:{action_name}:{item_id}"), B("✖️ No", callback_data="cf:no:0")]])


def cancel_row(optional: bool) -> M:
    row = [B("✖️ Cancel", callback_data="form:cancel")]
    if optional:
        row.insert(0, B("⏭ Skip", callback_data="form:skip"))
    return M([row])


def confirm_form() -> M:
    return M([[B("✅ Confirm", callback_data="form:yes"), B("✖️ Cancel", callback_data="form:no")]])
