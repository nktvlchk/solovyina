"""A validated reading of one image. The image itself is never stored."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_RUSSIAN_ONLY = set("ыэёъ")
MAX_ITEMS = 12


class PictureParseError(ValueError):
    """The model response is not a safe reading of the image."""


@dataclass(frozen=True)
class PictureItem:
    word: str
    note: str


@dataclass(frozen=True)
class PictureReading:
    summary: str
    items: tuple[PictureItem, ...]


def _ukrainian(text: str) -> bool:
    return not any(char in _RUSSIAN_ONLY for char in text.lower())


def _short(value: object, limit: int) -> str:
    return " ".join(str(value).split())[:limit]


def parse_picture(raw: str) -> PictureReading:
    cleaned = _FENCE_RE.sub("", raw.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise PictureParseError("no JSON object")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise PictureParseError("invalid JSON") from exc
    if not isinstance(data, dict):
        raise PictureParseError("top level is not an object")

    summary = _short(data.get("summary", ""), 500)
    raw_items = data.get("items", [])
    if not isinstance(raw_items, list):
        raise PictureParseError("items missing")
    items: list[PictureItem] = []
    for item in raw_items[:MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        word = _short(item.get("word", ""), 80)
        note = _short(item.get("note", ""), 200)
        if word and note:
            items.append(PictureItem(word, note))
    all_text = " ".join((summary, *(f"{item.word} {item.note}" for item in items)))
    if not summary or not _ukrainian(all_text):
        raise PictureParseError("unusable reading")
    return PictureReading(summary, tuple(items))
