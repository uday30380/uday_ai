"""Restore a backup: STOP the bot first. python scripts/restore.py backups/uday_ai_XXXX.db"""
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy.engine import make_url  # noqa: E402
from app.config import get_settings  # noqa: E402

if len(sys.argv) != 2:
    sys.exit("Usage: python scripts/restore.py <backup.db>")
backup = Path(sys.argv[1])
target = Path(make_url(get_settings().database_url).database)
if sqlite3.connect(backup).execute("PRAGMA integrity_check").fetchone()[0] != "ok":
    sys.exit("Backup failed integrity check; aborting.")
if target.exists():
    safety = target.with_suffix(f".before_restore_{datetime.now():%H%M%S}.db")
    shutil.copy2(target, safety)
    print(f"Current database saved to {safety}")
for ext in ("-wal", "-shm"):
    Path(str(target) + ext).unlink(missing_ok=True)
shutil.copy2(backup, target)
print("Restored. Start the bot again.")
