from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Source(StrEnum):
    """Where a result ACTUALLY came from (never the planned provider)."""

    DICTIONARY = "dictionary"  # local data/dictionary.tsv
    WIKTIONARY = "wiktionary"
    MYMEMORY = "mymemory"
    GOOGLE = "google"
    LLM = "llm"


SOURCE_LABELS = {
    Source.DICTIONARY: "локальний словник",
    Source.WIKTIONARY: "Wiktionary (CC BY-SA)",
    Source.MYMEMORY: "MyMemory",
    Source.GOOGLE: "Google Translate",
    Source.LLM: "мовна модель (може помилятися)",
}


@dataclass(frozen=True)
class TranslationResult:
    text: str
    source: Source
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DictionaryEntry:
    uk: str
    ru: str
    pos: str = ""
    gender: str = ""
    source: str = ""
    example: str = ""


@dataclass(frozen=True)
class WiktionaryEntry:
    word: str
    pos: str
    definitions: tuple[str, ...]
    pronunciation: str = ""
    synonyms: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    url: str = ""
