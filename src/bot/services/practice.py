"""Daily practice: up to five typed answers from cards that are already due.

A correct answer is an exact match after the same normalization the dictionary
uses. Alternatives written with ``;`` or ``/`` all count. The typed text is
never stored.
"""

from __future__ import annotations

import re

from bot.services.dictionary import normalize
from bot.storage.repositories import Card

DAILY_SIZE = 5
_SPLIT = re.compile(r"[;/]")


def accepted_answers(back: str) -> set[str]:
    keys = {normalize(part) for part in _SPLIT.split(back)}
    keys.add(normalize(back))
    return {key for key in keys if key}


def is_correct(given: str, back: str) -> bool:
    key = normalize(given)
    return bool(key) and key in accepted_answers(back)


def choose_daily(
    due: list[Card],
    types: dict[int, str],
    weak: list[str],
    limit: int = DAILY_SIZE,
) -> list[Card]:
    """Prefer the error types this user confirms most often.

    Saved words and unknown types come after those. Inside one type, the
    older due date goes first. The caller passes only cards that are due.
    """
    rank = {name: index for index, name in enumerate(weak)}
    last = len(rank)

    def sort_key(card: Card) -> tuple:
        return (rank.get(types.get(card.id), last), card.due_at, card.id)

    return sorted(due, key=sort_key)[:limit]
