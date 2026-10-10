"""MyMemory translation API (official, free anonymous quota).

Docs: https://mymemory.translated.net/doc/spec.php
Replaces the unofficial Google Translate scraper used before.
"""

from __future__ import annotations

import html

import httpx

from bot.models.translation import Source, TranslationResult
from bot.providers.base import ProviderError, get_json

API_URL = "https://api.mymemory.translated.net/get"
MAX_QUERY_CHARS = 500  # MyMemory limit per request

_BAD_MARKERS = (
    "mymemory warning",
    "you used all available free translations",
    "query length limit",
    "invalid language pair",
    "<html",
)


class MyMemoryProvider:
    name = "mymemory"

    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def translate(self, text: str, source: str, target: str) -> TranslationResult:
        if len(text) > MAX_QUERY_CHARS:
            raise ProviderError(self.name, "text too long for provider")
        data = await get_json(
            self._client,
            self.name,
            API_URL,
            params={"q": text, "langpair": f"{source}|{target}"},
        )
        try:
            status = int(data.get("responseStatus", 0))
            translated = html.unescape(str(data["responseData"]["translatedText"] or "")).strip()
            match = data["responseData"].get("match")
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ProviderError(self.name, "unexpected response shape") from None
        if status != 200:
            raise ProviderError(self.name, f"status {status}")
        low = translated.lower()
        if not translated or any(m in low for m in _BAD_MARKERS):
            raise ProviderError(self.name, "empty or error text")
        if len(translated) > max(300, len(text) * 6):
            raise ProviderError(self.name, "implausible length")
        confidence = float(match) if isinstance(match, int | float) else None
        return TranslationResult(text=translated, source=Source.MYMEMORY, confidence=confidence)
