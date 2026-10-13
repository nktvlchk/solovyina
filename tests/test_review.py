"""Anki-like review deck: scheduling, mistakes, and words the user asked to keep."""

import datetime as dt
import json

from bot.handlers import common, modes, review
from bot.handlers import translation as th
from bot.models.correction import Verdict
from bot.services.review import (
    ASK_MEAN,
    ASK_SAY,
    GRADE_AGAIN,
    GRADE_EASY,
    GRADE_GOOD,
    GRADE_HARD,
    SrsState,
    apply_grade,
)
from bot.storage.repositories import Card
from tests.conftest import FakeLLM, make_context, make_update

NOW = dt.datetime(2026, 10, 13, 12, 0, 0)

CHECK = json.dumps(
    {
        "corrected_text": "у магазин",
        "corrections": [
            {
                "original": "в магазин",
                "replacement": "у магазин",
                "type": "grammar",
                "severity": "error",
                "explanation": "чергування у/в",
                "confidence": 0.9,
            },
            {
                "original": "магазин",
                "replacement": "крамницю",
                "type": "vocabulary",
                "severity": "suggestion",
                "explanation": "лише стиль",
                "confidence": 0.4,
            },
        ],
    },
    ensure_ascii=False,
)


def test_good_intervals_grow():
    state = apply_grade(SrsState(), GRADE_GOOD)
    assert (state.interval_days, state.reps, state.ease) == (1, 1, 2.5)
    state = apply_grade(state, GRADE_GOOD)
    assert (state.interval_days, state.reps) == (6, 2)
    state = apply_grade(state, GRADE_GOOD)
    assert (state.interval_days, state.reps) == (15, 3)


def test_easy_is_longer_and_again_resets():
    state = apply_grade(SrsState(), GRADE_EASY)
    assert (state.interval_days, state.ease, state.reps) == (4, 2.65, 1)
    state = apply_grade(state, GRADE_AGAIN)
    assert (state.interval_days, state.reps, state.lapses, state.ease) == (0, 0, 1, 2.45)


def test_new_again_is_not_a_lapse_and_ease_stays_in_bounds():
    assert apply_grade(SrsState(), GRADE_AGAIN).lapses == 0
    state = SrsState(ease=1.3, reps=2, interval_days=6)
    for _ in range(8):
        state = apply_grade(state, GRADE_AGAIN)
    assert state.ease == 1.3
    state = SrsState(ease=2.9, reps=2, interval_days=10)
    for _ in range(8):
        state = apply_grade(state, GRADE_EASY)
    assert state.ease == 3.0


def test_hard_on_a_new_card_is_tomorrow():
    state = apply_grade(SrsState(), GRADE_HARD)
    assert (state.interval_days, state.reps, state.ease) == (1, 0, 2.35)


def test_grade_buttons_show_the_next_interval():
    card = Card(1, "word", ASK_MEAN, "кіт", "кот", "", 2.5, 0, 0, 0, "2026-10-13 12:00:00")
    rows = review.grade_keyboard(card).inline_keyboard
    labels = [button.text for row in rows for button in row]
    assert labels[:4] == ["Знову", "Важко · 1д", "Добре · 1д", "Легко · 4д"]
    assert all(len(button.callback_data.encode()) <= 64 for row in rows for button in row)


def test_save_word_dedupes_and_good_moves_the_due_date(make_services):
    svc = make_services()
    assert svc.cards.save_word(1, ASK_MEAN, "кіт", "кот", now=NOW) == "saved"
    assert svc.cards.save_word(1, ASK_MEAN, "кіт", "кот", now=NOW) == "exists"
    assert svc.cards.save_word(1, ASK_MEAN, "Кіт", "котик", now=NOW) == "updated"
    assert svc.cards.save_word(1, ASK_SAY, "кіт", "кіт", now=NOW) == "rejected"
    card = svc.cards.due(1, NOW)[0]
    assert card.back == "котик"
    updated = svc.cards.grade(1, card.id, GRADE_GOOD, now=NOW)
    assert updated is not None and updated.interval_days == 1
    assert svc.cards.due(1, NOW) == []
    assert svc.cards.due(1, NOW + dt.timedelta(days=1))[0].id == card.id
    assert svc.cards.grade(2, card.id, GRADE_GOOD, now=NOW) is None


