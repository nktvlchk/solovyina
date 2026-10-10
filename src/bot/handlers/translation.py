from __future__ import annotations

import logging
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.formatting import esc, esc_line
from bot.handlers.util import has_real_user, reply_html, services, user_id
from bot.language import Direction, Lang, detect_language
from bot.models.translation import SOURCE_LABELS, Source, TranslationResult
from bot.services.translation import TranslationUnavailable, WordLookup
from bot.services.tts import TtsError

logger = logging.getLogger(__name__)

DIRECTION_LABELS = {
    Direction.UK_RU: "Українська → російська",
    Direction.RU_UK: "Російська → українська",
    Direction.AUTO: "Автовизначення",
}
PENDING_KEY = "pending_text"


def direction_keyboard(prefix: str, include_auto: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(DIRECTION_LABELS[Direction.UK_RU], callback_data=f"{prefix}:uk-ru")],
        [InlineKeyboardButton(DIRECTION_LABELS[Direction.RU_UK], callback_data=f"{prefix}:ru-uk")],
    ]
    if include_auto:
        rows.append([InlineKeyboardButton(DIRECTION_LABELS[Direction.AUTO], callback_data=f"{prefix}:auto")])
    return InlineKeyboardMarkup(rows)


async def cmd_direction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = services(context)
    current = svc.user_settings.get_direction(user_id(update))
    await reply_html(
        update.effective_message,
        f"🧭 Зараз: <b>{esc_line(DIRECTION_LABELS[current])}</b>.\nОберіть напрям перекладу:",
        reply_markup=direction_keyboard("dir0", include_auto=True),
    )


async def on_direction_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """dir0:<d> saves the preference; dir1:<d> applies to the pending text only."""
    query = update.callback_query
    await query.answer()
    prefix, _, value = query.data.partition(":")
    try:
        direction = Direction(value)
    except ValueError:
        return
    svc = services(context)
    if prefix == "dir0":
        svc.user_settings.set_direction(user_id(update), direction)
        await query.edit_message_text(f"✅ Напрям: {DIRECTION_LABELS[direction]}")
        return
    text = context.user_data.pop(PENDING_KEY, None)
    if not text or direction is Direction.AUTO:
        await query.edit_message_text("Надішліть текст ще раз, будь ласка.")
        return
    await query.edit_message_text(f"Напрям: {DIRECTION_LABELS[direction]}")
    await _translate_and_reply(update, context, text, direction, detected=False)


def _resolve_direction(svc, uid: int, text: str) -> tuple[Direction, bool]:
    """Returns (direction, detected_automatically). AUTO means: ask the user."""
    saved = svc.user_settings.get_direction(uid)
    if saved is not Direction.AUTO:
        return saved, False
    detection = detect_language(text, svc.dictionary.has_uk, svc.dictionary.has_ru)
    if detection.lang is Lang.UNKNOWN:
        return Direction.AUTO, True
    return Direction.from_source(detection.lang), True


async def guard(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """Shared input checks. Returns False if the request must not proceed."""
    svc = services(context)
    uid = user_id(update)
    if not has_real_user(update):
        return False
    if not svc.rate_limiter.allow(uid):
        svc.metrics.incr("rate_limited")
        await update.effective_message.reply_text("⏳ Забагато запитів. Спробуйте за хвилину.")
        return False
    if len(text) > svc.settings.max_input_length:
        await update.effective_message.reply_text(
            f"Задовгий текст ({len(text)} символів). Максимум: {svc.settings.max_input_length}."
        )
        return False
    return True


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not message.text:
        return
    text = message.text.strip()
    if not text or not has_real_user(update):
        return

    # Mode buttons and one-shot modes (/check, /say, Поясни слово)
    from bot.handlers.modes import dispatch

    if await dispatch(update, context, text):
        return

    if not await guard(update, context, text):
        return
    svc = services(context)
    direction, detected = _resolve_direction(svc, user_id(update), text)
    if direction is Direction.AUTO:
        context.user_data[PENDING_KEY] = text  # in memory only
        await reply_html(
            message,
            "🤔 Не можу впевнено визначити мову. Оберіть напрям:",
            reply_markup=direction_keyboard("dir1", include_auto=False),
        )
        return
    await _translate_and_reply(update, context, text, direction, detected)


async def _translate_and_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    direction: Direction,
    detected: bool,
) -> None:
    svc = services(context)
    message = update.effective_message
    is_word = len(text.split()) == 1
    started = time.monotonic()
    async with svc.concurrency():
        try:
            if is_word:
                lookup = await svc.translation.translate_word(text, direction, user_id(update))
                reply, tts_text = format_word(lookup), lookup.uk_word
            else:
                result = await svc.translation.translate_phrase(text, direction, user_id(update))
                reply = format_phrase(text, result)
                tts_text = result.text if direction is Direction.RU_UK else text
        except TranslationUnavailable:
            svc.metrics.incr("provider_error.all")
            await reply_html(
                message,
                "😔 Зараз не вдалося перекласти: зовнішні сервіси недоступні, "
                "а в локальному словнику цього немає. Спробуйте пізніше або інше написання.",
            )
            return
    svc.metrics.incr(
        "requests.word" if is_word else "requests.phrase",
        int((time.monotonic() - started) * 1000),
    )
    if detected:
        label = esc(DIRECTION_LABELS[direction])
        reply += f"\n\n<i>Напрям визначено автоматично: {label}. Змінити: /direction</i>"
    markup = None
    if tts_text and len(tts_text) <= svc.settings.max_tts_length:
        key = svc.text_cache.put(tts_text)
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔊 Озвучити", callback_data=f"tts:{key}")]])
    await reply_html(message, reply, reply_markup=markup)


