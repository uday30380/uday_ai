"""Apply database migrations: python scripts/init_db.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings  # noqa: E402
from app.database.session import init_db  # noqa: E402

init_db(get_settings().database_url)
print("Database is up to date.")
