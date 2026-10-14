"""Typed daily practice: matching, scheduling, privacy, and the week report."""

import asyncio
import datetime as dt

from bot.handlers import modes, practice, translation
from bot.handlers.modes import modes_keyboard
from bot.models.correction import Correction, CorrectionType, Severity, Verdict
from bot.services.practice import choose_daily, is_correct
from bot.services.review import ASK_MEAN, ASK_SAY, GRADE_GOOD
from bot.storage.repositories import Card
from tests.conftest import make_context, make_update


def _card(card_id: int, *, due: str = "2026-01-01 00:00:00", source: str = "") -> Card:
    return Card(
        card_id,
        "mistake" if source else "word",
        ASK_MEAN,
        f"front {card_id}",
        f"back {card_id}",
        "",
        2.5,
        0,
        0,
        0,
        due,
        source,
    )


def test_answer_matching_accepts_alternatives_and_ignores_case():
    assert is_correct("котик", "кот; котик")
    assert is_correct("кот", "кот; котик")
    assert is_correct("кот; котик", "кот; котик")
    assert is_correct("А", "а / б")
    assert is_correct("Кіт!", "кіт")
    assert not is_correct("котик", "кот")
    assert not is_correct("   ", "кот")
    assert not is_correct("кіт", "")


def test_choose_daily_prefers_weak_types_then_due_date():
    spelling = [_card(2, due="2026-02-01 00:00:00", source="s"), _card(4, source="s")]
    grammar = _card(1, due="2020-01-01 00:00:00", source="g")
    word = _card(3, due="2019-01-01 00:00:00")
    chosen = choose_daily(
        [word, grammar, *spelling],
        {1: "grammar", 2: "spelling", 4: "spelling"},
        ["spelling", "grammar"],
    )
    assert [card.id for card in chosen] == [4, 2, 1, 3]
    # A type outside the profile sorts with saved words, then by due date.
    unknown = choose_daily([word, grammar], {1: "grammar"}, ["spelling"])
    assert [card.id for card in unknown] == [3, 1]
    assert [card.id for card in choose_daily([word, grammar], {}, [], limit=1)] == [3]


def _confirm(svc, original: str, replacement: str, type_: str, explanation: str = "пояснення") -> None:
    correction = Correction(
        id=f"c-{original}",
        original=original,
        replacement=replacement,
        type=CorrectionType(type_),
        severity=Severity.ERROR,
        explanation=explanation,
    )
    svc.corrections.add(1, correction, "m", "v")
    svc.corrections.set_verdict(correction.id, 1, Verdict.ERROR)


async def test_typed_answer_is_scored_and_not_translated(make_services):
    svc = make_services()
    svc.cards.save_word(1, ASK_MEAN, "кіт", "кот")
    ctx = make_context(svc)
    opening = make_update("🎯 Тренування")
    await translation.on_text(opening, ctx)
    question = opening.effective_message.sent[-1]["text"]
    assert "Завдання 1 з 1" in question and "кіт" in question
    assert (
        opening.effective_message.sent[-1]["reply_markup"]
        .inline_keyboard[0][0]
        .callback_data.startswith("pr:")
    )

    answer = make_update("кот")
    await translation.on_text(answer, ctx)
    text = "\n".join(item["text"] for item in answer.effective_message.sent)
    assert "Так." in text and "Джерело" not in text
    assert svc.cards.due(1) == []
    assert svc.metrics.count("requests.practice") == 1
    assert "кот" not in str(svc.analytics.export_rows())
    stored = svc.db.query("SELECT user_id, ok FROM practice_log")
    assert [(row["user_id"], row["ok"]) for row in stored] == [(1, 1)]