async def test_sync_keeps_only_confirmed_mistakes_and_their_schedule(make_services):
    svc = make_services(llm=FakeLLM(check=CHECK), ANTHROPIC_API_KEY="k")
    result = await svc.correction.check(1, "в магазин")
    real, style = result.corrections
    svc.corrections.set_verdict(real.id, 1, Verdict.ERROR)
    svc.corrections.set_verdict(style.id, 1, Verdict.STYLE)
    svc.cards.sync_mistakes(1, NOW)
    cards = svc.cards.due(1, NOW)
    assert [card.front for card in cards] == ["в магазин"]
    assert cards[0].back == "у магазин" and cards[0].note == "чергування у/в"
    graded = svc.cards.grade(1, cards[0].id, GRADE_GOOD, now=NOW)
    assert graded.interval_days == 1
    svc.cards.sync_mistakes(1, NOW)  # confirming again must not reset the schedule
    assert svc.cards.get(1, cards[0].id).interval_days == 1
    svc.corrections.set_verdict(real.id, 1, Verdict.NOT_ERROR)
    svc.cards.sync_mistakes(1, NOW)
    assert svc.cards.summary(1, NOW)[1] == 0


async def test_review_hides_the_answer_until_reveal_then_finishes(make_services):
    svc = make_services()
    svc.cards.save_word(1, ASK_MEAN, "кіт", "кот")
    ctx = make_context(svc)
    update = make_update("/review")
    await review.cmd_review(update, ctx)
    front = update.effective_message.sent[-1]["text"]
    assert "кіт" in front and "кот" not in front and "Що означає?" in front
    card_id = svc.cards.due(1)[0].id
    show = make_update(callback=f"rv:s:{card_id}")
    await review.on_review_callback(show, ctx)
    assert "кот" in show.callback_query.edited_text[-1]
    grade = make_update(callback=f"rv:3:{card_id}")
    await review.on_review_callback(grade, ctx)
    done = grade.callback_query.edited_text[-1]
    assert "На сьогодні все" in done and "Добре: 1" in done
    assert svc.cards.due(1) == []
    rows = svc.analytics.export_rows()
    assert rows[-1]["event"] == "review" and rows[-1]["detail"] == "done"
    assert "кіт" not in str(rows)


async def test_again_waits_until_the_other_card_is_done(make_services):
    svc = make_services()
    svc.cards.save_word(1, ASK_MEAN, "кіт", "кот")
    svc.cards.save_word(1, ASK_SAY, "магазин", "крамниця")
    ids = [card.id for card in svc.cards.due(1)]
    ctx = make_context(svc)
    await review.cmd_review(make_update("/review"), ctx)
    again = make_update(callback=f"rv:1:{ids[0]}")
    await review.on_review_callback(again, ctx)
    assert svc.cards.get(1, ids[1]).front in again.callback_query.edited_text[-1]
    nxt = make_update(callback=f"rv:3:{ids[1]}")
    await review.on_review_callback(nxt, ctx)
    returned = nxt.callback_query.edited_text[-1]
    assert svc.cards.get(1, ids[0]).front in returned
    assert "залишилось 1" in returned
    await review.on_review_callback(make_update(callback=f"rv:3:{ids[0]}"), ctx)
    assert svc.cards.due(1) == []


async def test_empty_deck_explains_both_sources(make_services):
    update = make_update("/review")
    await review.cmd_review(update, make_context(make_services()))
    text = update.effective_message.sent[-1]["text"]
    assert "Карток ще немає" in text and "Це помилка" in text and "/remember" in text


async def test_confirmed_mistake_becomes_a_card(make_services):
    svc = make_services(llm=FakeLLM(check=CHECK), ANTHROPIC_API_KEY="k")
    result = await svc.correction.check(1, "в магазин")
    press = make_update(callback=f"fb:{result.corrections[0].id}:e")
    from bot.handlers import feedback

    await feedback.on_feedback_callback(press, make_context(svc))
    assert "Буде в картках" in press.callback_query.answers[-1]
    ctx = make_context(svc)
    update = make_update("🃏 Повторити")
    await th.on_text(update, ctx)
    front = update.effective_message.sent[-1]["text"]
    assert "в магазин" in front and "у магазин" not in front and "помилка" in front
    card = svc.cards.due(1)[0]
    show = make_update(callback=f"rv:s:{card.id}")
    await review.on_review_callback(show, ctx)
    revealed = show.callback_query.edited_text[-1]
    assert "у магазин" in revealed and "чергування" in revealed and "крамницю" not in revealed


