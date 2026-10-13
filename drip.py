#!/usr/bin/env python3
"""Spaced-repetition drip for a Ukrainian class chat.

Posts one message a day into a Telegram chat, at a time drawn at random
inside a daily window (08:00-20:00 by default). Stateless: both the time
of day and the cards that go out are pure functions of the calendar, so
restarts and redeploys never shift, lose or duplicate a send. Stdlib only.

Card i (0-based, in deck order) is introduced on day i and then repeats
after 4, 10, 21 and 45 days. Everything due on a day goes out together in
a single message, so the chat gets exactly one notification a day.

Deck sources (first one set wins):
  DRIP_DECK_URL   URL of a deck file, e.g. the raw URL of a secret GitHub
                  gist. Keeps class material out of the public repository.
                  Re-downloaded before each slot; the last good copy is
                  reused if the download fails.
  DRIP_DECK       local path (default: deck.json next to this file)

Deck formats:
  * Markdown: cards between <!-- CARDS_START --> and <!-- CARDS_END -->,
    separated by a line with "---". Each card carries an id and is otherwise
    a ready message. Preferred: the id on its own comment line, which is
    stripped before sending:
        <!-- R03 -->
        🧠 Title
    Legacy, still accepted: the id inside the visible first line,
    "🧠 R01 · Title" or "📚 V01 · Title".
  * JSON: {"cards": [{"id", "type", "title", "body", "example"}]}.

Other env vars:
  TELEGRAM_BOT_TOKEN (or BOT_TOKEN / TELEGRAM_TOKEN)  bot token (required)
  DRIP_CHAT_ID    target chat id, e.g. -100XXXXXXXXXX (required)
  DRIP_START      date of the first send, YYYY-MM-DD (default: 2026-10-08)
  DRIP_WINDOW     local window for the daily send (default: 08:00-20:00).
                  The minute inside it is drawn from the date, so every day
                  gets a different time and every process agrees on it.
  DRIP_SEED       optional string mixed into that draw: change it to reshuffle
                  the times without touching the schedule of the cards
  DRIP_TZ         IANA timezone (default: Europe/Lisbon)
  DRIP_SEND_NOW   if 1, send today's cards once at boot and exit
"""

from __future__ import annotations

import hashlib
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

# Intervals in days: today, +4d, +10d, +21d, +45d.
REPEAT_OFFSETS = [0, 4, 10, 21, 45]
# When several cards fall due on the same day, send at most this many. The
# order below drops the 4-day review first — it is the cheapest to skip.
MAX_PER_MESSAGE = 3
PRIORITY = [0, 45, 21, 10, 4]
DEFAULT_WINDOW = "08:00-20:00"

SEPARATOR = "\n\n➖➖➖\n\n"
TELEGRAM_LIMIT = 4096

_MD_HEADER = re.compile(r"^\S+\s+([RV]\d+)\s+·", re.UNICODE)
# Preferred form: the id sits on its own comment line and is never sent to the chat.
_MD_ID_COMMENT = re.compile(r"^<!--\s*([A-Za-z0-9][A-Za-z0-9_.-]*)\s*-->[ \t]*\n", re.UNICODE)


@dataclass(frozen=True)
class Card:
    id: str
    text: str  # plain text, escaped at render time


@dataclass(frozen=True)
class Config:
    token: str
    chat_id: str
    tz: ZoneInfo
    window: tuple[int, int]  # minutes after midnight, start <= end
    seed: str
    start: date
    deck_url: str | None
    deck_path: Path


