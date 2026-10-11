from datetime import date, datetime
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
    assert len(drip.due_cards(cards, 105)) == drip.MAX_PER_MESSAGE
    assert drip.slot_number(cfg, date(2026, 10, 9), 1) == 4
    moment, number = drip.next_slot(cfg, datetime(2026, 10, 8, 10, 0, tzinfo=ZoneInfo("Europe/Lisbon")))
    assert (moment.hour, number) == (14, 1)


def test_render_escapes_and_marks_repeats():
    card = drip.Card(id="R1", text="a < b & <b>c</b>")
    assert drip.render_card(card, 0) == "a &lt; b &amp; &lt;b&gt;c&lt;/b&gt;"
    assert drip.render_card(card, 6).startswith("🔁 <i>повторення</i>\n")


def test_long_cards_are_split_by_telegram_limit():
    big = [(drip.Card(id=f"R{i}", text="x" * 3000), 0) for i in range(3)]
    messages = drip.render_messages(big)
    assert len(messages) == 3 and all(len(m) <= drip.TELEGRAM_LIMIT for m in messages)


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
