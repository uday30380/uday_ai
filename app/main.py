"""Entry point: python -m app.main"""
from __future__ import annotations

import logging
import sys

from app.config import ConfigError, get_settings
from app.database.session import init_db
from app.utils.security import setup_logging


def main() -> int:
    settings = get_settings()
    setup_logging(settings.log_level)
    try:
        settings.validate_runtime()
    except ConfigError as exc:
        print(f"\n❌ {exc}\n\nCopy .env.example to .env and fill in the missing values.", file=sys.stderr)
        return 2
    init_db(settings.database_url)
    from app.bot.application import build_application
    app = build_application(settings)
    log = logging.getLogger("uday")
    log.info("Starting Uday AI (long polling). AI provider=%s enabled=%s", settings.ai_provider, settings.ai_enabled)
    app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
