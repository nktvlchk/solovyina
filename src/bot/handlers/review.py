"""Personal review deck: confirmed mistakes and words the user asked to keep.

A session shows one card at a time. The front is the question, the back is
the answer, then Again / Hard / Good / Easy — the same four grades as Anki.
Again returns later in this session; the other grades schedule a future day.
"""

from __future__ import annotations

import datetime as dt
import json

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from bot.formatting import esc_line
from bot.handlers.util import reply_html, services, track, user_id
from bot.language import Direction
from bot.services.dictionary import normalize
from bot.services.review import (
    ASK_FIX,
    ASK_MEAN,
    ASK_SAY,
    GRADE_AGAIN,
    GRADE_EASY,
    GRADE_GOOD,
    GRADE_HARD,
    GRADE_NAMES,
    GRADES,
    apply_grade,
)
from bot.services.translation import TranslationUnavailable
from bot.storage.repositories import Card

ASK_TEXT = {
    ASK_FIX: "Як правильно?",
    ASK_SAY: "Як сказати українською?",
    ASK_MEAN: "Що означає?",
}
GRADE_LABELS = {
    GRADE_AGAIN: "Знову",
    GRADE_HARD: "Важко",
    GRADE_GOOD: "Добре",
    GRADE_EASY: "Легко",
}
REMEMBER_TOAST = {
    "saved": "📌 Додано в картки",
    "updated": "📌 Оновив картку",
    "exists": "📌 Вже є в картках",
    "rejected": "Не додаю цю картку",
}

_QUEUE = "review_queue"
_CURRENT = "review_current"
_SHOWN = "review_shown"
_STATS = "review_stats"
_CLOSED = "closed"
_EMPTY_MARKUP = InlineKeyboardMarkup([])


def _pair_ok(front: str, back: str) -> bool:
    front_key, back_key = normalize(front), normalize(back)
    return bool(front_key and back_key and front_key != back_key)


def sides_from_pair(
    original: str,
    translation: str,
    direction: Direction,
    note: str = "",
) -> tuple[str, str, str, str] | None:
    """(ask, front, back, note). Front is what the learner should recall from."""
    if direction is Direction.AUTO or not _pair_ok(original, translation):
        return None
    ask = ASK_SAY if direction is Direction.RU_UK else ASK_MEAN
    return ask, original.strip(), translation.strip(), note.strip()


def sides_from_lookup(lookup) -> tuple[str, str, str, str] | None:
    back = ""
    if lookup.entries:
        targets: list[str] = []
        for entry in lookup.entries[:4]:
            target = entry.ru if lookup.direction is Direction.UK_RU else entry.uk
            if target and target not in targets:
                targets.append(target)
        back = "; ".join(targets)
    elif lookup.result is not None:
        back = lookup.result.text
    note = f"форма слова «{lookup.matched_form}»" if lookup.matched_form else ""
    return sides_from_pair(lookup.query, back, lookup.direction, note)


def _remember_button(svc, ask: str, front: str, back: str, note: str) -> InlineKeyboardButton | None:
    if ask not in (ASK_SAY, ASK_MEAN) or not _pair_ok(front, back):
        return None
    payload = json.dumps({"a": ask, "f": front, "b": back, "n": note}, ensure_ascii=False)
    key = svc.text_cache.put(payload)
    return InlineKeyboardButton("📌 Запам'ятати", callback_data=f"mem:{key}")


def attach_actions(
    svc,
    *,
    tts_text: str | None,
    ask: str = "",
    front: str = "",
    back: str = "",
    note: str = "",
) -> InlineKeyboardMarkup | None:
    """🔊 and 📌 on one row. 🔊 stays first: existing buttons keep their place."""
    row: list[InlineKeyboardButton] = []
    if tts_text and 0 < len(tts_text) <= svc.settings.max_tts_length:
        row.append(InlineKeyboardButton("🔊 Озвучити", callback_data=f"tts:{svc.text_cache.put(tts_text)}"))
    remember = _remember_button(svc, ask, front, back, note)
    if remember is not None:
        row.append(remember)
    return InlineKeyboardMarkup([row]) if row else None


def _kind(card: Card) -> str:
    if card.kind == "mistake":
        return "помилка"
    return "фраза" if " " in card.front else "слово"


def format_front(card: Card, left: int) -> str:
    return (
        f"🃏 <b>Повторення</b> · {_kind(card)} · залишилось {left}\n\n"
        f"{ASK_TEXT.get(card.ask, ASK_TEXT[ASK_FIX])}\n\n"
        f"<b>{esc_line(card.front)}</b>"
    )


