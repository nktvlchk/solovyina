"""Local dictionary built from data/dictionary.tsv (see data/README.md)."""

from __future__ import annotations

import csv
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from bot.models.translation import DictionaryEntry

_APOSTROPHES = "’ʼ‘`´"

UK_SUFFIXES = sorted(
    [
        "ів",
        "ям",
        "ях",
        "ами",
        "ями",
        "ою",
        "ею",
        "єю",
        "ові",
        "еві",
        "єві",
        "ом",
        "ем",
        "єм",
        "їв",
        "ей",
        "у",
        "ю",
        "і",
        "ї",
        "и",
        "е",
        "є",
        "о",
        "а",
        "я",
        "ти",
        "ть",
        "ую",
        "юю",
        "єш",
        "еш",
        "ає",
        "емо",
        "ємо",
        "ете",
        "єте",
        "ють",
        "ять",
        "ла",
        "ло",
        "ли",
        "в",
        "ся",
        "сь",
    ],
    key=len,
    reverse=True,
)
UK_ENDINGS = ["", "а", "я", "о", "е", "і", "и", "ь", "й", "ти", "ка", "ок", "ий", "ій"]
RU_SUFFIXES = sorted(
    [
        "ами",
        "ями",
        "ов",
        "ев",
        "ей",
        "ом",
        "ем",
        "ой",
        "ам",
        "ям",
        "ах",
        "ях",
        "у",
        "ю",
        "а",
        "я",
        "е",
        "и",
        "ы",
        "о",
        "ть",
        "ся",
        "ую",
        "ешь",
        "ет",
        "ете",
        "ют",
        "ят",
        "ал",
        "ала",
        "ало",
        "али",
        "ил",
        "ила",
        "ило",
        "или",
    ],
    key=len,
    reverse=True,
)
RU_ENDINGS = ["", "а", "я", "о", "е", "и", "ь", "й", "ть", "ок", "ка", "ый", "ий", "ой"]


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    for ch in _APOSTROPHES:
        text = text.replace(ch, "'")
    return " ".join(text.lower().strip(' .,!?;:«»"()').split())


class Dictionary:
    def __init__(self, entries: list[DictionaryEntry]):
        self._by_uk: dict[str, list[DictionaryEntry]] = defaultdict(list)
        self._by_ru: dict[str, list[DictionaryEntry]] = defaultdict(list)
        for e in entries:
            self._by_uk[e.uk].append(e)
            for part in re.split(r"[;,/]", e.ru):
                key = normalize(part)
                if key:
                    self._by_ru[key].append(e)
                    # Russian ё is often typed as е.
                    if "ё" in key:
                        self._by_ru[key.replace("ё", "е")].append(e)

    @classmethod
    def load(cls, path: Path) -> Dictionary:
        with path.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh, delimiter="\t", quoting=csv.QUOTE_NONE)
            entries = [
                DictionaryEntry(
                    uk=row["uk"],
                    ru=row["ru"],
                    pos=row["pos"],
                    gender=row["gender"],
                    source=row["source"],
                    example=row["example"],
                )
                for row in reader
            ]
        return cls(entries)

    def __len__(self) -> int:
        return len(self._by_uk)

    def has_uk(self, word: str) -> bool:
        return normalize(word) in self._by_uk

    def has_ru(self, word: str) -> bool:
        return normalize(word) in self._by_ru

    def lookup_uk(self, word: str) -> tuple[list[DictionaryEntry], str | None]:
        """Returns (entries, matched_form). matched_form is set when the hit
        came from stripping an inflectional ending (an approximate match)."""
        return _lookup(normalize(word), self._by_uk, UK_SUFFIXES, UK_ENDINGS)

    def lookup_ru(self, word: str) -> tuple[list[DictionaryEntry], str | None]:
        return _lookup(normalize(word), self._by_ru, RU_SUFFIXES, RU_ENDINGS)


def _lookup(
    key: str,
    index: dict[str, list[DictionaryEntry]],
    suffixes: list[str],
    endings: list[str],
) -> tuple[list[DictionaryEntry], str | None]:
    if not key:
        return [], None
    if key in index:
        return list(_unique(index[key])), None
    if " " in key:
        return [], None
    for suffix in suffixes:
        if key.endswith(suffix) and len(key) > len(suffix) + 2:
            stem = key[: -len(suffix)]
            for ending in endings:
                candidate = stem + ending
                if candidate != key and candidate in index:
                    return list(_unique(index[candidate])), candidate
    return [], None


def _unique(entries: list[DictionaryEntry]):
    seen = set()
    for e in entries:
        k = (e.uk, e.ru, e.pos)
        if k not in seen:
            seen.add(k)
            yield e
