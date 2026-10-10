from __future__ import annotations

import logging
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.formatting import esc, esc_line
from bot.handlers.util import has_real_user, is_private, reply_html, services, track, user_id
from bot.language import Direction, Lang, detect_language
from bot.models.translation import SOURCE_LABELS, Source, TranslationResult
from bot.services.translation import TranslationUnavailable, WordLookup, fix_case
from bot.services.tts import TtsError

logger = logging.getLogger(__name__)

DIRECTION_LABELS = {
    Direction.UK_RU: "Українська → російська",
    Direction.RU_UK: "Російська → українська",
    Direction.AUTO: "Автовизначення",
}
PENDING_KEY = "pending_text"
LAST_DIRECTION_KEY = "last_direction"


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
        track(update, context, "direction", direction=direction.value)
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
        track(update, context, "rate_limited")
        await update.effective_message.reply_text("⏳ Забагато запитів. Спробуйте за хвилину.")
        return False
    if len(text) > svc.settings.max_input_length:
        await update.effective_message.reply_text(
            f"Задовгий текст ({len(text)} символів). Максимум: {svc.settings.max_input_length}."
        )
        return False
    return True


TRANSLATE_HINT = (
    "🔁 Напишіть текст одразу після команди: /tr ваш текст\n"
    "Або відповідайте командою /tr на повідомлення, яке треба перекласти."
)


async def cmd_translate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/tr: translate on request. In groups this is the only way to get a
    translation; the bot never reacts to ordinary group messages."""
    from bot.handlers.util import command_text

    message = update.effective_message
    if message is None or not has_real_user(update):
        return
    text = command_text(update)
    reply_to = message.reply_to_message
    if not text and reply_to is not None:
        text = (reply_to.text or reply_to.caption or "").strip()
    if not text:
        await message.reply_text(TRANSLATE_HINT)
        return
    await translate_text(update, context, text)


async def translate_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    text = fix_case(text)
    if not await guard(update, context, text):
        return
    svc = services(context)
    direction, detected = _resolve_direction(svc, user_id(update), text)
    # Main path: Claude detects ru/uk itself and translates into the other language.
    use_auto = svc.user_settings.get_direction(user_id(update)) is Direction.AUTO and not (
        _exact_dictionary_word(svc, text, direction)
    )
    if use_auto and await _auto_translate_and_reply(update, context, text):
        return
    guessed = False
    if direction is Direction.AUTO:
        # Not sure about the language: don't interrupt the user with a question.
        # Use their last direction (most users type Russian and want Ukrainian)
        # and, if that yields nothing, try the opposite direction.
        direction = context.user_data.get(LAST_DIRECTION_KEY, Direction.RU_UK)
        guessed = True
    await _translate_and_reply(update, context, text, direction, detected, guessed=guessed)


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
    await translate_text(update, context, text)


def _exact_dictionary_word(svc, text: str, direction: Direction) -> bool:
    """A single word with an exact dictionary hit: answered locally (instant,
    free, several meanings, stress from Wiktionary)."""
    if len(text.split()) != 1 or direction is Direction.AUTO:
        return False
    lookup = svc.dictionary.lookup_uk if direction is Direction.UK_RU else svc.dictionary.lookup_ru
    entries, form = lookup(text)
    return bool(entries) and form is None


async def _auto_translate_and_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    svc = services(context)
    started = time.monotonic()
    async with svc.concurrency():
        auto = await svc.translation.translate_auto(text, user_id(update))
    if auto is None:
        return False
    result, direction = auto
    context.user_data[LAST_DIRECTION_KEY] = direction
    elapsed = int((time.monotonic() - started) * 1000)
    svc.metrics.incr("requests.auto", elapsed)
    track(
        update,
        context,
        "translate",
        detail="auto",
        source=result.source.value,
        direction=direction.value,
        ok=True,
        latency_ms=elapsed,
        chars=len(text),
    )
    reply = format_phrase(text, result)
    tts_text = result.text if direction is Direction.RU_UK else text
    markup = None
    if tts_text and len(tts_text) <= svc.settings.max_tts_length:
        key = svc.text_cache.put(tts_text)
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔊 Озвучити", callback_data=f"tts:{key}")]])
    await reply_html(update.effective_message, reply, reply_markup=markup)
    return True


async def _translate_and_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    direction: Direction,
    detected: bool,
    guessed: bool = False,
) -> None:
    svc = services(context)
    message = update.effective_message
    is_word = len(text.split()) == 1
    started = time.monotonic()
    # When the language was only guessed, try the opposite direction too.
    attempts = [direction]
    if guessed:
        attempts.append(Direction.UK_RU if direction is Direction.RU_UK else Direction.RU_UK)
    reply = tts_text = source = None
    async with svc.concurrency():
        for attempt in attempts:
            try:
                if is_word:
                    lookup = await svc.translation.translate_word(text, attempt, user_id(update))
                    reply, tts_text = format_word(lookup), lookup.uk_word
                    source = lookup.result.source.value if lookup.result else None
                else:
                    result = await svc.translation.translate_phrase(text, attempt, user_id(update))
                    reply = format_phrase(text, result)
                    tts_text = result.text if attempt is Direction.RU_UK else text
                    source = result.source.value
            except TranslationUnavailable:
                continue
            direction = attempt
            break
    elapsed = int((time.monotonic() - started) * 1000)
    track(
        update,
        context,
        "translate",
        detail="word" if is_word else "phrase",
        source=source,
        direction=direction.value,
        ok=reply is not None,
        latency_ms=elapsed,
        chars=len(text),
    )
    if reply is None:
        svc.metrics.incr("provider_error.all")
        await reply_html(
            message,
            "😔 Зараз не вдалося перекласти: зовнішні сервіси недоступні, "
            "а в локальному словнику цього немає. Спробуйте пізніше або інше написання.",
        )
        return
    context.user_data[LAST_DIRECTION_KEY] = direction
    svc.metrics.incr(
        "requests.word" if is_word else "requests.phrase",
        int((time.monotonic() - started) * 1000),
    )
    if detected and is_private(update):
        label = esc(DIRECTION_LABELS[direction])
        reply += f"\n\n<i>{label}. Закріпити інший напрям: /direction</i>"
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
        track(update, context, "tts", ok=False)
        await query.message.reply_text("⚠️ Не вдалося озвучити. Спробуйте пізніше.")
        return
    await query.message.reply_voice(voice=audio, caption=f"🔊 {text[:100]}")
    elapsed = int((time.monotonic() - started) * 1000)
    svc.metrics.incr("tts.requests", elapsed)
    track(update, context, "tts", ok=True, latency_ms=elapsed, chars=len(text))
