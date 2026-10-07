#!/usr/bin/env python3
"""Spaced-repetition drip for the Ukrainian class chat.

Posts cards from deck.json into a Telegram chat three times a day.

The schedule is stateless: which cards go out in a given slot is a pure
function of the calendar, so restarts, redeploys and crashes never lose
or duplicate the schedule. No database, no volume, stdlib only.

Card i (0-based, in deck order) is introduced in slot i and then repeats
after 2, 6, 15 and 35 days. Appending new cards to the end of deck.json
never disturbs the cards already scheduled.

Env vars:
  BOT_TOKEN / TELEGRAM_TOKEN / TELEGRAM_BOT_TOKEN  bot token (required)
  DRIP_CHAT_ID    target chat id, e.g. -100XXXXXXXXXX (required)
  DRIP_START      date of the first slot, YYYY-MM-DD (default: 2026-10-08)
  DRIP_SLOTS      comma-separated local times (default: 09:00,14:00,20:00)
  DRIP_TZ         IANA timezone (default: Europe/Lisbon)
  DRIP_DECK       path to the deck (default: deck.json next to this file)
  DRIP_SEND_NOW   if set to 1, send the current slot once at boot and exit
"""

import html
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("drip")

API = "https://api.telegram.org/bot{token}/{method}"

# Intervals expressed in slots. With 3 slots a day: now, +2d, +6d, +15d, +35d.
REPEAT_OFFSETS = [0, 6, 18, 45, 105]
# When several cards land in the same slot, send at most this many. The
# order below drops the 2-day review first — it is the cheapest to skip.
MAX_PER_MESSAGE = 3
PRIORITY = [0, 105, 45, 18, 6]

SEPARATOR = "\n\n➖➖➖\n\n"


def env_token() -> str:
    for name in ("BOT_TOKEN", "TELEGRAM_TOKEN", "TELEGRAM_BOT_TOKEN", "TG_TOKEN"):
        value = os.environ.get(name)
        if value:
            return value.strip()
    sys.exit("No bot token: set BOT_TOKEN (or TELEGRAM_TOKEN) in the environment.")


def parse_slots(raw: str) -> list[tuple[int, int]]:
    slots = []
    for part in raw.split(","):
        hour, minute = part.strip().split(":")
        slots.append((int(hour), int(minute)))
    return sorted(slots)


TOKEN = env_token()
CHAT_ID = os.environ.get("DRIP_CHAT_ID", "").strip()
TZ = ZoneInfo(os.environ.get("DRIP_TZ", "Europe/Lisbon"))
SLOTS = parse_slots(os.environ.get("DRIP_SLOTS", "09:00,14:00,20:00"))
START = date.fromisoformat(os.environ.get("DRIP_START", "2026-10-08"))
DECK_PATH = Path(os.environ.get("DRIP_DECK", Path(__file__).with_name("deck.json")))

if not CHAT_ID:
    sys.exit("No target chat: set DRIP_CHAT_ID in the environment.")


def load_deck() -> list[dict]:
    with DECK_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)["cards"]


def slot_datetime(day: date, index: int) -> datetime:
    hour, minute = SLOTS[index]
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def slot_number(day: date, index: int) -> int:
    """Global slot counter since the first slot on START."""
    return (day - START).days * len(SLOTS) + index


def next_slot(now: datetime) -> tuple[datetime, int]:
    """The first slot strictly after `now`, with its global slot number."""
    for offset in (0, 1):
        day = now.date() + timedelta(days=offset)
        for index in range(len(SLOTS)):
            moment = slot_datetime(day, index)
            if moment > now:
                return moment, slot_number(day, index)
    raise RuntimeError("no upcoming slot found")


def due_cards(cards: list[dict], number: int) -> list[tuple[dict, int]]:
    """Cards due in slot `number`, paired with the offset that scheduled them."""
    picked = []
    for offset in PRIORITY:
        position = number - offset
        if 0 <= position < len(cards):
            picked.append((cards[position], offset))
    return picked[:MAX_PER_MESSAGE]


def render_card(card: dict, offset: int) -> str:
    kind = "📘 ПРАВИЛО" if card.get("type") == "rule" else "🔤 СЛОВО"
    tag = "" if offset == 0 else "  ·  повторення"
    lines = [
        f"{kind}{tag}",
        f"<b>{html.escape(card['title'], quote=False)}</b>",
        html.escape(card["body"], quote=False),
    ]
    if card.get("example"):
        lines.append(f"<i>{html.escape(card['example'], quote=False)}</i>")
    return "\n".join(lines)


def render_message(picked: list[tuple[dict, int]]) -> str:
    return SEPARATOR.join(render_card(card, offset) for card, offset in picked)


def send(text: str) -> bool:
    payload = json.dumps(
        {
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")
    url = API.format(token=TOKEN, method="sendMessage")
    for attempt in range(1, 4):
        request = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
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
            log.error("sendMessage failed (attempt %s): %s", attempt, error)
        time.sleep(5 * attempt)
    return False


def check_chat() -> None:
    url = API.format(token=TOKEN, method="getChat") + f"?chat_id={CHAT_ID}"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            chat = json.load(response)["result"]
        log.info("target chat: %s (%s)", chat.get("title") or chat.get("username"), chat["id"])
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")[:300]
        log.error("cannot reach chat %s: %s %s", CHAT_ID, error.code, body)
        log.error("add the bot to the chat, or fix DRIP_CHAT_ID")
    except Exception as error:
        log.error("cannot reach chat %s: %s", CHAT_ID, error)


def run_slot(cards: list[dict], number: int) -> None:
    picked = due_cards(cards, number)
    if not picked:
        log.info("slot %s: nothing due", number)
        return
    titles = ", ".join(card["title"] for card, _ in picked)
    if send(render_message(picked)):
        log.info("slot %s: sent %s card(s): %s", number, len(picked), titles)
    else:
        log.error("slot %s: delivery failed: %s", number, titles)


def main() -> None:
    cards = load_deck()
    log.info(
        "deck: %s cards | start %s | slots %s | tz %s",
        len(cards),
        START,
        ",".join(f"{h:02d}:{m:02d}" for h, m in SLOTS),
        TZ.key,
    )
    check_chat()

    if os.environ.get("DRIP_SEND_NOW") == "1":
        now = datetime.now(TZ)
        number = max(0, slot_number(now.date(), 0))
        log.info("DRIP_SEND_NOW: sending slot %s once", number)
        run_slot(cards, number)
        return

    while True:
        moment, number = next_slot(datetime.now(TZ))
        wait = (moment - datetime.now(TZ)).total_seconds()
        log.info("next slot %s at %s (in %.0f min)", number, moment.isoformat(), wait / 60)
        while wait > 0:
            time.sleep(min(wait, 300))  # wake periodically so clock changes are picked up
            wait = (moment - datetime.now(TZ)).total_seconds()
        cards = load_deck()  # pick up deck edits without a restart
        run_slot(cards, number)
        time.sleep(90)  # never fire the same slot twice


if __name__ == "__main__":
    main()
