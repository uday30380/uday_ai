import csv
import io

import httpx
import pytest

from app.database.repositories import get_or_create_user
from app.services import ai_service as ai, club_service as cs
from app.services.ollama_service import AIQuotaExceeded, AITimeout, AIUnavailable, OllamaClient
from app.utils import security as sec, validation as v


@pytest.fixture
def people(db, settings):
    with db.session_scope() as s:
        return [get_or_create_user(s, settings, i, f"u{i}", f"User{i}") for i in (111, 222, 333)]


def test_club_roles_and_permissions(db, people):
    owner, admin, member = people
    with db.session_scope() as s:
        club = cs.create_club(s, owner, "Tech Club")
        cid = club.id
        cs.add_member(s, cid, owner, "Admin", "admin", admin.telegram_id, "admin@x.com")
        cs.add_member(s, cid, owner, "Mem", "member", member.telegram_id, "secret@x.com")
        with pytest.raises(cs.PermissionDenied):
            cs.add_member(s, cid, admin, "Another admin", "admin")          # only owner makes admins
        cs.add_member(s, cid, admin, "Newbie", "member")                    # admin may add members
        with pytest.raises(cs.PermissionDenied):
            cs.add_member(s, cid, member, "Sneaky", "member")               # members may not
        with pytest.raises(cs.PermissionDenied):
            cs.create_event(s, cid, member, "Party", "event", __import__("datetime").datetime(2030, 1, 1))
        with pytest.raises(cs.PermissionDenied):
            cs.export_csv(s, cid, member, "members")
    with db.session_scope() as s:
        as_member = cs.list_members(s, cid, member)
        assert all(m["contact"] == "" and m["telegram_id"] is None for m in as_member)
        as_admin = cs.list_members(s, cid, admin)
        assert any(m["contact"] == "secret@x.com" for m in as_admin)


def test_clubs_are_isolated(db, people):
    a, b, _ = people
    with db.session_scope() as s:
        c1 = cs.create_club(s, a, "Alpha")
        c2 = cs.create_club(s, b, "Beta")
        for fn in (lambda: cs.list_members(s, c1.id, b), lambda: cs.list_events(s, c1.id, b), lambda: cs.list_tasks(s, c1.id, b),
                   lambda: cs.draft_announcement(s, c1.id, b, "hi"), lambda: cs.export_csv(s, c1.id, b, "members"),
                   lambda: cs.resolve_club(s, b, "Alpha")):
            with pytest.raises(cs.PermissionDenied):
                fn()
        assert [c.name for c, _ in cs.my_clubs(s, b)] == ["Beta"]
        assert c2.id != c1.id


def test_member_sees_only_own_tasks_and_can_complete_only_those(db, people):
    owner, _, member = people
    with db.session_scope() as s:
        c = cs.create_club(s, owner, "Club")
        m = cs.add_member(s, c.id, owner, "Mem", "member", member.telegram_id)
        t1 = cs.create_task(s, c.id, owner, "mine", m, None)
        t2 = cs.create_task(s, c.id, owner, "not mine", None, None)
        assert [t.title for t in cs.list_tasks(s, c.id, member)] == ["mine"]
        assert len(cs.list_tasks(s, c.id, owner)) == 2
        with pytest.raises(cs.PermissionDenied):
            cs.complete_task(s, member, t2.id)
        cs.complete_task(s, member, t1.id)


async def test_announcement_requires_admin_and_sends_once(db, people):
    owner, _, member = people
    with db.session_scope() as s:
        c = cs.create_club(s, owner, "Club")
        cs.add_member(s, c.id, owner, "Mem", "member", member.telegram_id)
        cs.add_member(s, c.id, owner, "Ghost", "member", None)
        with pytest.raises(cs.PermissionDenied):
            cs.draft_announcement(s, c.id, member, "hi")
        ann = cs.draft_announcement(s, c.id, owner, "Meeting at 5")
        aid = ann.id
    sent = []

    async def send(tid, text): sent.append(tid)
    with pytest.raises(cs.PermissionDenied):
        await cs.send_announcement(db.session_scope, member, aid, send)
    assert sent == []
    a = await cs.send_announcement(db.session_scope, owner, aid, send)
    assert sorted(sent) == [111, 333] and a.delivered == 2
    with pytest.raises(ValueError):
        await cs.send_announcement(db.session_scope, owner, aid, send)
    assert len(sent) == 2


