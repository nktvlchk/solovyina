"""Wiring: build services and the Telegram application."""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections import OrderedDict
from dataclasses import dataclass, field

from telegram.ext import Application, ApplicationBuilder

from bot.config import Settings
from bot.providers.base import make_client
from bot.providers.mymemory import MyMemoryProvider
from bot.providers.wiktionary import WiktionaryProvider
from bot.services.correction import CorrectionService
from bot.services.dictionary import Dictionary
from bot.services.limits import ConcurrencyLimiter, LlmBudget, RateLimiter
from bot.services.translation import TranslationService
from bot.services.tts import TtsService
from bot.storage.database import Database
from bot.storage.repositories import (
    AnalyticsRepository,
    CorrectionsRepository,
    MetricsRepository,
    PrivacyRepository,
    SavedWordsRepository,
    SettingsRepository,
)

logger = logging.getLogger(__name__)


class TextCache:
    """Short-lived in-memory map short_key -> text (for button payloads).
    Keeps Telegram callback_data tiny and keeps text out of the database."""

    def __init__(self, size: int = 2000):
        self._data: OrderedDict[str, str] = OrderedDict()
        self._size = size

    def put(self, text: str) -> str:
        # Random, not a counter: after a restart an old button must not
        # resolve to someone else's new text.
        key = secrets.token_urlsafe(6)
        self._data[key] = text
        if len(self._data) > self._size:
            self._data.popitem(last=False)
        return key

    def get(self, key: str) -> str | None:
        return self._data.get(key)


@dataclass
class Services:
    settings: Settings
    db: Database
    dictionary: Dictionary
    translation: TranslationService
    correction: CorrectionService
    tts: TtsService
    user_settings: SettingsRepository
    saved_words: SavedWordsRepository
    corrections: CorrectionsRepository
    metrics: MetricsRepository
    privacy: PrivacyRepository
    rate_limiter: RateLimiter
    concurrency: ConcurrencyLimiter
    analytics: AnalyticsRepository | None = None
    text_cache: TextCache = field(default_factory=TextCache)
    http_client: object = None


def build_services(settings: Settings, llm=None, wiktionary=None, mymemory=None, google=None) -> Services:
    db = Database(settings.database_path)
    metrics = MetricsRepository(db)
    corrections = CorrectionsRepository(db)
    dictionary = Dictionary.load(settings.dictionary_path)
    client = None
    if wiktionary is None or mymemory is None:
        client = make_client(settings.http_timeout_seconds)
    wiktionary = wiktionary or WiktionaryProvider(client)
    mymemory = mymemory or MyMemoryProvider(client)
    if google is None and settings.google_translate_api_key:
        from bot.providers.google import GoogleTranslateProvider

        client = client or make_client(settings.http_timeout_seconds)
        google = GoogleTranslateProvider(client, settings.google_translate_api_key)
    if llm is None and settings.llm_enabled:
        from bot.providers.anthropic import AnthropicProvider

        llm = AnthropicProvider(settings.anthropic_api_key, settings.llm_model)
    budget = (
        LlmBudget(metrics, settings.llm_daily_limit, settings.llm_user_daily_limit)
        if llm is not None
        else None
    )
    logger.info(
        "dictionary: %d headwords; model features: %s; google translate: %s",
        len(dictionary),
        "on" if llm is not None else "off (no ANTHROPIC_API_KEY)",
        "on" if google is not None else "off (no GOOGLE_TRANSLATE_API_KEY)",
    )
    return Services(
        settings=settings,
        db=db,
        dictionary=dictionary,
        translation=TranslationService(dictionary, wiktionary, mymemory, llm, budget, metrics, google),
        correction=CorrectionService(llm, budget, corrections, metrics, settings.store_raw_text),
        tts=TtsService(settings.max_tts_length),
        user_settings=SettingsRepository(db),
        saved_words=SavedWordsRepository(db),
        corrections=corrections,
        metrics=metrics,
        privacy=PrivacyRepository(db),
        rate_limiter=RateLimiter(settings.user_rate_limit_per_minute),
        concurrency=ConcurrencyLimiter(settings.max_concurrent_requests),
        http_client=client,
        analytics=AnalyticsRepository(db),
    )


async def _retention_loop(services: Services) -> None:
    while True:
        try:
            removed = services.privacy.purge_older_than(services.settings.retention_days)
            services.analytics.purge_older_than(services.settings.retention_days)
            if removed:
                logger.info("retention: removed %d old corrections", removed)
        except Exception as exc:  # never crash the bot because of cleanup
            logger.warning("retention failed: %s", type(exc).__name__)
        await asyncio.sleep(6 * 3600)


def build_application(settings: Settings, services: Services | None = None) -> Application:
    from bot.handlers import register_handlers

    services = services or build_services(settings)

    async def post_init(app: Application) -> None:
        app.bot_data["retention_task"] = asyncio.create_task(_retention_loop(services))
        await app.bot.set_my_commands(COMMANDS)

    async def post_shutdown(app: Application) -> None:
        task = app.bot_data.get("retention_task")
        if task:
            task.cancel()
        if services.http_client is not None:
            await services.http_client.aclose()
        services.db.close()

    app = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .concurrent_updates(True)
        .build()
    )
    app.bot_data["services"] = services
    register_handlers(app)
    return app


COMMANDS = [
    ("start", "Почати"),
    ("help", "Як користуватися"),
    ("tr", "Перекласти текст (або відповідь на повідомлення)"),
    ("say", "Як сказати? Природний український варіант"),
    ("check", "Перевір мене: перевірка тексту"),
    ("mistakes", "Мої помилки"),
    ("direction", "Напрям перекладу"),
    ("privacy", "Які дані обробляються"),
    ("delete_my_data", "Видалити мої дані"),
    ("about", "Про проєкт і джерела"),
]
