"""Unit tests for the Personal Assistant (PA) natural-language conversational handler."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo
from telegram import Update, Message, User as TgUser, Chat

from app.bot.handlers.pa_chat import handle_pa_chat
from app.database.session import session_scope
from app.database.models import User, Task, Reminder


def make_update(text: str, user_id: int = 111):
    up = MagicMock(spec=Update)
    up.effective_user = TgUser(id=user_id, first_name="Uday", is_bot=False, username="Uday_2400030380")
    up.effective_chat = MagicMock(spec=Chat)
    up.effective_chat.id = user_id
    up.callback_query = None
    msg = MagicMock(spec=Message)
    msg.text = text
    msg.reply_text = AsyncMock()
    up.effective_message = msg
    return up


@pytest.mark.asyncio
async def test_pa_greeting(settings):
    ctx = MagicMock()
    ctx.bot_data = {"settings": settings, "limiter": MagicMock(allow=lambda x: True), "ai": MagicMock()}
    ctx.bot = MagicMock()
    ctx.bot.send_chat_action = AsyncMock()
    
    with session_scope() as s:
        u = s.scalar(User.__table__.select().where(User.telegram_id == 5322871281))
        if not u:
            s.add(User(telegram_id=5322871281, first_name="Uday", username="Uday_2400030380", is_owner=True, timezone="Asia/Kolkata"))

    up = make_update("hello")
    await handle_pa_chat(up, ctx)
    assert up.effective_message.reply_text.called
    reply_text = up.effective_message.reply_text.call_args[0][0]
    assert "Executive Personal Assistant" in reply_text


@pytest.mark.asyncio
async def test_pa_task_creation(settings):
    ctx = MagicMock()
    ctx.bot_data = {"settings": settings, "limiter": MagicMock(allow=lambda x: True), "ai": MagicMock()}
    ctx.bot = MagicMock()
    ctx.bot.send_chat_action = AsyncMock()

    up = make_update("Add task: Submit quarterly project report urgent")
    await handle_pa_chat(up, ctx)
    assert up.effective_message.reply_text.called
    reply_text = up.effective_message.reply_text.call_args[0][0]
    assert "Task Added" in reply_text
    assert "Submit quarterly project report" in reply_text