def test_csv_export_neutralises_formulas(db, people):
    owner = people[0]
    with db.session_scope() as s:
        c = cs.create_club(s, owner, "Club")
        cs.add_member(s, c.id, owner, "=HYPERLINK(\"http://evil\")", "member", None, "+cmd")
        name, data = cs.export_csv(s, c.id, owner, "members")
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    assert rows[2][1].startswith("'=") and rows[2][4].startswith("'+")


# ---- AI -----------------------------------------------------------------------------------------
class FakeClient:
    def __init__(self, reply="ok", exc=None):
        self.reply, self.exc, self.calls = reply, exc, []

    async def is_available(self): return self.exc is None

    async def chat(self, messages):
        self.calls.append(messages)
        if self.exc:
            raise self.exc
        return self.reply


async def test_ai_unconfigured_raises_unavailable(db, owner, settings):
    with db.session_scope() as s:
        with pytest.raises(AIUnavailable):
            await ai.AIService(settings).respond(s, owner.id, "hi")


async def test_ai_provider_errors_propagate_without_billing_usage(db, owner, settings):
    svc = ai.AIService(settings, FakeClient(exc=AITimeout("slow")))
    with db.session_scope() as s:
        with pytest.raises(AITimeout):
            await svc.respond(s, owner.id, "hi")
        assert ai.usage_today(s, owner.id) == 0


async def test_ai_limits_history_and_clear(db, owner, settings):
    settings.ai_daily_request_limit, settings.ai_history_messages, settings.ai_max_input_chars = 3, 2, 50
    fake = FakeClient("answer")
    svc = ai.AIService(settings, fake)
    with db.session_scope() as s:
        with pytest.raises(Exception, match="too long"):
            await svc.respond(s, owner.id, "x" * 51)
        for i in range(3):
            assert await svc.respond(s, owner.id, f"q{i}") == "answer"
        with pytest.raises(AIQuotaExceeded):
            await svc.respond(s, owner.id, "q4")
        assert len(fake.calls[-1]) <= 1 + 2 + 1             # system + bounded history + new message
        assert ai.clear_history(s, owner.id) > 0


async def test_http_failure_modes():
    c = OllamaClient("http://127.0.0.1:9", "m", timeout=1)
    assert await c.is_available() is False
    with pytest.raises(AIUnavailable):
        await c.chat([{"role": "user", "content": "x"}])


def test_fallback_helpers():
    text = "Python is great. Python is used for web. Cats are cute animals. Python also powers data science work."
    assert "Python" in ai.fallback_summary(text, 2)
    assert ai.fallback_rewrite("hello  world ,i am here") == "Hello world, I am here."


# ---- security / validation ---------------------------------------------------------------------------
def test_rate_limiter():
    t = [0.0]
    rl = sec.RateLimiter(2, clock=lambda: t[0])
    assert rl.allow(1) and rl.allow(1) and not rl.allow(1) and rl.allow(2)
    t[0] = 61
    assert rl.allow(1)


def test_authorization(settings):
    assert sec.is_owner(settings, 111) and not sec.is_owner(settings, 222)
    assert sec.is_authorized(settings, 222) and not sec.is_authorized(settings, 999)


def test_username_authorization():
    from app.config import Settings
    s = Settings(_env_file=None, telegram_bot_token="123456789:" + "A" * 35, admin_ids="@Uday_2400030380, 2400030380")
    assert sec.is_owner(s, 2400030380)
    assert sec.is_owner(s, 999, username="Uday_2400030380")
    assert sec.is_owner(s, 999, username="@Uday_2400030380")
    assert not sec.is_owner(s, 999, username="someone_else")


def test_log_redaction():
    leaked = "POST https://api.telegram.org/bot123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij/getMe api_key=sk-abcdefghijk1234"
    out = sec.redact(leaked)
    assert "ABCDEFGHIJKLMNOP" not in out and "sk-abcdefghijk1234" not in out


@pytest.mark.parametrize("fn,bad", [(v.priority, "urgent"), (v.currency, "rupees"), (v.hhmm, "7am"), (v.url, "ftp://x"),
                                    (v.positive_int, "-3"), (v.timezone_name, "Nowhere/City")])
def test_validators_reject(fn, bad):
    with pytest.raises(ValueError):
        fn(bad)


def test_clean_strips_control_chars_and_limits():
    assert v.clean("a\x00b\x07c", max_len=10) == "abc"
    with pytest.raises(ValueError):
        v.clean("x" * 11, max_len=10)