async def test_remember_button_saves_once_and_escapes_nothing_into_analytics(make_services):
    svc = make_services()
    update = make_update("кіт")
    await th.on_text(update, make_context(svc))
    buttons = update.effective_message.sent[-1]["reply_markup"].inline_keyboard[0]
    assert buttons[0].callback_data.startswith("tts:")
    remember = next(button for button in buttons if button.callback_data.startswith("mem:"))
    press = make_update(callback=remember.callback_data)
    await review.on_remember_callback(press, make_context(svc))
    assert "Додано" in press.callback_query.answers[-1]
    card = svc.cards.due(1)[0]
    assert card.front == "кіт" and card.back == "кот" and card.ask == ASK_MEAN
    again = make_update(callback=remember.callback_data)
    await review.on_remember_callback(again, make_context(svc))
    assert "Вже є" in again.callback_query.answers[-1]
    assert len(svc.cards.due(1)) == 1
    assert "кіт" not in str(svc.analytics.export_rows())


async def test_remember_command_and_mode(make_services):
    svc = make_services()
    update = make_update("/remember кіт")
    await review.cmd_remember(update, make_context(svc))
    assert "кот" in update.effective_message.sent[-1]["text"]
    ctx = make_context(svc)
    await th.on_text(make_update("📌 Запам'ятати"), ctx)
    await th.on_text(make_update("мрія"), ctx)
    fronts = {card.front for card in svc.cards.due(1)}
    assert fronts == {"кіт", "мрія"}


async def test_card_text_is_escaped(make_services):
    svc = make_services()
    svc.cards.save_word(1, ASK_SAY, "<b>", "кіт")
    update = make_update("/review")
    await review.cmd_review(update, make_context(svc))
    text = update.effective_message.sent[-1]["text"]
    assert "&lt;b&gt;" in text
    assert "<b><b>" not in text


async def test_stranger_cannot_open_a_card(make_services):
    svc = make_services()
    svc.cards.save_word(1, ASK_MEAN, "кіт", "кот")
    card_id = svc.cards.due(1)[0].id
    update = make_update(user_id=2, callback=f"rv:s:{card_id}")
    await review.on_review_callback(update, make_context(svc))
    assert "не знайдено" in update.callback_query.answers[-1]
    assert update.callback_query.edited_text == []


async def test_double_tap_does_not_schedule_twice(make_services):
    svc = make_services()
    svc.cards.save_word(1, ASK_MEAN, "кіт", "кот")
    card_id = svc.cards.due(1)[0].id
    ctx = make_context(svc)
    await review.cmd_review(make_update("/review"), ctx)
    await review.on_review_callback(make_update(callback=f"rv:4:{card_id}"), ctx)
    second = make_update(callback=f"rv:4:{card_id}")
    await review.on_review_callback(second, ctx)
    assert "Вже оцінено" in second.callback_query.answers[-1]
    assert svc.cards.get(1, card_id).interval_days == 4


async def test_stop_leaves_the_card_due(make_services):
    svc = make_services()
    svc.cards.save_word(1, ASK_MEAN, "кіт", "кот")
    ctx = make_context(svc)
    await review.cmd_review(make_update("/review"), ctx)
    stop = make_update(callback="rv:x")
    await review.on_review_callback(stop, ctx)
    assert "Зупинились" in stop.callback_query.edited_text[-1]
    assert svc.cards.due(1)


async def test_delete_and_retention_cover_cards(make_services):
    svc = make_services()
    svc.cards.save_word(1, ASK_MEAN, "кіт", "кот")
    svc.cards.save_word(2, ASK_MEAN, "пес", "собака")
    await common.on_delete_callback(make_update(callback="del:yes", user_id=1), make_context(svc))
    assert svc.cards.summary(1)[1] == 0
    assert svc.cards.summary(2)[1] == 1

    svc.cards.save_word(1, ASK_MEAN, "кіт", "кот")
    svc.db.execute("UPDATE cards SET updated_at = datetime('now', '-400 days') WHERE front = 'кіт'")
    svc.cards.save_word(1, ASK_SAY, "дім", "дом")
    svc.db.execute(
        "INSERT INTO cards(user_id, kind, source_key, ask, front, back) "
        "VALUES (1, 'mistake', 'gone', 'fix', 'старий фрагмент', 'новий')"
    )
    svc.privacy.purge_older_than(180)
    fronts = {row["front"] for row in svc.db.query("SELECT front FROM cards")}
    assert "дім" in fronts and "пес" in fronts
    assert "кіт" not in fronts and "старий фрагмент" not in fronts


def test_keyboard_offers_review_and_remember():
    labels = []
    for row in modes.modes_keyboard().keyboard:
        for button in row:
            labels.append(getattr(button, "text", button))
    assert "🃏 Повторити" in labels and "📌 Запам'ятати" in labels


async def test_privacy_mentions_cards(make_services):
    update = make_update("/privacy")
    await common.cmd_privacy(update, make_context(make_services()))
    assert "картки повторення" in update.effective_message.sent[-1]["text"]
