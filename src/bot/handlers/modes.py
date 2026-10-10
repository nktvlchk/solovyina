"""Learner modes on a persistent reply keyboard.

* Як сказати?   — Russian/mixed phrase -> natural Ukrainian
* Перевір мене  — check Ukrainian text, explain 1–3 important mistakes
* Поясни слово  — meaning, stress, example, audio
* Мої помилки   — mistakes the user confirmed with "✅ Це помилка"

A mode applies to the NEXT message only, so the default behavior
(plain translation) is never "stuck" in a mode.
"""

from __future__ import annotations

import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.formatting import esc
from bot.handlers.util import reply_html, services, user_id
from bot.language import Direction, Lang, detect_language
from bot.models.translation import SOURCE_LABELS
from bot.services.translation import TranslationUnavailable

SAY, CHECK, EXPLAIN, MISTAKES = "say", "check", "explain", "mistakes"
MODE_KEY = "mode"

BUTTONS = {
    "💬 Як сказати?": SAY,
    "✍️ Перевір мене": CHECK,
    "📖 Поясни слово": EXPLAIN,
    "🗂 Мої помилки": MISTAKES,
}

PROMPTS = {
    SAY: "💬 Надішліть фразу російською або змішану — підкажу природний український варіант.",
    CHECK: "✍️ Надішліть текст українською — перевірю й поясню до трьох головних помилок.",
    EXPLAIN: "📖 Надішліть одне слово — покажу значення, наголос, приклад і озвучку.",
}


def modes_keyboard() -> ReplyKeyboardMarkup:
    labels = list(BUTTONS)
    return ReplyKeyboardMarkup([labels[:2], labels[2:]], resize_keyboard=True, is_persistent=True)


async def select_mode(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str) -> None:
    svc = services(context)
    if mode == MISTAKES:
        await show_mistakes(update, context)
        return
    if mode == CHECK and not svc.correction.enabled:
        await update.effective_message.reply_text("🔒 Перевірка тексту зараз вимкнена.")
        return
    context.user_data[MODE_KEY] = mode
    await update.effective_message.reply_text(PROMPTS[mode])


async def cmd_say(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(context.args).strip() if context.args else ""
    if not text:
        await select_mode(update, context, SAY)
        return
    await run_say(update, context, text)


async def cmd_mistakes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_mistakes(update, context)


async def run_say(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    from bot.handlers.translation import guard

    if not await guard(update, context, text):
        return
    svc = services(context)
    started = time.monotonic()
    try:
        async with svc.concurrency():
            result = await svc.translation.natural_ukrainian(text)
    except TranslationUnavailable:
        await reply_html(
            update.effective_message, "😔 Зараз не вдалося підібрати варіант. Спробуйте пізніше."
        )
        return
    svc.metrics.incr("requests.say", int((time.monotonic() - started) * 1000))
    reply = (
        f"<b>{esc(text)}</b>\n\n🇺🇦 {esc(result.text)}\n\n<i>Джерело: {esc(SOURCE_LABELS[result.source])}</i>"
    )
    markup = None
    if len(result.text) <= svc.settings.max_tts_length:
        key = svc.text_cache.put(result.text)
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔊 Озвучити", callback_data=f"tts:{key}")]])
    await reply_html(update.effective_message, reply, reply_markup=markup)


async def run_explain(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    from bot.handlers.translation import _translate_and_reply, guard

    if len(text.split()) != 1:
        await update.effective_message.reply_text("📖 Надішліть, будь ласка, одне слово.")
        return
    if not await guard(update, context, text):
        return
    svc = services(context)
    detection = detect_language(text, svc.dictionary.has_uk, svc.dictionary.has_ru)
    # A learner usually asks about a Ukrainian word; Russian words get the Ukrainian equivalent.
    direction = Direction.RU_UK if detection.lang is Lang.RU else Direction.UK_RU
    await _translate_and_reply(update, context, text, direction, detected=True)


async def show_mistakes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = services(context)
    items = svc.corrections.confirmed_for_user(user_id(update), limit=10)
    if not items:
        await reply_html(
            update.effective_message,
            "🗂 Тут з'являться помилки, які ви підтвердили кнопкою «✅ Це помилка» після перевірки тексту.",
        )
        return
    lines = ["🗂 <b>Мої помилки</b> (останні підтверджені):", ""]
    for i, c in enumerate(items, 1):
        lines.append(f"{i}. <s>{esc(c.original)}</s> → <b>{esc(c.replacement)}</b>")
        lines.append(f"   <i>{esc(c.explanation)}</i>")
    await reply_html(update.effective_message, "\n".join(lines))


async def dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """Handle a mode button or a pending mode. Returns True if handled."""
    if text in BUTTONS:
        context.user_data.pop(MODE_KEY, None)
        context.user_data.pop("await_check", None)
        await select_mode(update, context, BUTTONS[text])
        return True
    mode = context.user_data.pop(MODE_KEY, None)
    if context.user_data.pop("await_check", False):
        mode = CHECK
    if mode == CHECK:
        from bot.handlers.correction import run_check

        await run_check(update, context, text)
        return True
    if mode == SAY:
        await run_say(update, context, text)
        return True
    if mode == EXPLAIN:
        await run_explain(update, context, text)
        return True
    return False
