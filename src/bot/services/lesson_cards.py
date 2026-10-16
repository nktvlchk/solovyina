"""Turn one lesson into deck cards. The model proposes the words; this module
decides whether they are safe to publish and how they look in the chat.

A failed check produces no cards. Callers must not write the deck in that case.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from bot.providers.base import ProviderError

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_RUSSIAN_ONLY = set("ыэёъ")
_TAG_RE = re.compile(r"^[а-яіїєґ0-9-]{1,24}$")
_ID_RE = re.compile(r"^([RV])(\d+)$")

MAX_CARDS = 12
MIN_CLASS_TEXT = 40


class LessonRejected(ValueError):
    """The model output must not be written into the deck."""


class LessonUnavailable(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class RuleDraft:
    title: str
    body: str
    wrong: str
    right: str
    tag: str


@dataclass(frozen=True)
class VocabDraft:
    title: str
    tag: str
    items: tuple[tuple[str, str], ...]


Draft = RuleDraft | VocabDraft


def _ukrainian(text: str) -> bool:
    return bool(text) and not any(char in _RUSSIAN_ONLY for char in text.lower())


def _short(value: object, limit: int) -> str:
    return " ".join(str(value).split())[:limit]


def _tag(value: object) -> str:
    raw = str(value or "").strip().lstrip("#").split()
    if not raw:
        return ""
    tag = raw[0].lower().replace("_", "-")
    if not _TAG_RE.fullmatch(tag):
        raise LessonRejected("bad tag")
    return tag


def _pair(wrong: object, right: object) -> tuple[str, str]:
    bad, good = _short(wrong, 160), _short(right, 160)
    if bool(bad) != bool(good):
        raise LessonRejected("example pair is incomplete")
    return bad, good


def parse_lesson(raw: str) -> tuple[Draft, ...]:
    cleaned = _FENCE_RE.sub("", (raw or "").strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise LessonRejected("no JSON object")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LessonRejected("invalid JSON") from exc
    cards = data.get("cards") if isinstance(data, dict) else None
    if not isinstance(cards, list) or not 1 <= len(cards) <= MAX_CARDS:
        raise LessonRejected("card count")
    drafts: list[Draft] = []
    for card in cards:
        if not isinstance(card, dict):
            raise LessonRejected("card shape")
        kind = str(card.get("type", "")).strip()
        title = _short(card.get("title", ""), 80)
        tag = _tag(card.get("tag", ""))
        if kind == "rule":
            body = _short(card.get("body", ""), 500)
            wrong, right = _pair(card.get("wrong", ""), card.get("right", ""))
            if not title or not body:
                raise LessonRejected("empty rule")
            draft: Draft = RuleDraft(title, body, wrong, right, tag)
        elif kind == "vocab":
            raw_items = card.get("items")
            if not isinstance(raw_items, list) or len(raw_items) != 5:
                raise LessonRejected("vocab needs 5 items")
            items: list[tuple[str, str]] = []
            for item in raw_items:
                if not isinstance(item, dict):
                    raise LessonRejected("vocab item")
                word, note = _short(item.get("word", ""), 60), _short(item.get("note", ""), 160)
                if not word or not note:
                    raise LessonRejected("empty vocab item")
                items.append((word, note))
            draft = VocabDraft(title, tag, tuple(items))
            if not title:
                raise LessonRejected("empty vocab")
        else:
            raise LessonRejected("unknown type")
        visible = render_visible(draft)
        if not _ukrainian(visible):
            raise LessonRejected("not ukrainian")
        drafts.append(draft)
    return tuple(drafts)


def render_visible(draft: Draft) -> str:
    """The chat text. The card id is not included; the deck file stores it separately."""
    if isinstance(draft, RuleDraft):
        lines = [f"🧠 {draft.title}", "", draft.body]
        if draft.wrong:
            lines += ["", f"❌ {draft.wrong}", f"✅ {draft.right}"]
        lines += ["", "#правило" + (f" #{draft.tag}" if draft.tag else "")]
        return "\n".join(lines)
    lines = [f"📚 {draft.title}", ""]
    lines.extend(f"{index}. {word} — {note}" for index, (word, note) in enumerate(draft.items, 1))
    lines += ["", "#слова" + (f" #{draft.tag}" if draft.tag else "")]
    return "\n".join(lines)


def _max_number(existing: list[str], prefix: str) -> int:
    numbers = []
    for item in existing:
        match = _ID_RE.fullmatch(item)
        if match and match.group(1) == prefix:
            numbers.append(int(match.group(2)))
    return max(numbers, default=0)


def assign_ids(existing: list[str], drafts: tuple[Draft, ...]) -> list[tuple[str, str]]:
    """(id, visible text) continuing R and V numbers already in the deck."""
    rule_n = _max_number(existing, "R")
    vocab_n = _max_number(existing, "V")
    assigned: list[tuple[str, str]] = []
    for draft in drafts:
        if isinstance(draft, RuleDraft):
            rule_n += 1
            card_id = f"R{rule_n:02d}"
        else:
            vocab_n += 1
            card_id = f"V{vocab_n:02d}"
        assigned.append((card_id, render_visible(draft)))
    return assigned


async def request_drafts(
    llm, budget, user_id: int, metrics=None, *, text="", data=b"", media_type="", caption=""
):
    if llm is None or budget is None or not hasattr(llm, "lesson_cards"):
        raise LessonUnavailable("disabled")
    if not await budget.try_consume(user_id):
        raise LessonUnavailable("budget")
    try:
        if data:
            raw = await llm.lesson_media(data, media_type, caption)
        else:
            raw = await llm.lesson_cards(text)
    except ProviderError:
        if metrics is not None:
            metrics.incr("provider_error.llm")
        raise LessonUnavailable("provider") from None
    try:
        return parse_lesson(raw)
    except LessonRejected:
        if metrics is not None:
            metrics.incr("provider_error.llm_parse")
        raise LessonUnavailable("parse") from None
