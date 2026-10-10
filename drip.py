#!/usr/bin/env python3
"""Spaced-repetition drip for a Ukrainian class chat.

Posts study cards into a Telegram chat at fixed times of day. Stateless:
which cards go out in a slot is a pure function of the calendar, so
restarts and redeploys never lose or duplicate the schedule. Stdlib only.

Card i (0-based, in deck order) is introduced in slot i and then repeats
after 2, 6, 15 and 35 days (with three slots a day).

Deck sources (first one set wins):
  DRIP_DECK_URL   URL of a deck file, e.g. the raw URL of a secret GitHub
                  gist. Keeps class material out of the public repository.
                  Re-downloaded before each slot; the last good copy is
                  reused if the download fails.
  DRIP_DECK       local path (default: deck.json next to this file)

Deck formats:
  * Markdown: cards between <!-- CARDS_START --> and <!-- CARDS_END -->,
    separated by a line with "---"; each card is one ready message whose
    first line looks like "🧠 R01 · Title" or "📚 V01 · Title".
  * JSON: {"cards": [{"id", "type", "title", "body", "example"}]}.

Other env vars:
  TELEGRAM_BOT_TOKEN (or BOT_TOKEN / TELEGRAM_TOKEN)  bot token (required)
  DRIP_CHAT_ID    target chat id, e.g. -100XXXXXXXXXX (required)
  DRIP_START      date of the first slot, YYYY-MM-DD (default: 2026-10-08)
  DRIP_SLOTS      comma-separated local times (default: 09:00,14:00,20:00)
  DRIP_TZ         IANA timezone (default: Europe/Lisbon)
  DRIP_SEND_NOW   if 1, send the current slot once at boot and exit
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("drip")

API = "https://api.telegram.org/bot{token}/{method}"

# Intervals expressed in slots. With 3 slots a day: now, +2d, +6d, +15d, +35d.
REPEAT_OFFSETS = [0, 6, 18, 45, 105]
# When several cards land in the same slot, send at most this many. The
# order below drops the 2-day review first — it is the cheapest to skip.
MAX_PER_MESSAGE = 3
PRIORITY = [0, 105, 45, 18, 6]

SEPARATOR = "\n\n➖➖➖\n\n"
TELEGRAM_LIMIT = 4096

_MD_HEADER = re.compile(r"^\S+\s+([RV]\d+)\s+·", re.UNICODE)


@dataclass(frozen=True)
class Card:
    id: str
    text: str  # plain text, escaped at render time


@dataclass(frozen=True)
class Config:
    token: str
    chat_id: str
    tz: ZoneInfo
    slots: list[tuple[int, int]]
    start: date
    deck_url: str | None
    deck_path: Path


def parse_slots(raw: str) -> list[tuple[int, int]]:
    slots = []
    for part in raw.split(","):
        hour, minute = part.strip().split(":")
        slots.append((int(hour), int(minute)))
    return sorted(slots)


def load_config(env: dict[str, str] | None = None) -> Config:
    env = dict(os.environ) if env is None else env
    token = next(
        (
            env[n].strip()
            for n in ("TELEGRAM_BOT_TOKEN", "BOT_TOKEN", "TELEGRAM_TOKEN", "TG_TOKEN")
            if env.get(n)
        ),
        "",
    )
    if not token:
        sys.exit("No bot token: set TELEGRAM_BOT_TOKEN in the environment.")
    chat_id = env.get("DRIP_CHAT_ID", "").strip()
    if not chat_id:
        sys.exit("No target chat: set DRIP_CHAT_ID in the environment.")
    return Config(
        token=token,
        chat_id=chat_id,
        tz=ZoneInfo(env.get("DRIP_TZ", "Europe/Lisbon")),
        slots=parse_slots(env.get("DRIP_SLOTS", "09:00,14:00,20:00")),
        start=date.fromisoformat(env.get("DRIP_START", "2026-10-08")),
        deck_url=(env.get("DRIP_DECK_URL") or "").strip() or None,
        deck_path=Path(env.get("DRIP_DECK") or Path(__file__).with_name("deck.json")),
    )


# ------------------------------------------------------------------ decks


def parse_markdown_deck(text: str) -> list[Card]:
    if "<!-- CARDS_START -->" not in text:
        raise ValueError("markdown deck has no <!-- CARDS_START --> marker")
    body = text.split("<!-- CARDS_START -->", 1)[1].split("<!-- CARDS_END -->", 1)[0]
    cards = []
    for chunk in re.split(r"\n-{3,}\n", "\n" + body + "\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        match = _MD_HEADER.match(chunk)
        if not match:
            raise ValueError(f"card without an id header: {chunk[:40]!r}")
        cards.append(Card(id=match.group(1), text=chunk))
    ids = [c.id for c in cards]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate card ids")
    return cards


def parse_json_deck(text: str) -> list[Card]:
    cards = []
    for i, card in enumerate(json.loads(text)["cards"], 1):
        kind = "📘 ПРАВИЛО" if card.get("type") == "rule" else "🔤 СЛОВО"
        lines = [kind, card["title"], card["body"]]
        if card.get("example"):
            lines.append(card["example"])
        cards.append(Card(id=str(card.get("id", i)), text="\n".join(lines)))
    return cards


def parse_deck(text: str) -> list[Card]:
    stripped = text.lstrip()
    cards = parse_json_deck(text) if stripped.startswith("{") else parse_markdown_deck(text)
    if not cards:
        raise ValueError("deck is empty")
    return cards


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "solovyina-drip/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


class DeckSource:
    def __init__(self, config: Config, fetch=fetch_text):
        self.config = config
        self._fetch = fetch
        self._last: list[Card] | None = None

    def load(self) -> list[Card]:
        try:
            if self.config.deck_url:
                text = self._fetch(self.config.deck_url)
            else:
                text = self.config.deck_path.read_text(encoding="utf-8")
            self._last = parse_deck(text)
        except Exception as error:
            if self._last is None:
                raise
            log.error("deck reload failed, keeping the previous copy: %s", type(error).__name__)
        return self._last


# --------------------------------------------------------------- schedule


def slot_datetime(config: Config, day: date, index: int) -> datetime:
    hour, minute = config.slots[index]
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=config.tz)


def slot_number(config: Config, day: date, index: int) -> int:
    """Global slot counter since the first slot on START."""
    return (day - config.start).days * len(config.slots) + index


def next_slot(config: Config, now: datetime) -> tuple[datetime, int]:
    """The first slot strictly after `now`, with its global slot number."""
    for offset in (0, 1):
        day = now.date() + timedelta(days=offset)
        for index in range(len(config.slots)):
            moment = slot_datetime(config, day, index)
            if moment > now:
                return moment, slot_number(config, day, index)
    raise RuntimeError("no upcoming slot found")


def due_cards(cards: list[Card], number: int) -> list[tuple[Card, int]]:
    """Cards due in slot `number`, paired with the offset that scheduled them."""
    picked = []
    for offset in PRIORITY:
        position = number - offset
        if 0 <= position < len(cards):
            picked.append((cards[position], offset))
    return picked[:MAX_PER_MESSAGE]


def render_card(card: Card, offset: int) -> str:
    text = html.escape(card.text, quote=False)
    return text if offset == 0 else f"🔁 <i>повторення</i>\n{text}"


def render_messages(picked: list[tuple[Card, int]]) -> list[str]:
    """Join cards into as few messages as fit Telegram's limit."""
    messages: list[str] = []
    for card, offset in picked:
        part = render_card(card, offset)
        if messages and len(messages[-1]) + len(SEPARATOR) + len(part) <= TELEGRAM_LIMIT:
            messages[-1] += SEPARATOR + part
        else:
            messages.append(part[:TELEGRAM_LIMIT])
    return messages