async def test_wrong_answer_is_not_repeated_in_the_same_session(make_services):
    svc = make_services()
    svc.cards.save_word(1, ASK_MEAN, "кіт", "кот; котик")
    svc.cards.save_word(1, ASK_SAY, "дом", "дім")
    ctx = make_context(svc)
    await practice.cmd_practice(make_update("/practice"), ctx)
    first_id = ctx.user_data["practice"]["current"]
    wrong = make_update("пес <b>")
    await translation.on_text(wrong, ctx)
    shown = wrong.effective_message.sent[0]["text"]
    assert "Ви написали" in shown and "пес &lt;b&gt;" in shown
    assert "кот" in shown and "пес <b>" not in shown
    assert ctx.user_data["practice"]["current"] != first_id
    assert first_id not in ctx.user_data["practice"]["rest"]
    assert svc.cards.get(1, first_id).interval_days == 0

    second_id = ctx.user_data["practice"]["current"]
    right = svc.cards.get(1, second_id).back
    await translation.on_text(make_update(right), ctx)
    assert "practice" not in ctx.user_data
    assert svc.cards.get(1, first_id) in svc.cards.due(1)
    assert svc.cards.get(1, second_id) not in svc.cards.due(1)
    blob = str([(row["user_id"], row["ok"]) for row in svc.db.query("SELECT user_id, ok FROM practice_log")])
    assert "пес" not in blob

    again = make_update("/practice")
    await practice.cmd_practice(again, ctx)
    assert "Завдання 1 з 1" in again.effective_message.sent[-1]["text"]
    assert svc.cards.get(1, ctx.user_data["practice"]["current"]).id == first_id


async def test_alternative_and_skip_and_explanation(make_services):
    svc = make_services()
    _confirm(svc, "в магазин", "у магазин", "grammar", "чергування у/в")
    ctx = make_context(svc)
    await practice.cmd_practice(make_update("/practice"), ctx)
    card_id = ctx.user_data["practice"]["current"]
    skip = make_update(callback=f"pr:{card_id}")
    await practice.on_practice_callback(skip, ctx)
    assert "Поки що так" in skip.callback_query.edited_text[-1]
    assert "у магазин" in skip.callback_query.edited_text[-1]
    assert "чергування" in skip.callback_query.edited_text[-1]
    assert "Ви написали" not in skip.callback_query.edited_text[-1]
    assert svc.cards.get(1, card_id).interval_days == 0
    assert svc.db.query("SELECT ok FROM practice_log")[0]["ok"] == 0

    svc.cards.grade(1, card_id, GRADE_GOOD)
    svc.cards.save_word(1, ASK_MEAN, "кіт", "кот; котик")
    await practice.cmd_practice(make_update("/practice"), ctx)
    await translation.on_text(make_update("котик"), ctx)
    assert svc.cards.due(1) == []


async def test_session_is_five_and_the_rest_waits_for_later(make_services):
    svc = make_services()
    for i in range(6):
        svc.cards.save_word(1, ASK_SAY, f"питання {i}", f"відповідь {i}")
    ctx = make_context(svc)
    opening = make_update("/practice")
    await practice.cmd_practice(opening, ctx)
    assert "Завдання 1 з 5" in opening.effective_message.sent[-1]["text"]
    last = None
    for _ in range(5):
        back = svc.cards.get(1, ctx.user_data["practice"]["current"]).back
        last = make_update(back)
        await translation.on_text(last, ctx)
    summary = last.effective_message.sent[-1]["text"]
    assert "вірно 5" in summary and "Ще на сьогодні: 1." in summary
    assert "practice" not in ctx.user_data
    assert len(svc.cards.due(1)) == 1


async def test_weak_error_type_is_asked_first(make_services):
    svc = make_services()
    _confirm(svc, "граматика", "правильно", "grammar")
    _confirm(svc, "орфографія", "правильно теж", "spelling")
    _confirm(svc, "ще орфографія", "і це", "spelling")
    ctx = make_context(svc)
    opening = make_update("/practice")
    await practice.cmd_practice(opening, ctx)
    question = opening.effective_message.sent[-1]["text"]
    assert "граматика" not in question
    assert "орфографія" in question


async def test_empty_deck_and_nothing_due_are_different(make_services):
    svc = make_services()
    empty = make_update("/practice")
    await practice.cmd_practice(empty, make_context(svc))
    assert "Карток ще немає" in empty.effective_message.sent[-1]["text"]
    assert "Це помилка" in empty.effective_message.sent[-1]["text"]

    svc.cards.save_word(1, ASK_MEAN, "кіт", "кот")
    card = svc.cards.due(1)[0]
    svc.cards.grade(1, card.id, GRADE_GOOD)
    idle = make_update("/practice")
    await practice.cmd_practice(idle, make_context(svc))
    text = idle.effective_message.sent[-1]["text"]
    assert "На сьогодні все." in text and "Завдання" not in text