def format_back(card: Card, left: int) -> str:
    if card.kind == "mistake":
        body = f"<s>{esc_line(card.front)}</s>\n\n✅ <b>{esc_line(card.back)}</b>"
    else:
        body = f"<b>{esc_line(card.front)}</b>\n\n→ <b>{esc_line(card.back)}</b>"
    note = f"\n<i>{esc_line(card.note)}</i>" if card.note else ""
    return (
        f"🃏 <b>Повторення</b> · {_kind(card)} · залишилось {left}\n\n"
        f"{ASK_TEXT.get(card.ask, ASK_TEXT[ASK_FIX])}\n\n"
        f"{body}{note}"
    )


def _when(due_at: str | None, now: dt.datetime) -> str:
    if not due_at:
        return "немає"
    due = dt.datetime.strptime(due_at, "%Y-%m-%d %H:%M:%S")
    if now.tzinfo is not None:
        now = now.astimezone(dt.UTC).replace(tzinfo=None)
    days = (due.date() - now.date()).days
    if days <= 0:
        return "сьогодні"
    if days == 1:
        return "завтра"
    return f"через {days} дн."


def _grade_line(stats: dict) -> str:
    labels = (("again", "Знову"), ("hard", "Важко"), ("good", "Добре"), ("easy", "Легко"))
    return " · ".join(f"{name}: {stats.get(key, 0)}" for key, name in labels if stats.get(key))


def format_idle(total: int, next_due: str | None, now: dt.datetime) -> str:
    if total == 0:
        return (
            "🃏 <b>Карток ще немає.</b>\n\n"
            "Вони з'являються з двох місць:\n"
            "• помилки, які ви підтвердили кнопкою «✅ Це помилка»;\n"
            "• слова й фрази, які ви просите запам'ятати — «📌 Запам'ятати» "
            "під перекладом або /remember."
        )
    lines = [
        "🃏 <b>На сьогодні все.</b>",
        "",
        f"У колоді: {total}.",
    ]
    if next_due:
        lines.append(f"Наступна картка: {_when(next_due, now)}.")
    return "\n".join(lines)


def format_finished(stats: dict, total: int, next_due: str | None, now: dt.datetime) -> str:
    lines = ["🃏 <b>На сьогодні все.</b>", ""]
    graded = _grade_line(stats)
    if graded:
        lines.append(graded)
    lines.append(f"У колоді: {total}.")
    if next_due:
        lines.append(f"Наступна картка: {_when(next_due, now)}.")
    return "\n".join(lines)


def format_stopped(stats: dict, due_left: int) -> str:
    lines = ["⏹ <b>Зупинились.</b>", ""]
    graded = _grade_line(stats)
    if graded:
        lines.append(graded)
    if due_left:
        lines.append(f"Ще на сьогодні: {due_left}. Продовжити: /review")
    else:
        lines.append("На сьогодні карток більше немає.")
    return "\n".join(lines)


def show_keyboard(card_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("👁 Показати відповідь", callback_data=f"rv:s:{card_id}")],
            [InlineKeyboardButton("⏹ Досить", callback_data="rv:x")],
        ]
    )


def grade_keyboard(card: Card) -> InlineKeyboardMarkup:
    def button(grade: int) -> InlineKeyboardButton:
        label = GRADE_LABELS[grade]
        if grade != GRADE_AGAIN:
            label = f"{label} · {apply_grade(card.state, grade).interval_days}д"
        return InlineKeyboardButton(label, callback_data=f"rv:{grade}:{card.id}")

    return InlineKeyboardMarkup(
        [
            [button(GRADE_AGAIN), button(GRADE_HARD)],
            [button(GRADE_GOOD), button(GRADE_EASY)],
            [InlineKeyboardButton("⏹ Досить", callback_data="rv:x")],
        ]
    )


def _fresh_stats() -> dict[str, int]:
    return {"again": 0, "hard": 0, "good": 0, "easy": 0}


def _stats(context: ContextTypes.DEFAULT_TYPE) -> dict[str, int]:
    stats = context.user_data.get(_STATS)
    if not isinstance(stats, dict):
        stats = _fresh_stats()
        context.user_data[_STATS] = stats
    return stats


def _token(card: Card) -> str:
    return f"{card.id}:{card.reps}:{card.lapses}:{card.interval_days}:{card.due_at}"