def _source_line(result: TranslationResult) -> str:
    label = SOURCE_LABELS[result.source]
    if result.metadata.get("approximate"):
        label += ", приблизний збіг"
    return f"<i>Джерело: {esc(label)}</i>"


def format_word(lookup: WordLookup) -> str:
    lines = [f"<b>{esc_line(lookup.query)}</b>"]
    if lookup.matched_form:
        lines.append(f"<i>(форма слова «{esc(lookup.matched_form)}»)</i>")
    lines.append("")
    if lookup.result and lookup.result.source is Source.DICTIONARY:
        for e in lookup.entries[:6]:
            target = e.ru if lookup.direction is Direction.UK_RU else e.uk
            tag = " ".join(x for x in (e.pos, e.gender) if x)
            lines.append(f"• {esc(target)}" + (f" <i>{esc_line(tag)}</i>" if tag else ""))
        example = next((e.example for e in lookup.entries if e.example), "")
        if example:
            lines.append(f"💬 <i>{esc_line(example)}</i>")
    elif lookup.result and lookup.result.source is not Source.WIKTIONARY:
        lines.append(esc(lookup.result.text))
    if lookup.result:
        lines.append(_source_line(lookup.result))

    w = lookup.wiktionary
    if w:
        lines.append("")
        head = f"📖 <b>{esc_line(w.word)}</b>"
        if w.pos:
            head += f" <i>{esc_line(w.pos)}</i>"
        if w.pronunciation:
            head += f"  🔉 [{esc(w.pronunciation)}]"
        lines.append(head)
        for i, d in enumerate(w.definitions[:3], 1):
            lines.append(f"{i}. {esc(d)}")
        if w.synonyms:
            lines.append(f"🔹 Синоніми: {esc(', '.join(w.synonyms))}")
        if w.examples:
            lines.append(f"💬 <i>{esc_line(w.examples[0])}</i>")
        lines.append(f'<i>Wiktionary, CC BY-SA 4.0: <a href="{esc(w.url)}">сторінка</a></i>')
    return "\n".join(lines)


def format_phrase(original: str, result: TranslationResult) -> str:
    return f"<b>{esc_line(original)}</b>\n\n{esc(result.text)}\n\n{_source_line(result)}"


async def on_tts_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    svc = services(context)
    text = svc.text_cache.get(query.data.partition(":")[2])
    if not text:
        await query.answer("Кнопка застаріла. Надішліть текст ще раз.", show_alert=False)
        return
    if not svc.rate_limiter.allow(user_id(update)):
        await query.answer("⏳ Забагато запитів.")
        return
    await query.answer()
    started = time.monotonic()
    try:
        async with svc.concurrency():
            audio = await svc.tts.synthesize(text)
    except TtsError:
        svc.metrics.incr("provider_error.tts")
        await query.message.reply_text("⚠️ Не вдалося озвучити. Спробуйте пізніше.")
        return
    await query.message.reply_voice(voice=audio, caption=f"🔊 {text[:100]}")
    svc.metrics.incr("tts.requests", int((time.monotonic() - started) * 1000))
