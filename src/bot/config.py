"""Configuration from environment variables, validated at start-up."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DICTIONARY = PROJECT_ROOT / "data" / "dictionary.tsv"


class ConfigError(Exception):
    """Raised for invalid configuration. Never includes secret values."""


def _bool(name: str, raw: str | None, default: bool) -> bool:
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be true or false")


def _int(name: str, raw: str | None, default: int, minimum: int = 0) -> int:
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        raise ConfigError(f"{name} must be an integer") from None
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}")
    return value


def _admin_ids(raw: str | None) -> frozenset[int]:
    if not raw:
        return frozenset()
    ids = set()
    for part in raw.replace(" ", "").split(","):
        if not part:
            continue
        if not part.lstrip("-").isdigit():
            raise ConfigError("ADMIN_IDS must be a comma-separated list of numeric Telegram IDs")
        ids.add(int(part))
    return frozenset(ids)


def _bot_username(raw: str | None) -> str | None:
    if not raw or not raw.strip():
        return None
    name = raw.strip().lstrip("@")
    if not name.replace("_", "").isalnum() or not name.lower().endswith("bot"):
        raise ConfigError("REDIRECT_TO_BOT must be a bot username like @my_new_bot")
    return name


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str = field(repr=False)
    anthropic_api_key: str | None = field(default=None, repr=False)
    google_translate_api_key: str | None = field(default=None, repr=False)
    admin_ids: frozenset[int] = frozenset()
    database_path: Path = Path("data/runtime/bot.sqlite3")
    dictionary_path: Path = DEFAULT_DICTIONARY
    store_raw_text: bool = False
    log_level: str = "INFO"
    llm_model: str = "claude-haiku-4-5-20251001"
    llm_daily_limit: int = 500
    llm_user_daily_limit: int = 60
    max_input_length: int = 1000
    max_tts_length: int = 300
    user_rate_limit_per_minute: int = 20
    max_concurrent_requests: int = 8
    http_timeout_seconds: float = 8.0
    retention_days: int = 180
    redirect_to_bot: str | None = None

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_api_key)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        env = dict(os.environ) if env is None else env
        token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
        if not token:
            raise ConfigError(
                "TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env "
                "or set the variable in your hosting provider."
            )
        if ":" not in token:
            raise ConfigError("TELEGRAM_BOT_TOKEN has an unexpected format (value not shown)")

        log_level = (env.get("LOG_LEVEL") or "INFO").strip().upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR or CRITICAL")

        return cls(
            telegram_bot_token=token,
            anthropic_api_key=(env.get("ANTHROPIC_API_KEY") or "").strip() or None,
            google_translate_api_key=(env.get("GOOGLE_TRANSLATE_API_KEY") or "").strip() or None,
            admin_ids=_admin_ids(env.get("ADMIN_IDS")),
            database_path=Path(env.get("DATABASE_PATH") or "data/runtime/bot.sqlite3"),
            dictionary_path=Path(env.get("DICTIONARY_PATH") or DEFAULT_DICTIONARY),
            store_raw_text=_bool("STORE_RAW_TEXT", env.get("STORE_RAW_TEXT"), False),
            log_level=log_level,
            llm_model=(env.get("LLM_MODEL") or "claude-haiku-4-5-20251001").strip(),
            llm_daily_limit=_int("LLM_DAILY_LIMIT", env.get("LLM_DAILY_LIMIT"), 500),
            llm_user_daily_limit=_int("LLM_USER_DAILY_LIMIT", env.get("LLM_USER_DAILY_LIMIT"), 60, 1),
            max_input_length=_int("MAX_INPUT_LENGTH", env.get("MAX_INPUT_LENGTH"), 1000, 10),
            max_tts_length=_int("MAX_TTS_LENGTH", env.get("MAX_TTS_LENGTH"), 300, 10),
            user_rate_limit_per_minute=_int(
                "USER_RATE_LIMIT_PER_MINUTE", env.get("USER_RATE_LIMIT_PER_MINUTE"), 20, 1
            ),
            max_concurrent_requests=_int("MAX_CONCURRENT_REQUESTS", env.get("MAX_CONCURRENT_REQUESTS"), 8, 1),
            retention_days=_int("RETENTION_DAYS", env.get("RETENTION_DAYS"), 180, 1),
            redirect_to_bot=_bot_username(env.get("REDIRECT_TO_BOT")),
        )