def _left(context: ContextTypes.DEFAULT_TYPE, card_id: int) -> int:
    queue = context.user_data.get(_QUEUE)
    if not isinstance(queue, list):
        return 1
    return len([item for item in queue if item != card_id]) + 1


def _advance(
    context: ContextTypes.DEFAULT_TYPE, card_id: int, grade: int, fallback_ids: list[int]
) -> int | None:
    """Next card in this session. Again goes to the back of the queue."""
    queue = context.user_data.get(_QUEUE)
    if not isinstance(queue, list):
        queue = [item for item in fallback_ids if item != card_id]
        if grade == GRADE_AGAIN and card_id in fallback_ids:
            queue.append(card_id)
    else:
        queue = [item for item in queue if item != card_id]
        if grade == GRADE_AGAIN:
            queue.append(card_id)
    if not queue:
        context.user_data[_QUEUE] = []
        context.user_data[_CURRENT] = None
        return None
    nxt = queue.pop(0)
    context.user_data[_QUEUE] = queue
    context.user_data[_CURRENT] = nxt
    return nxt


async def _present(update: Update, text: str, markup, *, edit: bool) -> None:
    query = update.callback_query
    if edit and query is not None:
        try:
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
                disable_web_page_preview=True,
            )
            return
        except BadRequest:
            if query.message is not None:
                await reply_html(query.message, text, reply_markup=markup)
            return
    if update.effective_message is not None:
        await reply_html(update.effective_message, text, reply_markup=markup)


async def start_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = services(context)
    uid = user_id(update)
    svc.cards.sync_mistakes(uid)
    due = svc.cards.due(uid)
    context.user_data[_STATS] = _fresh_stats()
    now = dt.datetime.now(dt.UTC)
    if not due:
        _due, total, nxt = svc.cards.summary(uid)
        track(update, context, "review", detail="empty" if total == 0 else "done")
        context.user_data[_SHOWN] = _CLOSED
        context.user_data[_QUEUE] = []
        context.user_data[_CURRENT] = None
        await _present(update, format_idle(total, nxt, now), None, edit=False)
        return
    context.user_data[_QUEUE] = [card.id for card in due[1:]]
    context.user_data[_CURRENT] = due[0].id
    context.user_data[_SHOWN] = _token(due[0])
    svc.metrics.incr("requests.review")
    await _present(update, format_front(due[0], len(due)), show_keyboard(due[0].id), edit=False)


async def cmd_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start_review(update, context)


