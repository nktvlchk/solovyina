"""Read and update the private class deck.

The deck file stays in a secret gist. This process downloads it, inserts
cards at the next day that has not gone out yet, and uploads the result.
Card text from the model is not written to SQLite; only the ids of the
last batch are kept, so /deck_undo can remove them.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from bot.services.lesson_cards import Draft, assign_ids
from bot.storage.repositories import DeckBatchRepository


class DeckError(Exception):
    """The deck was not changed."""


def _drip():
    """drip.py lives at the repo root and is not inside the bot package."""
    root = str(Path(__file__).resolve().parents[3])
    if root not in sys.path:
        sys.path.append(root)
    import drip

    return drip


def insertion_index(start: date, today: date, size: int) -> int:
    """Where a new batch starts.

    Index 0 is introduced on `start`. Everything before the returned index
    has already had its first day, so those cards stay put. Later cards
    move back by the size of the batch. If the schedule has already passed
    the end of the deck, the batch is appended.
    """
    upcoming = (today - start).days + 1
    if upcoming < 0:
        return 0
    return min(upcoming, size)


def replace_cards(document: str, cards) -> str:
    start, end = "<!-- CARDS_START -->", "<!-- CARDS_END -->"
    if start not in document or end not in document:
        raise DeckError("markers")
    head, rest = document.split(start, 1)
    _, tail = rest.split(end, 1)
    body = "\n\n---\n\n".join(f"<!-- {card.id} -->\n{card.text.strip()}" for card in cards)
    return f"{head}{start}\n\n{body}\n\n{end}{tail}"


def load_cards(document: str):
    try:
        return _drip().parse_deck(document)
    except Exception as exc:
        raise DeckError("unreadable deck") from exc


@dataclass(frozen=True)
class Added:
    ids: tuple[str, ...]
    index: int
    first_day: date
    total: int


class GistDeck:
    """GitHub gist read/write. The token is sent only as a header."""

    def __init__(
        self, token: str, gist_id: str, filename: str = "deck.md", client: httpx.AsyncClient | None = None
    ):
        self._token = token
        self._gist_id = gist_id
        self._filename = filename
        self._client = client

    async def read(self) -> str:
        payload = await self._request("GET", f"https://api.github.com/gists/{self._gist_id}")
        files = payload.get("files") if isinstance(payload, dict) else None
        if not isinstance(files, dict) or not files:
            raise DeckError("gist has no files")
        chosen = files.get(self._filename)
        if chosen is None and len(files) == 1:
            chosen = next(iter(files.values()))
        content = chosen.get("content") if isinstance(chosen, dict) else None
        if not isinstance(chosen, dict) or chosen.get("truncated") or not isinstance(content, str):
            raise DeckError("gist file is unusable")
        return content

    async def write(self, text: str) -> None:
        await self._request(
            "PATCH",
            f"https://api.github.com/gists/{self._gist_id}",
            {"files": {self._filename: {"content": text}}},
        )

    async def _request(self, method: str, url: str, payload: dict | None = None) -> dict:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "solovyina-bot",
        }
        try:
            if self._client is not None:
                response = await self._client.request(method, url, headers=headers, json=payload)
            else:
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.request(method, url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise DeckError(type(exc).__name__) from None
        if response.status_code >= 400:
            raise DeckError(f"gist {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            raise DeckError("gist response") from None
        return body if isinstance(body, dict) else {}


class DeckEditor:
    def __init__(self, store, batches: DeckBatchRepository, start: date, tz: ZoneInfo):
        self.store = store
        self.batches = batches
        self.start = start
        self.tz = tz
        self._lock = asyncio.Lock()

    async def add(self, drafts: tuple[Draft, ...], *, today: date | None = None) -> Added:
        if not drafts:
            raise DeckError("empty")
        async with self._lock:
            document = await self.store.read()
            cards = load_cards(document)
            day = today or datetime.now(self.tz).date()
            index = insertion_index(self.start, day, len(cards))
            fresh = []
            for card_id, text in assign_ids([card.id for card in cards], drafts):
                fresh.append(_drip().Card(card_id, text))
            updated = cards[:index] + fresh + cards[index:]
            await self.store.write(replace_cards(document, updated))
            ids = tuple(card.id for card in fresh)
            self.batches.set(list(ids))
            return Added(ids, index, self.start + timedelta(days=index), len(updated))

    async def undo(self) -> tuple[str, ...]:
        async with self._lock:
            ids = self.batches.get()
            if not ids:
                return ()
            wanted = set(ids)
            document = await self.store.read()
            cards = load_cards(document)
            kept = [card for card in cards if card.id not in wanted]
            removed = tuple(card.id for card in cards if card.id in wanted)
            if removed and not kept:
                raise DeckError("empty")
            if removed:
                await self.store.write(replace_cards(document, kept))
            self.batches.clear()
            return removed

    async def status(self, *, today: date | None = None) -> tuple[int, int, int, date, list[str]]:
        """(day number, deck size, next index, first day of a new batch, last ids)."""
        cards = load_cards(await self.store.read())
        day = today or datetime.now(self.tz).date()
        number = (day - self.start).days
        index = insertion_index(self.start, day, len(cards))
        return number, len(cards), index, self.start + timedelta(days=index), self.batches.get()