def parse_window(raw: str) -> tuple[int, int]:
    """'08:00-20:00' -> (480, 1200), in minutes after midnight."""
    try:
        first, last = (part.strip() for part in raw.split("-", 1))
        start, end = (int(h) * 60 + int(m) for h, m in (part.split(":") for part in (first, last)))
    except ValueError:
        raise ValueError(f"DRIP_WINDOW must look like 08:00-20:00, got {raw!r}") from None
    if not 0 <= start <= end < 24 * 60:
        raise ValueError(f"DRIP_WINDOW must be a same-day window inside 00:00-23:59, got {raw!r}")
    return start, end


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
    if env.get("DRIP_SLOTS"):
        log.warning("DRIP_SLOTS is no longer used: one message a day goes out inside DRIP_WINDOW")
    return Config(
        token=token,
        chat_id=chat_id,
        tz=ZoneInfo(env.get("DRIP_TZ", "Europe/Lisbon")),
        window=parse_window(env.get("DRIP_WINDOW") or DEFAULT_WINDOW),
        seed=env.get("DRIP_SEED", ""),
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
        comment = _MD_ID_COMMENT.match(chunk)
        if comment:
            card_id, text = comment.group(1), chunk[comment.end() :].strip()
            if not text:
                raise ValueError(f"card {card_id} has no text after the id comment")
        else:
            header = _MD_HEADER.match(chunk)  # legacy form: id inside the visible header
            if not header:
                raise ValueError(f"card without an id header: {chunk[:40]!r}")
            card_id, text = header.group(1), chunk
        cards.append(Card(id=card_id, text=text))
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


def slot_minute(config: Config, day: date) -> int:
    """The day's send time, in minutes after midnight.

    Drawn from the date (and DRIP_SEED), not from random(): the same day
    always resolves to the same minute, so a restart cannot move it.
    """
    start, end = config.window
    digest = hashlib.sha256(f"{config.seed}|{day.isoformat()}".encode()).digest()
    return start + int.from_bytes(digest[:8], "big") % (end - start + 1)


def slot_datetime(config: Config, day: date) -> datetime:
    minute = slot_minute(config, day)
    return datetime(day.year, day.month, day.day, minute // 60, minute % 60, tzinfo=config.tz)


def slot_number(config: Config, day: date) -> int:
    """Day counter since START: day 0 introduces the first card."""
    return (day - config.start).days


def next_slot(config: Config, now: datetime) -> tuple[datetime, int]:
    """The next daily send strictly after `now`, with its day number."""
    for offset in (0, 1):
        day = now.date() + timedelta(days=offset)
        moment = slot_datetime(config, day)
        if moment > now:
            return moment, slot_number(config, day)
    raise RuntimeError("no upcoming slot found")


def due_cards(cards: list[Card], number: int) -> list[tuple[Card, int]]:
    """Cards due on day `number`, paired with the offset that scheduled them."""
    picked = []
    for offset in PRIORITY:
        position = number - offset
        if 0 <= position < len(cards):
            picked.append((cards[position], offset))
    return picked[:MAX_PER_MESSAGE]


def render_card(card: Card, offset: int) -> str:
    text = html.escape(card.text, quote=False)
    return text if offset == 0 else f"🔁 <i>повторення</i>\n{text}"


def render_message(picked: list[tuple[Card, int]]) -> tuple[str, list[str]]:
    """One message a day: everything due, in priority order, that fits.

    Returns the message and the ids left out because of Telegram's limit —
    `picked` is already sorted by PRIORITY, so the cheapest reviews go first.
    """
    text, dropped = "", []
    for card, offset in picked:
        part = render_card(card, offset)
        candidate = f"{text}{SEPARATOR}{part}" if text else part
        if len(candidate) <= TELEGRAM_LIMIT:
            text = candidate
        elif text:
            dropped.append(card.id)
        else:
            text = part[:TELEGRAM_LIMIT]
    return text, dropped


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
        log.info("day %s: nothing due", number)
        return
    message, dropped = render_message(picked)
    if dropped:
        log.warning("day %s: dropped for length: %s", number, ", ".join(dropped))
    ids = ", ".join(card.id for card, _ in picked if card.id not in dropped)
    if send(config, message):
        log.info("day %s: sent %s", number, ids)
    else:
        log.error("day %s: delivery failed: %s", number, ids)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    config = load_config()
    source = DeckSource(config)
    cards = source.load()
    start, end = config.window
    log.info(
        "deck: %s cards from %s | start %s | one message a day in %02d:%02d-%02d:%02d | tz %s",
        len(cards),
        "URL" if config.deck_url else config.deck_path.name,
        config.start,
        start // 60,
        start % 60,
        end // 60,
        end % 60,
        config.tz.key,
    )
    check_chat(config)

    if os.environ.get("DRIP_SEND_NOW") == "1":
        now = datetime.now(config.tz)
        number = max(0, slot_number(config, now.date()))
        log.info("DRIP_SEND_NOW: sending day %s once", number)
        run_slot(config, cards, number)
        return

    while True:
        moment, number = next_slot(config, datetime.now(config.tz))
        wait = (moment - datetime.now(config.tz)).total_seconds()
        log.info("next send (day %s) at %s (in %.0f min)", number, moment.isoformat(), wait / 60)
        while wait > 0:
            time.sleep(min(wait, 300))  # wake periodically so clock changes are picked up
            wait = (moment - datetime.now(config.tz)).total_seconds()
        cards = source.load()  # pick up deck edits without a restart
        run_slot(config, cards, number)
        time.sleep(90)  # never fire the same day twice


if __name__ == "__main__":
    main()
