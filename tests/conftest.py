import pytest

from app.config import Settings
from app.database import session as sess
from app.database.repositories import get_or_create_user


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, telegram_bot_token="123456789:" + "A" * 35, admin_ids="111", allowed_user_ids="222,333",
                    database_url=f"sqlite:///{tmp_path}/t.db", ollama_model="")


@pytest.fixture
def db(settings):
    sess.init_db(settings.database_url)  # runs real Alembic migrations
    yield sess
    sess.get_engine().dispose()


@pytest.fixture
def owner(db, settings):
    with db.session_scope() as s:
        return get_or_create_user(s, settings, 111, "owner", "Owner")


@pytest.fixture
def other(db, settings):
    with db.session_scope() as s:
        return get_or_create_user(s, settings, 222, "other", "Other")
