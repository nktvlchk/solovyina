"""Google Cloud Translation API (Basic, v2): the official, documented API.

Optional: enabled only when GOOGLE_TRANSLATE_API_KEY is set. The key travels
as a query parameter, so it is never logged (the HTTP helper does not log
URLs). Docs: https://cloud.google.com/translate/docs/reference/rest/v2/translate
"""

from __future__ import annotations

import html

import httpx

from bot.models.translation import Source, TranslationResult
from bot.providers.base import ProviderError, get_json

API_URL = "https://translation.googleapis.com/language/translate/v2"


class GoogleTranslateProvider:
    name = "google"

    def __init__(self, client: httpx.AsyncClient, api_key: str):
        self._client = client
        self._key = api_key

    async def translate(self, text: str, source: str, target: str) -> TranslationResult:
        data = await get_json(
            self._client,
            self.name,
            API_URL,
            params={"q": text, "source": source, "target": target, "format": "text", "key": self._key},
        )
        try:
            translated = html.unescape(data["data"]["translations"][0]["translatedText"]).strip()
        except (KeyError, IndexError, TypeError):
            raise ProviderError(self.name, "unexpected response shape") from None
        if not translated:
            raise ProviderError(self.name, "empty translation")
        return TranslationResult(text=translated, source=Source.GOOGLE)
