"""Russian Wiktionary (ru.wiktionary.org): Ukrainian headwords with Russian
definitions, stress/IPA and examples.

Content license: CC BY-SA 4.0 — every reply that shows this data carries an
attribution line and a link to the page.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from urllib.parse import quote

import httpx

from bot.models.translation import WiktionaryEntry
from bot.providers.base import ProviderError, get_json

API_URL = "https://ru.wiktionary.org/w/api.php"
PAGE_URL = "https://ru.wiktionary.org/wiki/"

_POS_PATTERNS = (
    (r"\{\{(?:сущ|сущ uk)[ .-]?uk|существительное", "ім."),
    (r"\{\{(?:гл|глаг)[.-]uk|глагол", "дієсл."),
    (r"\{\{прил[ .-]uk|прилагательное", "прикм."),
    (r"\{\{(?:нар|нареч)[.-]uk|наречие", "присл."),
    (r"\{\{мест[.-]uk|местоимение", "займ."),
    (r"\{\{числ[.-]uk|числительное", "числ."),
    (r"\{\{предл[.-]uk|предлог", "прийм."),
    (r"\{\{союз[.-]uk|союз", "спол."),
    (r"\{\{част[.-]uk|частица", "частка"),
    (r"\{\{межд[.-]uk|междометие", "вигук"),
)


def clean_wikitext(text: str) -> str:
    text = re.sub(r"\{\{помета\|([^}]*)\}\}", r"(\1)", text)
    text = re.sub(r"\{\{выдел\|([^}]*)\}\}", r"\1", text)
    text = re.sub(r"\{\{итп\}\}", "и т. п.", text)
    text = re.sub(r"\{\{итд\}\}", "и т. д.", text)
    text = re.sub(r"\{\{[^|{}]*\|([^|{}]*?)(?:\|[^}]*)?\}\}", r"\1", text)
    text = re.sub(r"\{\{[^}]*\}\}", "", text)
    text = re.sub(r"\[\[[^|\]]*\|([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"'{2,3}", "", text)
    text = re.sub(r"<[^>]+>", "", text)  # stray HTML in wikitext
    return re.sub(r"\s+", " ", text).strip()


def parse_ukrainian_section(word: str, wikitext: str) -> WiktionaryEntry | None:
    match = re.search(r"=\s*\{\{-uk-\}\}\s*=|=\s*Украинский\s*=", wikitext)
    if not match:
        return None
    rest = wikitext[match.end() :]
    nxt = re.search(r"\n=\s*\{\{-[a-z]+-\}\}\s*=|\n=\s*[А-Я]", rest)
    section = rest[: nxt.start()] if nxt else rest

    pos = next((label for pat, label in _POS_PATTERNS if re.search(pat, section, re.I)), "")
    definitions = _definitions(section)
    if not definitions:
        return None
    return WiktionaryEntry(
        word=word,
        pos=pos,
        definitions=tuple(definitions[:3]),
        pronunciation=_pronunciation(section),
        synonyms=tuple(_list_section(section, "Синонимы")[:4]),
        examples=tuple(_examples(section)[:1]),
        url=PAGE_URL + quote(word),
    )


def _definitions(section: str) -> list[str]:
    m = re.search(r"={3,4}\s*Значение\s*={3,4}", section)
    if not m:
        return []
    after = section[m.end() :]
    nxt = re.search(r"={3,4}\s*\S", after)
    if nxt:
        after = after[: nxt.start()]
    out = []
    for line in after.split("\n"):
        line = line.strip()
        if line.startswith("#") and not line.startswith("##"):
            cleaned = clean_wikitext(line.lstrip("#").strip())
            if len(cleaned) > 1 and cleaned != "-":
                out.append(cleaned[:300])
    return out


def _pronunciation(section: str) -> str:
    m = re.search(r"={3,4}\s*Произношение\s*={3,4}(.*?)={3,4}", section, re.DOTALL)
    if not m:
        return ""
    ipa = re.search(r"\{\{МФА[^}]*\|([^}|]+)", m.group(1))
    if ipa:
        return ipa.group(1).strip()[:80]
    ipa2 = re.search(r"\[([^\]]*[ˈˌ][^\]]*)\]", m.group(1))
    return ipa2.group(1).strip()[:80] if ipa2 else ""


def _list_section(section: str, name: str) -> list[str]:
    m = re.search(rf"={{3,4}}\s*{name}\s*={{3,4}}(.*?)(?:={{3,4}}|\Z)", section, re.DOTALL)
    if not m:
        return []
    items = []
    for line in m.group(1).split("\n"):
        line = line.strip()
        if line.startswith("#") and not line.startswith("##"):
            item = clean_wikitext(line.lstrip("#").strip())
            if item and item != "-":
                items.append(item[:100])
    return items


def _examples(section: str) -> list[str]:
    out = []
    for m in re.finditer(r"\{\{пример\|([^}]*)\}\}", section):
        cleaned = clean_wikitext(m.group(1).split("|")[0])
        if len(cleaned) > 3:
            out.append(cleaned[:300])
    return out


class WiktionaryProvider:
    name = "wiktionary"

    def __init__(self, client: httpx.AsyncClient, cache_size: int = 2000):
        self._client = client
        self._cache: OrderedDict[str, WiktionaryEntry | None] = OrderedDict()
        self._cache_size = cache_size

    async def lookup(self, word: str) -> WiktionaryEntry | None:
        """None means "no Ukrainian entry". Raises ProviderError on failure."""
        word = word.strip().lower()
        if not word or len(word) > 60 or " " in word:
            return None
        if word in self._cache:
            self._cache.move_to_end(word)
            return self._cache[word]
        data = await get_json(
            self._client,
            self.name,
            API_URL,
            params={
                "action": "parse",
                "page": word,
                "format": "json",
                "prop": "wikitext",
                "redirects": "1",
            },
        )
        if not isinstance(data, dict):
            raise ProviderError(self.name, "unexpected response shape")
        if "error" in data:
            result = None
        else:
            wikitext = data.get("parse", {}).get("wikitext", {}).get("*", "")
            result = parse_ukrainian_section(word, wikitext) if wikitext else None
        self._cache[word] = result
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return result
