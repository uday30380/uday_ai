import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.config import ConfigError, Settings
from app.database.models import Task, User
from app.database.repositories import get_or_create_user


def make(**kw):
    base = dict(_env_file=None, telegram_bot_token="123456789:" + "A" * 35, admin_ids="111")
    base.update(kw)
    return Settings(**base)


def test_valid_config_passes():
    make().validate_runtime()


def test_missing_values_are_all_reported():
    with pytest.raises(ConfigError) as e:
        Settings(_env_file=None).validate_runtime()
    msg = str(e.value)
    assert "TELEGRAM_BOT_TOKEN" in msg and "ADMIN_IDS" in msg


@pytest.mark.parametrize("kw,needle", [({"telegram_bot_token": "garbage"}, "does not look valid"),
                                       ({"admin_ids": "abc"}, "ADMIN_IDS invalid"),
                                       ({"app_timezone": "Mars/Base"}, "APP_TIMEZONE")])
def test_bad_values(kw, needle):
    with pytest.raises(ConfigError) as e:
        make(**kw).validate_runtime()
    assert needle in str(e.value)


def test_non_sqlite_and_bad_briefing_time_rejected():
    with pytest.raises(ValueError):
        make(database_url="postgresql://x/y")
    with pytest.raises(ValueError):
        make(daily_briefing_time="25:99")


def test_ai_disabled_without_model():
    assert make().ai_enabled is False
    assert make(ollama_model="llama3").ai_enabled is True


def test_migrations_create_all_tables(db):
    names = set(inspect(db.get_engine()).get_table_names())
    for t in ["users", "tasks", "reminders", "reminder_attempts", "ai_messages", "dev_projects", "snippets", "clients",
              "freelance_projects", "payments", "followups", "clubs", "club_members", "club_events", "club_tasks",
              "announcements", "audit_logs"]:
        assert t in names


def test_foreign_keys_enforced(db, owner):
    with pytest.raises(IntegrityError):
        with db.session_scope() as s:
            s.add(Task(user_id=9999, title="orphan"))


def test_user_registration_and_owner_flag(db, settings):
    with db.session_scope() as s:
        a = get_or_create_user(s, settings, 111, "o", "Owner")
        b = get_or_create_user(s, settings, 222, "x", "X")
        again = get_or_create_user(s, settings, 111, "o2", "Owner")
        assert a.id == again.id and a.is_owner and not b.is_owner
    with db.session_scope() as s:
        assert s.scalar(select(User.username).where(User.telegram_id == 111)) == "o2"
        assert s.execute(text("select count(*) from users")).scalar() == 2
