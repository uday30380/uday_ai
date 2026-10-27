"""Entry point: python -m app.main"""
from __future__ import annotations

import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.config import ConfigError, get_settings
from app.database.session import init_db
from app.utils.security import setup_logging


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Uday AI Personal Assistant is active and running.\n")

    def log_message(self, format, *args):
        pass  # Quiet health check logs


def _start_health_server(port: int) -> None:
    try:
        server = HTTPServer(("0.0.0.0", port), HealthHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("uday").warning("Could not bind health server to port %s: %s", port, exc)


def main() -> int:
    settings = get_settings()
    setup_logging(settings.log_level)
    try:
        settings.validate_runtime()
    except ConfigError as exc:
        print(f"\n❌ {exc}\n\nCopy .env.example to .env and fill in the missing values.", file=sys.stderr)
        return 2
    init_db(settings.database_url)
    
    port_str = os.getenv("PORT")
    if port_str and port_str.isdigit():
        _start_health_server(int(port_str))
        logging.getLogger("uday").info("Health check server active on port %s (Free Cloud Web Service mode)", port_str)

    from app.bot.application import build_application
    app = build_application(settings)
    log = logging.getLogger("uday")
    log.info("Starting Uday AI (long polling). AI provider=%s enabled=%s", settings.ai_provider, settings.ai_enabled)
    app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
