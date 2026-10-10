"""Shared fixtures. Unit tests never touch the network: any socket connect
raises, and every external provider is a fake."""

from __future__ import annotations

import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.app import build_services
from bot.config import Settings
from bot.models.translation import Source, TranslationResult, WiktionaryEntry
from bot.providers.base import ProviderError


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def guard(*args, **kwargs):
        raise RuntimeError("network access in unit tests is forbidden")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket.socket, "connect_ex", guard)
    monkeypatch.setattr(socket, "create_connection", guard)


class FakeWiktionary:
    def __init__(self, entries: dict[str, WiktionaryEntry] | None = None, fail: bool = False):
        self.entries = entries or {}
        self.fail = fail
        self.calls: list[str] = []

    async def lookup(self, word: str):
        self.calls.append(word)
        if self.fail:
            raise ProviderError("wiktionary", "timeout")
        return self.entries.get(word)


class FakeMyMemory:
    def __init__(self, answers: dict[str, str] | None = None, fail: bool = False):
        self.answers = answers or {}
        self.fail = fail
        self.calls: list[tuple[str, str, str]] = []

    async def translate(self, text, source, target):
        self.calls.append((text, source, target))
        if self.fail or text not in self.answers:
            raise ProviderError("mymemory", "HTTP 503")
        return TranslationResult(text=self.answers[text], source=Source.MYMEMORY)


class FakeLLM:
    model = "fake-model"

    def __init__(self, translate="", check="", phrase="", fail=False):
        self._translate, self._check, self._phrase, self.fail = translate, check, phrase, fail
        self.calls: list[str] = []

    async def translate(self, text, source, target):
        self.calls.append("translate")
        if self.fail:
            raise ProviderError("anthropic", "APIError")
        return self._translate

    async def natural_phrase(self, text):
        self.calls.append("phrase")
        if self.fail:
            raise ProviderError("anthropic", "APIError")
        return self._phrase

    async def check(self, text):
        self.calls.append("check")
        if self.fail:
            raise ProviderError("anthropic", "APIError")
        return self._check


def make_settings(**overrides) -> Settings:
    env = {"TELEGRAM_BOT_TOKEN": "123456:TEST", "DATABASE_PATH": ":memory:"}
    env.update({k: str(v) for k, v in overrides.items()})
    return Settings.from_env(env)


@pytest.fixture
def make_services():
    def _make(llm=None, wiktionary=None, mymemory=None, **settings):
        return build_services(
            make_settings(**settings),
            llm=llm,
            wiktionary=wiktionary or FakeWiktionary(),
            mymemory=mymemory or FakeMyMemory(),
        )

    return _make


# ----------------------------------------------------------- Telegram fakes


class FakeMessage:
    def __init__(self, text: str = "", user_id: int = 1):
        self.text = text
        self.reply_to_message = None
        self.sent: list[dict] = []
        self.voices: list[dict] = []
        self.deleted = False
        self.edited: list[str] = []
        self.reply_text = AsyncMock(side_effect=self._reply)
        self.reply_voice = AsyncMock(side_effect=self._voice)

    async def _reply(self, text, **kwargs):
        self.sent.append({"text": text, **kwargs})
        return FakeStatus(self)

    async def _voice(self, voice, **kwargs):
        self.voices.append({"data": voice.read(), **kwargs})


class FakeStatus:
    def __init__(self, parent):
        self.parent = parent

    async def edit_text(self, text, **kwargs):
        self.parent.edited.append(text)

    async def delete(self):
        self.parent.deleted = True


class FakeQuery:
    def __init__(self, data: str, message: FakeMessage):
        self.data = data
        self.message = message
        self.answers: list[str] = []
        self.edited_text: list[str] = []
        self.markups: list = []

    async def answer(self, text=None, **kwargs):
        self.answers.append(text)

    async def edit_message_text(self, text, **kwargs):
        self.edited_text.append(text)

    async def edit_message_reply_markup(self, reply_markup=None, **kwargs):
        self.markups.append(reply_markup)


def make_update(text: str = "", user_id: int = 1, callback: str | None = None):
    message = FakeMessage(text, user_id)
    query = FakeQuery(callback, message) if callback else None
    return SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=user_id),
        callback_query=query,
    )


def make_context(services, args=None, user_data=None):
    return SimpleNamespace(
        application=SimpleNamespace(bot_data={"services": services}),
        user_data={} if user_data is None else user_data,
        args=args or [],
    )
