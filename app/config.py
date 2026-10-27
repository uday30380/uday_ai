"""Configuration loaded from environment / .env and validated at startup."""
from __future__ import annotations

import re
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


def _parse_ids(raw: str) -> list[int]:
    ids: list[int] = []
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if not part or part.startswith("@"):
            continue
        if not part.lstrip("-").isdigit():
            raise ValueError(f"'{part}' is not a numeric Telegram ID")
        ids.append(int(part))
    return ids


def _parse_usernames(raw: str) -> list[str]:
    names: list[str] = []
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if part.startswith("@"):
            cleaned = part.lstrip("@").lower()
            if cleaned:
                names.append(cleaned)
    return names


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: str = ""
    admin_ids: str = ""
    allowed_user_ids: str = ""
    database_url: str = "sqlite:///./data/uday_ai.db"
    app_timezone: str = "Asia/Kolkata"
    log_level: str = "INFO"

    ai_provider: str = "ollama"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = ""
    ai_max_tokens: int = Field(1024, ge=16, le=8192)
    ai_timeout_seconds: int = Field(120, ge=5, le=900)
    ai_max_input_chars: int = Field(4000, ge=100, le=20000)
    ai_history_messages: int = Field(10, ge=0, le=50)
    ai_daily_request_limit: int = Field(30, ge=0)
    rate_limit_per_minute: int = Field(15, ge=1)

    hosted_ai_base_url: str = ""
    hosted_ai_api_key: str = ""
    hosted_ai_model: str = ""

    enable_daily_briefing: bool = False
    daily_briefing_time: str = "07:00"

    @field_validator("ai_provider")
    @classmethod
    def _provider(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"ollama", "openai_compatible", "none"}:
            raise ValueError("AI_PROVIDER must be ollama, openai_compatible or none")
        return v

    @field_validator("daily_briefing_time")
    @classmethod
    def _briefing(cls, v: str) -> str:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v.strip()):
            raise ValueError("DAILY_BRIEFING_TIME must look like 07:00 (24-hour HH:MM)")
        return v.strip()

    @field_validator("database_url")
    @classmethod
    def _db(cls, v: str) -> str:
        if not v.startswith("sqlite"):
            raise ValueError("Only sqlite:/// URLs are supported in this release")
        return v

    # ---- derived -------------------------------------------------------
    @property
    def admin_id_list(self) -> list[int]:
        return _parse_ids(self.admin_ids)

    @property
    def admin_username_list(self) -> list[str]:
        return _parse_usernames(self.admin_ids)

    @property
    def allowed_id_list(self) -> list[int]:
        return _parse_ids(self.allowed_user_ids)

    @property
    def allowed_username_list(self) -> list[str]:
        return _parse_usernames(self.allowed_user_ids)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.app_timezone)

    @property
    def ai_enabled(self) -> bool:
        if self.ai_provider == "ollama":
            return bool(self.ollama_model.strip())
        if self.ai_provider == "openai_compatible":
            return bool(self.hosted_ai_base_url and self.hosted_ai_model)
        return False

    def validate_runtime(self) -> None:
        """Strict checks needed before the bot starts. Raises ConfigError with all problems."""
        problems: list[str] = []
        if not self.telegram_bot_token:
            problems.append("TELEGRAM_BOT_TOKEN is missing. Create a bot with @BotFather and put the token in .env")
        elif not TOKEN_RE.match(self.telegram_bot_token):
            problems.append("TELEGRAM_BOT_TOKEN does not look valid (expected 123456:ABC...)")
        try:
            if not self.admin_id_list and not self.admin_username_list:
                problems.append("ADMIN_IDS is empty. Put your numeric Telegram user ID or @username in .env")
        except ValueError as exc:
            problems.append(f"ADMIN_IDS invalid: {exc}")
        try:
            self.allowed_id_list
            self.allowed_username_list
        except ValueError as exc:
            problems.append(f"ALLOWED_USER_IDS invalid: {exc}")
        try:
            ZoneInfo(self.app_timezone)
        except (ZoneInfoNotFoundError, ValueError):
            problems.append(f"APP_TIMEZONE '{self.app_timezone}' is not a valid IANA timezone (e.g. Asia/Kolkata)")
        if self.ai_provider == "openai_compatible" and not (self.hosted_ai_base_url and self.hosted_ai_model):
            problems.append("AI_PROVIDER=openai_compatible needs HOSTED_AI_BASE_URL and HOSTED_AI_MODEL")
        if problems:
            raise ConfigError("Configuration problems:\n - " + "\n - ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