async def test_other_mode_cancels_the_session(make_services):
    svc = make_services()
    svc.cards.save_word(1, ASK_MEAN, "кіт", "кот")
    ctx = make_context(svc)
    await translation.on_text(make_update("🎯 Тренування"), ctx)
    assert ctx.user_data["practice"]["current"]
    await translation.on_text(make_update("🗂 Мої помилки"), ctx)
    assert "practice" not in ctx.user_data
    translated = make_update("кот")
    await translation.on_text(translated, ctx)
    assert "Джерело" in translated.effective_message.sent[-1]["text"]
    assert svc.db.query("SELECT COUNT(*) AS n FROM practice_log")[0]["n"] == 0


async def test_future_card_is_not_pulled_into_practice(make_services):
    svc = make_services()
    svc.cards.save_word(1, ASK_MEAN, "кіт", "кот")
    svc.cards.grade(1, svc.cards.due(1)[0].id, GRADE_GOOD)
    svc.cards.save_word(1, ASK_MEAN, "дім", "дом")
    opening = make_update("/practice")
    await practice.cmd_practice(opening, make_context(svc))
    text = opening.effective_message.sent[-1]["text"]
    assert "дім" in text and "кіт" not in text and "Завдання 1 з 1" in text


async def test_progress_and_week_window(make_services):
    svc = make_services()
    _confirm(svc, "в", "у", "grammar")
    _confirm(svc, "магазін", "магазин", "spelling")
    old = dt.datetime.now(dt.UTC) - dt.timedelta(days=8)
    svc.practice.record(1, True, now=old)
    svc.practice.record(1, True)
    svc.practice.record(1, False)
    update = make_update("/progress")
    await practice.cmd_progress(update, make_context(svc))
    text = update.effective_message.sent[-1]["text"]
    assert "1 із 2 вірних" in text
    assert "граматика" in text and "орфографія" in text
    assert "Зараз:" not in text


async def test_double_message_grades_once(make_services):
    svc = make_services()
    svc.cards.save_word(1, ASK_MEAN, "кіт", "кот")
    ctx = make_context(svc)
    await practice.cmd_practice(make_update("/practice"), ctx)
    await asyncio.gather(
        translation.on_text(make_update("кот"), ctx),
        translation.on_text(make_update("кот"), ctx),
    )
    assert svc.cards.due(1) == []
    assert svc.db.query("SELECT COUNT(*) AS n FROM practice_log")[0]["n"] == 1
    assert svc.cards.summary(1)[1] == 1


async def test_stranger_callback_does_not_grade(make_services):
    svc = make_services()
    svc.cards.save_word(1, ASK_MEAN, "кіт", "кот")
    ctx = make_context(svc)
    await practice.cmd_practice(make_update("/practice"), ctx)
    card_id = ctx.user_data["practice"]["current"]
    stranger = make_update(user_id=2, callback=f"pr:{card_id}")
    await practice.on_practice_callback(stranger, ctx)
    assert "закрите" in stranger.effective_message.sent[-1]["text"]
    assert svc.cards.get(1, card_id).reps == 0
    assert "practice" not in ctx.user_data


async def test_delete_and_retention_cover_the_log(make_services):
    from bot.handlers import common

    svc = make_services()
    svc.practice.record(1, True)
    svc.practice.record(2, False)
    await common.on_delete_callback(make_update(callback="del:yes", user_id=1), make_context(svc))
    left = svc.db.query("SELECT user_id FROM practice_log")
    assert [row["user_id"] for row in left] == [2]

    svc.practice.record(1, True)
    svc.db.execute("UPDATE practice_log SET created_at = datetime('now', '-400 days')")
    svc.privacy.purge_older_than(180)
    assert svc.db.query("SELECT COUNT(*) AS n FROM practice_log")[0]["n"] == 0


async def test_privacy_copy_mentions_practice(make_services):
    from bot.handlers import common

    svc = make_services()
    privacy = make_update("/privacy")
    await common.cmd_privacy(privacy, make_context(svc))
    text = privacy.effective_message.sent[-1]["text"]
    assert "результати тренувань" in text and "без тексту" in text
    prompt = make_update("/delete_my_data")
    await common.cmd_delete_my_data(prompt, make_context(svc))
    assert "результати тренувань" in prompt.effective_message.sent[-1]["text"]


def test_keyboard_pairs_include_practice():
    rows = modes_keyboard().keyboard
    labels = [getattr(button, "text", button) for row in rows for button in row]
    assert "🎯 Тренування" in labels
    assert all(len(row) <= 2 for row in rows)
    assert modes.PRACTICE == "practice"
