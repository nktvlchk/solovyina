"""Translation chain.

Single word:  local dictionary -> Wiktionary -> Claude -> Google Translate -> MyMemory
Phrase:       Claude -> Google Translate -> MyMemory
"Як сказати?": Claude (natural phrasing prompt) -> Google Translate -> MyMemory

Claude is used only when ANTHROPIC_API_KEY is set and the daily budget allows;
Google Translate only when GOOGLE_TRANSLATE_API_KEY is set. Providers are
called one after another, never all at once. Every answer must look like the
target language (e.g. no ы/э/ё/ъ in a Ukrainian result), otherwise the next
provider is tried. Each result carries the source that ACTUALLY produced it.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field

from bot.language import Direction, Lang
from bot.models.explanation import ExplanationParseError, WordExplanation, parse_explanation
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


class ExplanationUnavailable(Exception):
    """No model-backed Ukrainian dictionary explanation is available."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


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


_ONLY_RU = set("ыэёъ")
_ONLY_UK = set("іїєґ")


def looks_like_target(text: str, target: Lang) -> bool:
    """Reject answers written in the wrong language (a frequent failure of
    translation memories such as MyMemory)."""
    low = text.lower()
    if target is Lang.UK:
        return not any(ch in _ONLY_RU for ch in low)
    if target is Lang.RU:
        return not any(ch in _ONLY_UK for ch in low)
    return True


# A lowercase letter followed by an uppercase one inside a word ("вИсновки").
_ODD_CASE = re.compile(r"(?<=[^\W\d_])(?<![A-ZА-ЯЁІЇЄҐ])[A-ZА-ЯЁІЇЄҐ]")
_WORD = re.compile(r"[^\W\d_]+")


def _has_odd_case(word: str) -> bool:
    return bool(_ODD_CASE.search(word)) and not word.isupper()


def fix_case(text: str) -> str:
    """Lowercase words with a stray capital inside ("вИсновки" -> "висновки").
    Such typos confuse language detection and translation memories. Normal
    capitals (first letter, ALL CAPS) are kept."""
    return _WORD.sub(lambda m: m.group(0).lower() if _has_odd_case(m.group(0)) else m.group(0), text)


def _garbled(out: str, text: str) -> bool:
    """Answer has odd casing that the input did not have, e.g. MyMemory
    returning "сВисновки": a sign of a broken translation-memory match."""
    return any(_has_odd_case(w) for w in _WORD.findall(out)) and not any(
        _has_odd_case(w) for w in _WORD.findall(text)
    )


def _plausible(out: str, text: str, direction: Direction) -> bool:
    return (
        bool(out)
        and not _same(out, text)
        and not _garbled(out, text)
        and len(out) <= max(300, len(text) * 6)
        and looks_like_target(out, direction.target)
    )


class TranslationService:
    def __init__(
        self,
        dictionary: Dictionary,
        wiktionary,
        mymemory,
        llm=None,
        llm_budget: LlmBudget | None = None,
        metrics: MetricsRepository | None = None,
        google=None,
    ):
        self.google = google
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
        if not _plausible(res.text, text, direction):
            self._metric("provider_error.mymemory_rejected")
            return None
        return res

    async def _google(self, text: str, direction: Direction) -> TranslationResult | None:
        if self.google is None:
            return None
        started = time.monotonic()
        try:
            res = await self.google.translate(text, direction.source.value, direction.target.value)
        except ProviderError:
            self._metric("provider_error.google")
            return None
        self._metric("source.google", int((time.monotonic() - started) * 1000))
        return res if _plausible(res.text, text, direction) else None

    async def _external(
        self, text: str, direction: Direction, user_id: int | None
    ) -> TranslationResult | None:
        """Claude -> Google Translate -> MyMemory, first plausible answer wins."""
        return (
            await self._llm_translate(text, direction, user_id)
            or await self._google(text, direction)
            or await self._mymemory(text, direction)
        )

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
        if not _plausible(out, text, direction):
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

        # 3. Claude -> Google Translate -> MyMemory
        result = await self._external(word, direction, user_id)
        if result is None:
            raise TranslationUnavailable()
        lookup.result = result
        if direction is Direction.RU_UK and " " not in result.text.strip():
            lookup.uk_word = normalize(result.text)
            lookup.wiktionary = await self._wiktionary(lookup.uk_word)
        elif direction is Direction.UK_RU:
            lookup.uk_word = normalize(word)
        return lookup

    # ------------------------------------------------------------ auto (Claude)
    async def translate_auto(
        self, text: str, user_id: int | None = None
    ) -> tuple[TranslationResult, Direction] | None:
        """Claude detects the language itself and translates into the other one.
        Returns None when the model is unavailable or its answer is unusable,
        so the caller can fall back to the heuristic chain."""
        if self.llm is None or self.llm_budget is None or not hasattr(self.llm, "translate_auto"):
            return None
        if not await self.llm_budget.try_consume(user_id):
            return None
        started = time.monotonic()
        try:
            raw = await self.llm.translate_auto(text)
        except ProviderError:
            self._metric("provider_error.llm")
            return None
        self._metric("source.llm", int((time.monotonic() - started) * 1000))
        try:
            cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
            data = json.loads(cleaned[cleaned.find("{") : cleaned.rfind("}") + 1])
            source = Lang(str(data["source"]).lower())
            out = str(data["translation"]).strip()
        except (ValueError, KeyError, TypeError):
            self._metric("provider_error.llm_parse")
            return None
        if source is Lang.UNKNOWN:
            return None
        direction = Direction.from_source(source)
        if not _plausible(out, text, direction):
            return None
        return TranslationResult(text=out, source=Source.LLM, metadata={"model": self.llm.model}), direction

    # --------------------------------------------------------------- phrases
    async def translate_phrase(
        self, text: str, direction: Direction, user_id: int | None = None
    ) -> TranslationResult:
        if direction is Direction.AUTO:
            raise ValueError("direction must be explicit")
        result = await self._external(text, direction, user_id)
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
                if _plausible(out, "", Direction.RU_UK):
                    return TranslationResult(text=out, source=Source.LLM, metadata={"model": self.llm.model})
        result = await self._google(text, Direction.RU_UK) or await self._mymemory(text, Direction.RU_UK)
        if result is None:
            raise TranslationUnavailable()
        return result

    async def explain_word(self, word: str, user_id: int | None = None) -> WordExplanation:
        """Explain a word in Ukrainian instead of translating it into Russian."""
        if self.llm is None or self.llm_budget is None or not hasattr(self.llm, "explain_word"):
            raise ExplanationUnavailable("disabled")
        if not await self.llm_budget.try_consume(user_id):
            raise ExplanationUnavailable("budget")
        started = time.monotonic()
        try:
            raw = await self.llm.explain_word(word)
        except ProviderError:
            self._metric("provider_error.llm")
            raise ExplanationUnavailable("provider") from None
        self._metric("source.llm", int((time.monotonic() - started) * 1000))
        try:
            return parse_explanation(raw, word)
        except ExplanationParseError:
            self._metric("provider_error.llm_parse")
            raise ExplanationUnavailable("parse") from None
