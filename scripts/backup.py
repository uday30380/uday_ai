"""Consistent SQLite backup (safe while the bot runs): python scripts/backup.py [dest_dir]"""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy.engine import make_url  # noqa: E402
from app.config import get_settings  # noqa: E402

src = Path(make_url(get_settings().database_url).database)
dest_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "backups")
dest_dir.mkdir(parents=True, exist_ok=True)
dest = dest_dir / f"uday_ai_{datetime.now():%Y%m%d_%H%M%S}.db"
with sqlite3.connect(src) as a, sqlite3.connect(dest) as b:
    a.backup(b)
print(f"Backup written to {dest}")
