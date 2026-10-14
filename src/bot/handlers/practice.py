"""Typed practice over the personal deck, and a 7-day progress report.

One session is a fixed list of up to five due cards. A wrong answer is not
asked again in that list: it stays due for the next session or for /review.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from bot.formatting import esc_line
from bot.handlers.correction import TYPE_LABELS
from bot.handlers.review import ASK_TEXT
from bot.handlers.util import is_private, reply_html, services, track, user_id
from bot.services.practice import DAILY_SIZE, choose_daily, is_correct
from bot.services.review import ASK_FIX, GRADE_AGAIN, GRADE_GOOD
from bot.storage.repositories import Card

SESSION = "practice"

_EMPTY = (
    "🎯 <b>Карток ще немає.</b>\n\n"
    "Тренування бере їх з двох місць:\n"
    "• помилки, які ви підтвердили кнопкою «✅ Це помилка»;\n"
    "• слова й фрази з «📌 Запам'ятати» або /remember."
)
_CLOSED = "Це тренування вже закрите. Почати знову: /practice"


def clear_practice(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(SESSION, None)


def _claim(context: ContextTypes.DEFAULT_TYPE) -> tuple[dict, int] | None:
    """Take the open card before any await, so a second message cannot grade it."""
    session = context.user_data.get(SESSION)
    if not isinstance(session, dict):
        return None
    card_id = session.get("current")
    if not isinstance(card_id, int):
        return None
    session["current"] = None
    return session, card_id


def _note(card: Card) -> str:
    return f"\n<i>{esc_line(card.note)}</i>" if card.note else ""


def format_question(card: Card, step: int, total: int) -> str:
    ask = ASK_TEXT.get(card.ask, ASK_TEXT[ASK_FIX])
    return (
        f"🎯 <b>Завдання {step} з {total}</b>\n\n"
        f"{esc_line(ask)}\n\n"
        f"<b>{esc_line(card.front)}</b>\n\n"
        "Напишіть відповідь або натисніть «Не знаю»."
    )


def format_correct(card: Card) -> str:
    return f"✅ <b>Так.</b>\n\n<b>{esc_line(card.back)}</b>{_note(card)}"


def format_wrong(card: Card, given: str) -> str:
    return f"❌ <b>Ви написали:</b> {esc_line(given)}\n\nПравильно: <b>{esc_line(card.back)}</b>{_note(card)}"


def format_skip(card: Card) -> str:
    return f"Поки що так: <b>{esc_line(card.back)}</b>{_note(card)}"


def format_report(svc, uid: int, *, good: int | None = None, again: int | None = None) -> str:
    correct, total = svc.practice.week(uid)
    profile = svc.corrections.error_profile(uid)
    due, _deck, _next = svc.cards.summary(uid)
    lines = ["🎯 <b>Прогрес</b>", ""]
    if good is not None and again is not None:
        lines.append(f"Зараз: вірно {good} · ще раз {again}")
    if total:
        lines.append(f"За 7 днів: {correct} із {total} вірних.")
    else:
        lines.append("За 7 днів ще немає відповідей.")
    if profile:
        labels = [TYPE_LABELS.get(name, name) for name, _count in profile[:3]]
        lines.append("Найчастіше: " + ", ".join(labels) + ".")
    lines.append(f"Ще на сьогодні: {due}." if due else "На сьогодні все.")
    return "\n".join(lines)


def _skip_keyboard(card_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Не знаю", callback_data=f"pr:{card_id}")]])


async def _show(update: Update, text: str, *, edit: bool) -> None:
    query = update.callback_query
    if edit and query is not None:
        try:
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            return
        except BadRequest:
            pass
    if update.effective_message is not None:
        await reply_html(update.effective_message, text)


async def _ask(update: Update, context: ContextTypes.DEFAULT_TYPE, session: dict, card: Card) -> None:
    session["current"] = card.id
    await reply_html(
        update.effective_message,
        format_question(card, int(session["step"]), int(session["total"])),
        reply_markup=_skip_keyboard(card.id),
    )


async def _finish(
    update: Update, context: ContextTypes.DEFAULT_TYPE, session: dict, result: str, *, edit: bool
) -> None:
    await _show(update, result, edit=edit)
    rest = session.get("rest")
    if not isinstance(rest, list) or not rest:
        good, again = int(session.get("good", 0)), int(session.get("again", 0))
        clear_practice(context)
        track(update, context, "practice", detail="done", ok=True)
        await reply_html(
            update.effective_message,
            format_report(services(context), user_id(update), good=good, again=again),
        )
        return
    nxt_id = rest.pop(0)
    session["rest"] = rest
    session["step"] = int(session.get("step", 1)) + 1
    card = services(context).cards.get(user_id(update), nxt_id)
    if card is None:
        clear_practice(context)
        await reply_html(update.effective_message, _CLOSED)
        return
    await _ask(update, context, session, card)


async def _apply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    session: dict,
    card: Card,
    *,
    ok: bool,
    result: str,
    edit: bool,
) -> None:
    svc = services(context)
    uid = user_id(update)
    svc.cards.grade(uid, card.id, GRADE_GOOD if ok else GRADE_AGAIN)
    svc.practice.record(uid, ok)
    if ok:
        session["good"] = int(session.get("good", 0)) + 1
    else:
        session["again"] = int(session.get("again", 0)) + 1
    track(update, context, "practice", detail="good" if ok else "again", ok=ok)
    await _finish(update, context, session, result, edit=edit)


async def start_practice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = services(context)
    uid = user_id(update)
    svc.cards.sync_mistakes(uid)
    _due, total, _next = svc.cards.summary(uid)
    if total == 0:
        track(update, context, "practice", detail="empty")
        clear_practice(context)
        await reply_html(update.effective_message, _EMPTY)
        return
    due = svc.cards.due(uid, limit=100)
    if not due:
        track(update, context, "practice", detail="done")
        clear_practice(context)
        await reply_html(update.effective_message, format_report(svc, uid))
        return
    weak = [name for name, _count in svc.corrections.error_profile(uid)]
    chosen = choose_daily(due, svc.corrections.types_by_card(uid, due), weak, DAILY_SIZE)
    svc.metrics.incr("requests.practice")
    session = {
        "current": None,
        "rest": [card.id for card in chosen[1:]],
        "step": 1,
        "total": len(chosen),
        "good": 0,
        "again": 0,
    }
    context.user_data[SESSION] = session
    await _ask(update, context, session, chosen[0])


async def cmd_practice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start_practice(update, context)


async def cmd_progress(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = services(context)
    uid = user_id(update)
    svc.cards.sync_mistakes(uid)
    track(update, context, "progress")
    await reply_html(update.effective_message, format_report(svc, uid))


async def submit_practice(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    claimed = _claim(context)
    if claimed is None:
        return
    session, card_id = claimed
    card = services(context).cards.get(user_id(update), card_id)
    if card is None:
        clear_practice(context)
        await reply_html(update.effective_message, _CLOSED)
        return
    if is_correct(text, card.back):
        await _apply(update, context, session, card, ok=True, result=format_correct(card), edit=False)
        return
    await _apply(update, context, session, card, ok=False, result=format_wrong(card, text), edit=False)


async def on_practice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or not is_private(update):
        if query is not None:
            await query.answer()
        return
    claimed = _claim(context)
    if claimed is None:
        await query.answer("Це завдання вже закрите")
        return
    session, card_id = claimed
    try:
        pressed = int(query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        session["current"] = card_id
        await query.answer()
        return
    if pressed != card_id:
        session["current"] = card_id
        await query.answer("Це інше завдання")
        return
    await query.answer()
    card = services(context).cards.get(user_id(update), card_id)
    if card is None:
        clear_practice(context)
        await reply_html(update.effective_message, _CLOSED)
        return
    await _apply(update, context, session, card, ok=False, result=format_skip(card), edit=True)
