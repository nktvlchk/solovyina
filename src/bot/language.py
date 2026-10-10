"""Ukrainian vs Russian detection.

Ukrainian and Russian share most Cyrillic letters, so detection is only a
*hint*. The user can always pick the direction explicitly. When the signals
are weak or contradictory the result is ``unknown`` and the bot asks.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class Lang(StrEnum):
    UK = "uk"
    RU = "ru"
    UNKNOWN = "unknown"


class Direction(StrEnum):
    """Translation direction chosen by the user."""

    AUTO = "auto"
    UK_RU = "uk-ru"
    RU_UK = "ru-uk"

    @property
    def source(self) -> Lang:
        return {Direction.UK_RU: Lang.UK, Direction.RU_UK: Lang.RU}.get(self, Lang.UNKNOWN)

    @property
    def target(self) -> Lang:
        return {Direction.UK_RU: Lang.RU, Direction.RU_UK: Lang.UK}.get(self, Lang.UNKNOWN)

    @classmethod
    def from_source(cls, lang: Lang) -> Direction:
        return {Lang.UK: cls.UK_RU, Lang.RU: cls.RU_UK}.get(lang, cls.AUTO)


@dataclass(frozen=True)
class Detection:
    lang: Lang
    confidence: float  # 0..1, a heuristic score, not a probability
    signals: tuple[str, ...] = ()


UK_LETTERS = set("іїєґ")
RU_LETTERS = set("ыэёъ")
_WORD_RE = re.compile(r"[a-zа-яіїєґёыэъ'’ʼ-]+", re.IGNORECASE)
# Apostrophe between letters is typical for Ukrainian (м'ясо, п'ять).
_UK_APOSTROPHE_RE = re.compile(r"[бпвмфрнк][’'ʼ][яюєї]", re.IGNORECASE)

# Frequent function words that exist in only one of the languages.
UK_WORDS = {
    "що",
    "це",
    "та",
    "або",
    "але",
    "як",
    "який",
    "яка",
    "які",
    "якщо",
    "тому",
    "бо",
    "щоб",
    "ще",
    "вже",
    "дуже",
    "мені",
    "тобі",
    "його",
    "її",
    "він",
    "вона",
    "вони",
    "ми",
    "ви",
    "я",
    "ти",
    "так",
    "ні",
    "дякую",
    "будь",
    "ласка",
    "привіт",
    "добре",
    "де",
    "коли",
    "чому",
    "хто",
    "чи",
    "ось",
}
RU_WORDS = {
    "что",
    "это",
    "или",
    "но",
    "как",
    "какой",
    "какая",
    "какие",
    "если",
    "потому",
    "чтобы",
    "еще",
    "уже",
    "очень",
    "мне",
    "тебе",
    "его",
    "ее",
    "он",
    "она",
    "они",
    "мы",
    "вы",
    "я",
    "ты",
    "да",
    "нет",
    "спасибо",
    "пожалуйста",
    "привет",
    "хорошо",
    "где",
    "когда",
    "почему",
    "кто",
    "ли",
    "вот",
}
# Words present in both lists carry no signal.
_SHARED = UK_WORDS & RU_WORDS
UK_WORDS -= _SHARED
RU_WORDS -= _SHARED


def detect_language(
    text: str,
    in_uk_dictionary: Callable[[str], bool] | None = None,
    in_ru_dictionary: Callable[[str], bool] | None = None,
) -> Detection:
    """Heuristic detection with several independent signals."""
    lowered = text.lower()
    words = _WORD_RE.findall(lowered)
    if not words:
        return Detection(Lang.UNKNOWN, 0.0, ("no-words",))

    uk = 0.0
    ru = 0.0
    signals: list[str] = []

    uk_letters = sum(1 for ch in lowered if ch in UK_LETTERS)
    ru_letters = sum(1 for ch in lowered if ch in RU_LETTERS)
    if uk_letters:
        uk += 2.0 + 0.5 * min(uk_letters, 4)
        signals.append("uk-letters")
    if ru_letters:
        ru += 2.0 + 0.5 * min(ru_letters, 4)
        signals.append("ru-letters")

    if _UK_APOSTROPHE_RE.search(lowered):
        uk += 1.5
        signals.append("uk-apostrophe")

    uk_hits = sum(1 for w in words if w in UK_WORDS)
    ru_hits = sum(1 for w in words if w in RU_WORDS)
    if uk_hits:
        uk += 1.0 * uk_hits
        signals.append("uk-words")
    if ru_hits:
        ru += 1.0 * ru_hits
        signals.append("ru-words")

    if in_uk_dictionary or in_ru_dictionary:
        only_uk = only_ru = 0
        for word in words:
            if len(word) < 3 and len(words) > 1:
                continue
            in_uk = bool(in_uk_dictionary and in_uk_dictionary(word))
            in_ru = bool(in_ru_dictionary and in_ru_dictionary(word))
            only_uk += in_uk and not in_ru
            only_ru += in_ru and not in_uk
        if only_uk:
            uk += 1.0 if len(words) == 1 else 0.6 * only_uk
            signals.append("uk-dictionary")
        if only_ru:
            ru += 1.0 if len(words) == 1 else 0.6 * only_ru
            signals.append("ru-dictionary")

    total = uk + ru
    if total == 0:
        return Detection(Lang.UNKNOWN, 0.0, tuple(signals) or ("no-signal",))
    margin = abs(uk - ru)
    confidence = round(min(1.0, margin / max(total, 2.0)), 2)
    if margin < 1.0 or confidence < 0.3:
        return Detection(Lang.UNKNOWN, confidence, tuple(signals))
    return Detection(Lang.UK if uk > ru else Lang.RU, confidence, tuple(signals))
