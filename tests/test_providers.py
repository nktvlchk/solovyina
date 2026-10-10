"""Provider parsing with httpx.MockTransport: no real network."""

import httpx
import pytest

from bot.providers.base import ProviderError
from bot.providers.mymemory import MyMemoryProvider
from bot.providers.wiktionary import WiktionaryProvider

WIKITEXT = """= {{-uk-}} =
=== Морфологические и синтаксические свойства ===
{{сущ uk m a 1a}}
=== Произношение ===
{{transcription-uk|kʲit}}
=== Семантические свойства ===
==== Значение ====
# [[кот]] {{пример|Кіт спить.}}
==== Синонимы ====
# [[котик]]
"""


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_mymemory_ok():
    def handler(request):
        return httpx.Response(
            200, json={"responseStatus": 200, "responseData": {"translatedText": "кот", "match": 1}}
        )

    result = await MyMemoryProvider(client(handler)).translate("кіт", "uk", "ru")
    assert result.text == "кот" and result.source.value == "mymemory"


async def test_mymemory_quota_message_is_error():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "responseStatus": 200,
                "responseData": {"translatedText": "MYMEMORY WARNING: YOU USED ALL"},
            },
        )

    with pytest.raises(ProviderError):
        await MyMemoryProvider(client(handler)).translate("кіт", "uk", "ru")


async def test_retries_are_bounded(monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503)

    async def no_sleep(_):
        return None

    monkeypatch.setattr("bot.providers.base.asyncio.sleep", no_sleep)
    with pytest.raises(ProviderError):
        await MyMemoryProvider(client(handler)).translate("кіт", "uk", "ru")
    assert len(calls) == 3


async def test_timeout_becomes_provider_error(monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout("slow")

    async def no_sleep(_):
        return None

    monkeypatch.setattr("bot.providers.base.asyncio.sleep", no_sleep)
    with pytest.raises(ProviderError) as exc:
        await MyMemoryProvider(client(handler)).translate("кіт", "uk", "ru")
    assert "кіт" not in str(exc.value)


async def test_wiktionary_parse_and_cache():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"parse": {"wikitext": {"*": WIKITEXT}}})

    provider = WiktionaryProvider(client(handler))
    entry = await provider.lookup("кіт")
    assert entry.definitions == ("кот",) and entry.pos == "ім."
    assert entry.url.startswith("https://ru.wiktionary.org/wiki/")
    await provider.lookup("кіт")
    assert len(calls) == 1


async def test_wiktionary_missing_page():
    def handler(request):
        return httpx.Response(200, json={"error": {"code": "missingtitle"}})

    assert await WiktionaryProvider(client(handler)).lookup("абв") is None