# --------------------------------------------------------------- telegram


def send(config: Config, text: str) -> bool:
    payload = json.dumps(
        {"chat_id": config.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    ).encode("utf-8")
    url = API.format(token=config.token, method="sendMessage")
    for attempt in range(1, 4):
        request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                json.load(response)
            return True
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", "replace")[:300]
            log.error("sendMessage failed (attempt %s): %s %s", attempt, error.code, body)
            if error.code in (400, 403):
                return False  # bad chat id or bot not in the chat — retrying won't help
        except Exception as error:  # network hiccup
            log.error("sendMessage failed (attempt %s): %s", attempt, type(error).__name__)
        time.sleep(5 * attempt)
    return False


def check_chat(config: Config) -> None:
    url = API.format(token=config.token, method="getChat") + f"?chat_id={config.chat_id}"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            chat = json.load(response)["result"]
        log.info("target chat: %s", chat.get("title") or chat.get("username"))
    except urllib.error.HTTPError as error:
        log.error("cannot reach the chat: HTTP %s — add the bot to the chat or fix DRIP_CHAT_ID", error.code)
    except Exception as error:
        log.error("cannot reach the chat: %s", type(error).__name__)


def run_slot(config: Config, cards: list[Card], number: int) -> None:
    picked = due_cards(cards, number)
    if not picked:
        log.info("slot %s: nothing due", number)
        return
    ids = ", ".join(card.id for card, _ in picked)
    ok = all(send(config, message) for message in render_messages(picked))
    if ok:
        log.info("slot %s: sent %s", number, ids)
    else:
        log.error("slot %s: delivery failed: %s", number, ids)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    config = load_config()
    source = DeckSource(config)
    cards = source.load()
    log.info(
        "deck: %s cards from %s | start %s | slots %s | tz %s",
        len(cards),
        "URL" if config.deck_url else config.deck_path.name,
        config.start,
        ",".join(f"{h:02d}:{m:02d}" for h, m in config.slots),
        config.tz.key,
    )
    check_chat(config)

    if os.environ.get("DRIP_SEND_NOW") == "1":
        now = datetime.now(config.tz)
        number = max(0, slot_number(config, now.date(), 0))
        log.info("DRIP_SEND_NOW: sending slot %s once", number)
        run_slot(config, cards, number)
        return

    while True:
        moment, number = next_slot(config, datetime.now(config.tz))
        wait = (moment - datetime.now(config.tz)).total_seconds()
        log.info("next slot %s at %s (in %.0f min)", number, moment.isoformat(), wait / 60)
        while wait > 0:
            time.sleep(min(wait, 300))  # wake periodically so clock changes are picked up
            wait = (moment - datetime.now(config.tz)).total_seconds()
        cards = source.load()  # pick up deck edits without a restart
        run_slot(config, cards, number)
        time.sleep(90)  # never fire the same slot twice


if __name__ == "__main__":
    main()
