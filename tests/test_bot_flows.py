"""End-to-end tests: real Update objects go through the real handlers; only Telegram's network calls are mocked."""
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from telegram import CallbackQuery, Message, Update
from telegram.ext import Application

from app.bot.application import build_application
from app.database.models import Task
from sqlalchemy import select

_uid = [1000]


@pytest.fixture
async def bot(db, settings, monkeypatch):
    sent = []

    rt = AsyncMock(side_effect=lambda text, **kw: sent.append(text))
    monkeypatch.setattr(Message, "reply_text", rt)
    monkeypatch.setattr(Message, "reply_document", AsyncMock())
    from telegram.ext import ExtBot
    monkeypatch.setattr(ExtBot, "send_chat_action", AsyncMock())
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    monkeypatch.setattr(CallbackQuery, "edit_message_text", AsyncMock(side_effect=lambda text, **kw: sent.append(text)))
    app = build_application(settings, with_scheduler=False, form_timeout=None)
    from telegram import User as TgUser
    app.bot._bot_user = TgUser(id=1, first_name="UdayAI", is_bot=True, username="uday_test_bot")  # no getMe call
    app.bot._initialized = True
    app._initialized = True   # skip Application.initialize(): it would call Telegram's getMe
    app.sent = sent

    async def send(text, user_id=111):
        _uid[0] += 1
        data = {"update_id": _uid[0], "message": {"message_id": _uid[0], "date": int(datetime.now().timestamp()),
                "chat": {"id": user_id, "type": "private"}, "from": {"id": user_id, "is_bot": False, "first_name": "T", "username": "t"},
                "text": text, **({"entities": [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]} if text.startswith("/") else {})}}
        await app.process_update(Update.de_json(data, app.bot))

    async def press(cb, user_id=111):
        _uid[0] += 1
        data = {"update_id": _uid[0], "callback_query": {"id": str(_uid[0]), "chat_instance": "x", "data": cb,
                "from": {"id": user_id, "is_bot": False, "first_name": "T"},
                "message": {"message_id": 1, "date": int(datetime.now().timestamp()), "chat": {"id": user_id, "type": "private"}, "text": "m"}}}
        await app.process_update(Update.de_json(data, app.bot))
    app.send, app.press = send, press
    return app


async def test_unauthorized_user_is_refused_and_not_registered(bot, db):
    await bot.send("/tasks", user_id=999)
    assert "private assistant" in bot.sent[-1]
    from app.database.models import User
    with db.session_scope() as s:
        assert s.scalar(select(User).where(User.telegram_id == 999)) is None


async def test_id_works_for_anyone(bot):
    await bot.send("/id", user_id=999)
    assert "999" in bot.sent[-1]


async def test_allowed_user_cannot_use_owner_features(bot):
    await bot.send("/task buy milk", user_id=222)
    assert "owner only" in bot.sent[-1]
    await bot.send("/club", user_id=222)          # club features are open to allowed users
    assert "not in any club" in bot.sent[-1]


async def test_task_guided_form_with_inline_title_and_skips(bot, db):
    await bot.send("/task Buy milk")                 # title prefilled → asks description
    assert "step 2/4" in bot.sent[-1]
    await bot.press("form:skip")                     # description
    await bot.send("high")                           # priority
    await bot.send("tomorrow 5pm")                   # due
    assert "Task <b>#1</b> created" in bot.sent[-1]
    with db.session_scope() as s:
        t = s.scalar(select(Task))
        assert t.title == "Buy milk" and t.priority == "high" and t.due_at is not None
    await bot.send("/tasks")
    assert "Buy milk" in bot.sent[-1]
    await bot.send("/done 1")
    assert "Done" in bot.sent[-1]


async def test_validation_error_keeps_user_in_form_and_cancel_works(bot, db):
    await bot.send("/task")
    await bot.send("Write essay")
    await bot.press("form:skip")
    await bot.send("urgent!!")                       # invalid priority
    assert "Priority must be" in bot.sent[-1]
    await bot.send("/cancel")
    assert "Cancelled" in bot.sent[-1]
    with db.session_scope() as s:
        assert s.scalar(select(Task)) is None


async def test_ambiguous_time_triggers_clarification(bot):
    await bot.send("/remind Call dad")
    await bot.send("at 5")
    assert "ambiguous" in bot.sent[-1]
    await bot.send("tomorrow 5pm")
    assert "Please confirm" in bot.sent[-1]
    await bot.press("form:yes")
    assert "Reminder <b>#1</b> set" in bot.sent[-1]


async def test_destructive_delete_requires_confirmation(bot, db):
    await bot.send("/task Temp task")
    for _ in range(3):
        await bot.press("form:skip")
    await bot.send("/delete_task 1")
    assert "Delete task" in bot.sent[-1]
    with db.session_scope() as s:
        assert s.scalar(select(Task)) is not None      # nothing deleted yet
    await bot.press("cf:no:0")
    with db.session_scope() as s:
        assert s.scalar(select(Task)) is not None
    await bot.press("cf:deltask:1")
    with db.session_scope() as s:
        assert s.scalar(select(Task)) is None


async def test_ai_offline_fallbacks_do_not_break_bot(bot):
    await bot.send("/ask what is life")
    assert "offline or not configured" in bot.sent[-1]
    await bot.send("/summarize The cat sat on the mat today. The dog barked loudly at the mailman. Birds sang in the garden.")
    assert "Extractive summary" in bot.sent[-1]
    await bot.send("/devhelp git branch")
    for _ in range(0):
        pass
    assert "Git quick reference" in bot.sent[-1]


async def test_snippet_flow_and_retrieval(bot):
    await bot.send("/snippet hello")
    await bot.press("form:skip")                     # language
    await bot.press("form:skip")                     # description
    await bot.send("print('hi')")
    assert "saved" in bot.sent[-1]
    await bot.send("/snippet hello")
    assert "print(&#x27;hi&#x27;)" in bot.sent[-1] or "print('hi')" in bot.sent[-1]


async def test_freelance_payment_requires_confirmation_and_blocks_overpay(bot, db):
    await bot.send("/client Acme")
    for _ in range(3):
        await bot.press("form:skip")
    await bot.send("/newproject Acme")
    await bot.send("Website"); await bot.press("form:skip"); await bot.send("10000"); await bot.press("form:skip"); await bot.press("form:skip")
    assert "quoted" in bot.sent[-1]
    await bot.send("/payment 1")
    await bot.send("20000")
    assert "exceeds the outstanding" in bot.sent[-1]
    await bot.send("4000"); await bot.press("form:skip"); await bot.press("form:skip")
    assert "Please confirm" in bot.sent[-1]
    from app.database.models import Payment
    with db.session_scope() as s:
        assert s.scalar(select(Payment)) is None       # not recorded before confirmation
    await bot.press("form:yes")
    assert "recorded" in bot.sent[-1]
    await bot.send("/invoice 1")
    assert "Invoice preview" in bot.sent[-1]


async def test_rate_limit_applies(bot, settings):
    bot.bot_data["limiter"].per_minute = 2
    for _ in range(4):
        await bot.send("/menu")
    assert any("too fast" in m for m in bot.sent)


async def test_unhandled_exception_does_not_leak_stack_trace(bot, monkeypatch):
    import app.bot.handlers.tasks as th
    monkeypatch.setattr(th.task_service, "list_tasks", lambda *a, **k: 1 / 0)
    await bot.send("/tasks")
    assert "Something went wrong" in bot.sent[-1] and "ZeroDivision" not in bot.sent[-1]
