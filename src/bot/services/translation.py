"""Translation chain.

Single word:  local dictionary -> Wiktionary -> MyMemory -> model (last resort)
Phrase:       MyMemory -> model;   "natural phrasing" mode: model -> MyMemory

Providers are called one after another, never all at once. Each result
carries the source that ACTUALLY produced it.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from bot.language import Direction
from bot.models.translation import (
    DictionaryEntry,
    Source,
    TranslationResult,
    WiktionaryEntry,
)
from bot.providers.base import ProviderError
from bot.services.dictionary import Dictionary, normalize
from bot.services.limits import LlmBudget
from bot.storage.repositories import MetricsRepository

logger = logging.getLogger(__name__)


class TranslationUnavailable(Exception):
    """No provider could produce a translation."""


@dataclass
class WordLookup:
    query: str
    direction: Direction
    result: TranslationResult | None = None
    entries: list[DictionaryEntry] = field(default_factory=list)
    wiktionary: WiktionaryEntry | None = None
    matched_form: str | None = None  # set for approximate dictionary hits
    uk_word: str | None = None  # Ukrainian form, used for TTS/Wiktionary


def _same(a: str, b: str) -> bool:
    return normalize(a) == normalize(b)


class TranslationService:
    def __init__(
        self,
        dictionary: Dictionary,
        wiktionary,
        mymemory,
        llm=None,
        llm_budget: LlmBudget | None = None,
        metrics: MetricsRepository | None = None,
    ):
        self.dictionary = dictionary
        self.wiktionary = wiktionary
        self.mymemory = mymemory
        self.llm = llm
        self.llm_budget = llm_budget
        self.metrics = metrics

    # ---------------------------------------------------------------- helpers
    def _metric(self, name: str, elapsed_ms: int = 0) -> None:
        if self.metrics is not None:
            self.metrics.incr(name, elapsed_ms)

    async def _wiktionary(self, word: str) -> WiktionaryEntry | None:
        try:
            return await self.wiktionary.lookup(word)
        except ProviderError:
            self._metric("provider_error.wiktionary")
            return None

    async def _mymemory(self, text: str, direction: Direction) -> TranslationResult | None:
        started = time.monotonic()
        try:
            res = await self.mymemory.translate(text, direction.source.value, direction.target.value)
        except ProviderError:
            self._metric("provider_error.mymemory")
            return None
        self._metric("source.mymemory", int((time.monotonic() - started) * 1000))
        return None if _same(res.text, text) else res

    async def _llm_translate(
        self, text: str, direction: Direction, user_id: int | None = None
    ) -> TranslationResult | None:
        if self.llm is None or self.llm_budget is None:
            return None
        if not await self.llm_budget.try_consume(user_id):
            return None
        started = time.monotonic()
        try:
            out = await self.llm.translate(text, direction.source.value, direction.target.value)
        except ProviderError:
            self._metric("provider_error.llm")
            return None
        self._metric("source.llm", int((time.monotonic() - started) * 1000))
        out = out.strip().strip('"«»').strip()
        if not out or _same(out, text) or len(out) > max(300, len(text) * 6):
            return None
        return TranslationResult(text=out, source=Source.LLM, metadata={"model": self.llm.model})

    def _use_dictionary(
        self, lookup: WordLookup, entries: list[DictionaryEntry], form: str | None, word: str
    ) -> None:
        direction = lookup.direction
        lookup.entries = entries
        lookup.matched_form = form
        targets = (e.ru if direction is Direction.UK_RU else e.uk for e in entries)
        lookup.result = TranslationResult(
            text="; ".join(dict.fromkeys(targets)),
            source=Source.DICTIONARY,
            metadata={"approximate": form is not None},
        )
        self._metric("source.dictionary")
        if direction is Direction.UK_RU:
            lookup.uk_word = form or normalize(word)
        else:
            lookup.uk_word = entries[0].uk

    # ----------------------------------------------------------------- words
    async def translate_word(self, word: str, direction: Direction, user_id: int | None = None) -> WordLookup:
        if direction is Direction.AUTO:
            raise ValueError("direction must be explicit")
        lookup = WordLookup(query=word, direction=direction)

        # 1. Local dictionary (exact match first)
        lookup_fn = self.dictionary.lookup_uk if direction is Direction.UK_RU else self.dictionary.lookup_ru
        entries, form = lookup_fn(word)
        if entries and form is None:
            self._use_dictionary(lookup, entries, None, word)

        # 2. Wiktionary: enrichment for a Ukrainian word, and a translation
        #    source (Russian definitions) for uk->ru when the dictionary missed.
        uk_word = lookup.uk_word or (normalize(word) if direction is Direction.UK_RU else None)
        if uk_word and " " not in uk_word:
            lookup.wiktionary = await self._wiktionary(uk_word)
            if lookup.wiktionary and lookup.result is None:
                lookup.result = TranslationResult(
                    text="; ".join(lookup.wiktionary.definitions),
                    source=Source.WIKTIONARY,
                    metadata={"url": lookup.wiktionary.url},
                )
                lookup.uk_word = uk_word
                self._metric("source.wiktionary")

        # 1b. Approximate dictionary match (inflected form), clearly labeled
        if lookup.result is None and entries:
            self._use_dictionary(lookup, entries, form, word)

        if lookup.result is not None:
            return lookup

        # 3. External translator, 4. model as the last resort
        result = await self._mymemory(word, direction) or await self._llm_translate(word, direction, user_id)
        if result is None:
            raise TranslationUnavailable()
        lookup.result = result
        if direction is Direction.RU_UK and " " not in result.text.strip():
            lookup.uk_word = normalize(result.text)
            lookup.wiktionary = await self._wiktionary(lookup.uk_word)
        elif direction is Direction.UK_RU:
            lookup.uk_word = normalize(word)
        return lookup

    # --------------------------------------------------------------- phrases
    async def translate_phrase(
        self, text: str, direction: Direction, user_id: int | None = None
    ) -> TranslationResult:
        if direction is Direction.AUTO:
            raise ValueError("direction must be explicit")
        result = await self._mymemory(text, direction) or await self._llm_translate(text, direction, user_id)
        if result is None:
            raise TranslationUnavailable()
        return result

    async def natural_ukrainian(self, text: str, user_id: int | None = None) -> TranslationResult:
        """'Як сказати?': natural Ukrainian for a Russian/mixed phrase."""
        if (
            self.llm is not None
            and self.llm_budget is not None
            and await self.llm_budget.try_consume(user_id)
        ):
            started = time.monotonic()
            try:
                out = (await self.llm.natural_phrase(text)).strip().strip('"«»').strip()
            except ProviderError:
                self._metric("provider_error.llm")
            else:
                self._metric("source.llm", int((time.monotonic() - started) * 1000))
                if out and len(out) <= max(300, len(text) * 6):
                    return TranslationResult(text=out, source=Source.LLM, metadata={"model": self.llm.model})
        result = await self._mymemory(text, Direction.RU_UK)
        if result is None:
            raise TranslationUnavailable()
        return result
