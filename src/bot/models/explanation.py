"""Validated dictionary-style explanation returned by the language model."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from bot.services.dictionary import normalize

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_RUSSIAN_ONLY = set("ыэёъ")


class ExplanationParseError(ValueError):
    """The model response is not a safe, usable dictionary entry."""


@dataclass(frozen=True)
class WordExplanation:
    word: str
    part_of_speech: str
    definitions: tuple[str, ...]
    example: str
    stress: str = ""
    synonyms: tuple[str, ...] = ()


def _ukrainian(text: str) -> bool:
    return not any(char in _RUSSIAN_ONLY for char in text.lower())


def _short(value: object, limit: int) -> str:
    return " ".join(str(value).split())[:limit]


def parse_explanation(raw: str, query: str) -> WordExplanation:
    cleaned = _FENCE_RE.sub("", raw.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ExplanationParseError("no JSON object")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ExplanationParseError("invalid JSON") from exc
    if not isinstance(data, dict):
        raise ExplanationParseError("top level is not an object")

    word = _short(data.get("word", query), 80)
    part = _short(data.get("part_of_speech", ""), 80)
    stress = _short(data.get("stress", ""), 80)
    example = _short(data.get("example", ""), 300)
    raw_definitions = data.get("definitions", [])
    raw_synonyms = data.get("synonyms", [])
    if not isinstance(raw_definitions, list) or not isinstance(raw_synonyms, list):
        raise ExplanationParseError("lists missing")
    definitions = tuple(definition for item in raw_definitions[:3] if (definition := _short(item, 300)))
    synonyms = tuple(synonym for item in raw_synonyms[:5] if (synonym := _short(item, 80)))
    all_text = " ".join((word, part, stress, example, *definitions, *synonyms))
    if (
        not word
        or not definitions
        or not example
        or normalize(word) == normalize(definitions[0])
        or not _ukrainian(all_text)
    ):
        raise ExplanationParseError("unusable explanation")
    return WordExplanation(
        word=word,
        part_of_speech=part,
        definitions=definitions,
        example=example,
        stress=stress,
        synonyms=synonyms,
    )