async def cmd_remember(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from bot.handlers.modes import REMEMBER, select_mode
    from bot.handlers.util import command_text

    text = command_text(update)
    if not text:
        await select_mode(update, context, REMEMBER)
        return
    await run_remember(update, context, text)


async def run_remember(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    from bot.handlers.translation import LAST_DIRECTION_KEY, _resolve_direction, guard

    if not await guard(update, context, text):
        return
    svc = services(context)
    uid = user_id(update)
    direction, _detected = _resolve_direction(svc, uid, text)
    if direction is Direction.AUTO:
        direction = context.user_data.get(LAST_DIRECTION_KEY, Direction.RU_UK)
        if direction is Direction.AUTO:
            direction = Direction.RU_UK
    message = update.effective_message
    try:
        async with svc.concurrency():
            if len(text.split()) == 1:
                lookup = await svc.translation.translate_word(text, direction, uid)
                sides = sides_from_lookup(lookup)
            else:
                result = await svc.translation.translate_phrase(text, direction, uid)
                sides = sides_from_pair(text, result.text, direction)
    except TranslationUnavailable:
        await reply_html(message, "😔 Не вдалося перекласти, тож картку не додав. Спробуйте пізніше.")
        return
    if sides is None:
        await reply_html(
            message,
            "Не бачу окремого перекладу — картку не додаю. "
            "Натисніть «📌 Запам'ятати» під готовою відповіддю.",
        )
        return
    ask, front, back, note = sides
    status = svc.cards.save_word(uid, ask, front, back, note)
    if status in REMEMBER_TOAST and status != "rejected":
        track(update, context, "remember", detail=status)
    if status == "saved":
        svc.metrics.incr("requests.remember")
    if status == "rejected":
        await reply_html(message, "Не додаю: немає що запам'ятовувати.")
        return
    verb = {"saved": "Додав картку", "updated": "Оновив картку", "exists": "Вже є в картках"}[status]
    await reply_html(
        message,
        f"📌 <b>{verb}</b>\n{ASK_TEXT[ask]}\n<b>{esc_line(front)}</b> → <b>{esc_line(back)}</b>\n\n"
        "Повторити: /review",
    )


def _parse_payload(raw: str) -> tuple[str, str, str, str] | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    ask, front, back, note = data.get("a"), data.get("f"), data.get("b"), data.get("n") or ""
    if ask not in (ASK_SAY, ASK_MEAN) or not isinstance(front, str) or not isinstance(back, str):
        return None
    if not isinstance(note, str):
        return None
    return ask, front, back, note


async def on_remember_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or not query.data:
        return
    raw = services(context).text_cache.get(query.data[4:])
    parsed = _parse_payload(raw) if raw else None
    if parsed is None:
        await query.answer("Кнопка застаріла. Надішліть слово ще раз.")
        return
    ask, front, back, note = parsed
    status = services(context).cards.save_word(user_id(update), ask, front, back, note)
    if status in ("saved", "updated", "exists"):
        track(update, context, "remember", detail=status)
    if status == "saved":
        services(context).metrics.incr("requests.remember")
    await query.answer(REMEMBER_TOAST.get(status, REMEMBER_TOAST["rejected"]))


async def _finish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = services(context)
    _due, total, nxt = svc.cards.summary(user_id(update))
    track(update, context, "review", detail="done")
    context.user_data[_SHOWN] = _CLOSED
    await _present(
        update,
        format_finished(_stats(context), total, nxt, dt.datetime.now(dt.UTC)),
        _EMPTY_MARKUP,
        edit=True,
    )


async def _stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    context.user_data[_SHOWN] = _CLOSED
    context.user_data[_QUEUE] = []
    context.user_data[_CURRENT] = None
    due_left, _total, _nxt = services(context).cards.summary(user_id(update))
    await query.answer()
    await _present(update, format_stopped(_stats(context), due_left), _EMPTY_MARKUP, edit=True)


async def _reveal(update: Update, context: ContextTypes.DEFAULT_TYPE, card_id: int) -> None:
    query = update.callback_query
    card = services(context).cards.get(user_id(update), card_id)
    if card is None:
        await query.answer("Картку не знайдено.")
        return
    await query.answer()
    await _present(update, format_back(card, _left(context, card_id)), grade_keyboard(card), edit=True)


async def _grade(update: Update, context: ContextTypes.DEFAULT_TYPE, card_id: int, grade: int) -> None:
    query = update.callback_query
    svc = services(context)
    uid = user_id(update)
    # Set the lock before the first await so a double tap in this process
    # cannot apply the same grade twice. The database check covers a race
    # across processes.
    if context.user_data.get(_SHOWN) == _CLOSED:
        await query.answer("Вже оцінено.")
        return
    card = svc.cards.get(uid, card_id)
    if card is None:
        await query.answer("Картку не знайдено.")
        return
    shown = context.user_data.get(_SHOWN)
    if shown not in (None, _token(card)):
        await query.answer("Вже оцінено.")
        return
    context.user_data[_SHOWN] = _CLOSED
    updated = svc.cards.grade(uid, card_id, grade)
    if updated is None:
        await query.answer("Вже оцінено.")
        return
    name = GRADE_NAMES[grade]
    stats = _stats(context)
    stats[name] = stats.get(name, 0) + 1
    track(update, context, "review", detail=name)
    if grade == GRADE_AGAIN:
        toast = "Знову — ще раз у цій сесії"
    else:
        toast = f"{GRADE_LABELS[grade]} · {_when(updated.due_at, dt.datetime.now(dt.UTC))}"
    await query.answer(toast)
    nxt = _advance(context, card_id, grade, [item.id for item in svc.cards.due(uid)])
    if nxt is None:
        await _finish(update, context)
        return
    nxt_card = svc.cards.get(uid, nxt)
    if nxt_card is None:
        await _finish(update, context)
        return
    context.user_data[_SHOWN] = _token(nxt_card)
    await _present(update, format_front(nxt_card, _left(context, nxt)), show_keyboard(nxt), edit=True)


async def on_review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """rv:s:<id> reveals, rv:1..4:<id> grades, rv:x stops the session."""
    query = update.callback_query
    if query is None or not query.data:
        return
    parts = query.data.split(":")
    if len(parts) == 2 and parts[1] == "x":
        await _stop(update, context)
        return
    if len(parts) != 3 or not parts[2].isdigit():
        await query.answer()
        return
    action, card_id = parts[1], int(parts[2])
    if action == "s":
        await _reveal(update, context, card_id)
        return
    if action.isdigit() and int(action) in GRADES:
        await _grade(update, context, card_id, int(action))
        return
    await query.answer()
