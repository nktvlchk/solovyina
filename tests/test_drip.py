from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import drip
import pytest

EXAMPLE = Path(__file__).resolve().parents[1] / "docs" / "deck.example.md"


def config(**env):
    base = {"TELEGRAM_BOT_TOKEN": "1:x", "DRIP_CHAT_ID": "-100", "DRIP_DECK": str(EXAMPLE)}
    base.update(env)
    return drip.load_config(base)


def test_markdown_deck_parsing():
    cards = drip.parse_deck(EXAMPLE.read_text(encoding="utf-8"))
    assert [c.id for c in cards] == ["R01", "V01"]
    assert cards[0].text.startswith("🧠 R01 · Кличний")
    assert "<!--" not in cards[1].text


def test_bad_markdown_deck_rejected():
    with pytest.raises(ValueError):
        drip.parse_deck("no marker")
    with pytest.raises(ValueError):
        drip.parse_deck("<!-- CARDS_START -->\n---\nбез заголовка\n<!-- CARDS_END -->")
    dup = "<!-- CARDS_START -->\n---\n🧠 R01 · a\n---\n🧠 R01 · b\n<!-- CARDS_END -->"
    with pytest.raises(ValueError):
        drip.parse_deck(dup)


def test_json_deck_still_supported():
    cards = drip.parse_deck('{"cards": [{"id": 1, "type": "rule", "title": "T", "body": "B"}]}')
    assert cards[0].text == "📘 ПРАВИЛО\nT\nB"


def test_config_requires_token_and_chat():
    with pytest.raises(SystemExit):
        drip.load_config({"DRIP_CHAT_ID": "-1"})
    with pytest.raises(SystemExit):
        drip.load_config({"TELEGRAM_BOT_TOKEN": "1:x"})


def test_url_deck_and_fallback_to_last_good_copy():
    calls = {"n": 0}

    def fetch(url):
        calls["n"] += 1
        if calls["n"] > 1:
            raise OSError("network")
        return EXAMPLE.read_text(encoding="utf-8")

    source = drip.DeckSource(config(DRIP_DECK_URL="https://example.invalid/deck.md"), fetch=fetch)
    first = source.load()
    second = source.load()  # download fails, previous copy is reused
    assert first == second and len(first) == 2


def test_schedule_and_repeats():
    cfg = config(DRIP_START="2026-10-08")
    cards = [drip.Card(id=f"R{i}", text=f"card {i}") for i in range(200)]
    assert [c.id for c, _ in drip.due_cards(cards, 0)] == ["R0"]
    picked = drip.due_cards(cards, 6)
    assert ("R6", 0) in [(c.id, o) for c, o in picked] and ("R0", 6) in [(c.id, o) for c, o in picked]
    assert len(drip.due_cards(cards, 40)) == drip.MAX_PER_MESSAGE
    assert drip.slot_number(cfg, date(2026, 10, 9)) == 1


def test_one_random_send_a_day_inside_the_window():
    cfg = config(DRIP_START="2026-10-08", DRIP_WINDOW="08:00-20:00")
    tz = ZoneInfo("Europe/Lisbon")
    days = [date(2026, 10, 8) + timedelta(days=i) for i in range(60)]
    minutes = [drip.slot_minute(cfg, day) for day in days]
    assert all(8 * 60 <= minute <= 20 * 60 for minute in minutes)
    assert len(set(minutes)) > 40  # the time really moves from day to day
    assert minutes == [drip.slot_minute(cfg, day) for day in days]  # stable across calls
    assert drip.slot_minute(config(DRIP_SEED="other"), days[0]) != minutes[0]

    # today's send if it is still ahead, otherwise tomorrow's — never twice a day
    moment, number = drip.next_slot(cfg, datetime(2026, 10, 8, 0, 1, tzinfo=tz))
    assert (moment.date(), number) == (date(2026, 10, 8), 0)
    assert 8 <= moment.hour <= 20
    moment, number = drip.next_slot(cfg, datetime(2026, 10, 8, 23, 59, tzinfo=tz))
    assert (moment.date(), number) == (date(2026, 10, 9), 1)


def test_window_is_validated():
    assert drip.parse_window(" 08:30 - 09:00 ") == (510, 540)
    with pytest.raises(ValueError):
        drip.parse_window("20:00-08:00")  # overnight windows are not supported
    with pytest.raises(ValueError):
        drip.parse_window("8-20")


def test_render_escapes_and_marks_repeats():
    card = drip.Card(id="R1", text="a < b & <b>c</b>")
    assert drip.render_card(card, 0) == "a &lt; b &amp; &lt;b&gt;c&lt;/b&gt;"
    assert drip.render_card(card, 6).startswith("🔁 <i>повторення</i>\n")


def test_everything_due_goes_out_in_a_single_message():
    picked = [(drip.Card(id="R6", text="new"), 0), (drip.Card(id="R0", text="review"), 6)]
    message, dropped = drip.render_message(picked)
    assert dropped == [] and drip.SEPARATOR in message and len(message) <= drip.TELEGRAM_LIMIT

    big = [(drip.Card(id=f"R{i}", text="x" * 3000), 0) for i in range(3)]
    message, dropped = drip.render_message(big)
    assert len(message) <= drip.TELEGRAM_LIMIT and dropped == ["R1", "R2"]


def test_id_comment_is_stripped_from_the_sent_text():
    deck = (
        "<!-- CARDS_START -->\n---\n<!-- R07 -->\n🧠 Апостроф\n\nпояснення\n"
        "---\n<!-- V12 -->\n📚 Кольори\n\nсиній\n<!-- CARDS_END -->"
    )
    cards = drip.parse_deck(deck)
    assert [c.id for c in cards] == ["R07", "V12"]
    assert cards[0].text == "🧠 Апостроф\n\nпояснення"
    assert "R07" not in cards[0].text and "<!--" not in cards[1].text


def test_mixed_and_broken_id_comments():
    mixed = "<!-- CARDS_START -->\n---\n<!-- R01 -->\n🧠 A\n---\n📚 V01 · B\n<!-- CARDS_END -->"
    assert [c.id for c in drip.parse_deck(mixed)] == ["R01", "V01"]
    with pytest.raises(ValueError):
        drip.parse_deck("<!-- CARDS_START -->\n---\n<!-- R01 -->\n<!-- CARDS_END -->")
    with pytest.raises(ValueError):
        drip.parse_deck(
            "<!-- CARDS_START -->\n---\n<!-- R01 -->\n🧠 A\n---\n<!-- R01 -->\n🧠 B\n<!-- CARDS_END -->"
        )
