from __future__ import annotations

import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.formatting import esc, esc_line
from bot.handlers.util import command_text, is_private, reply_html, services, track, user_id
from bot.models.correction import CheckResult, Correction, Severity, Verdict
from bot.services.correction import CheckUnavailable

TYPE_LABELS = {
    "spelling": "орфографія",
    "grammar": "граматика",
    "calque": "калька з російської",
    "punctuation": "пунктуація",
    "vocabulary": "слововживання",
    "style": "стиль",
}
VERDICT_LABELS = {
    Verdict.ERROR: "✅ Це помилка",
    Verdict.NOT_ERROR: "❌ Тут немає помилки",
    Verdict.STYLE: "✍️ Лише стиль",
}
VERDICT_CODES = {Verdict.ERROR: "e", Verdict.NOT_ERROR: "n", Verdict.STYLE: "s"}
CODE_TO_VERDICT = {v: k for k, v in VERDICT_CODES.items()}

GROUP_CHECK_HINT = (
    "✍️ Напишіть текст одразу після команди: /check Ваш текст\n"
    "Або відповідайте командою /check на своє повідомлення."
)

UNAVAILABLE_MESSAGES = {
    "disabled": "🔒 Перевірка тексту вимкнена: не налаштовано мовну модель.",
    "budget": "⏳ Денний ліміт перевірок вичерпано. Спробуйте завтра.",
    "provider": "😔 Сервіс перевірки зараз недоступний. Спробуйте пізніше.",
    "parse": "😔 Не вдалося коректно обробити відповідь моделі. Спробуйте ще раз.",
}


def feedback_keyboard(correction_id: str, chosen: Verdict | None = None) -> InlineKeyboardMarkup:
    def button(verdict: Verdict) -> InlineKeyboardButton:
        label = VERDICT_LABELS[verdict]
        if verdict is chosen:
            label = f"● {label}"
        return InlineKeyboardButton(label, callback_data=f"fb:{correction_id}:{VERDICT_CODES[verdict]}")

    return InlineKeyboardMarkup([[button(Verdict.ERROR), button(Verdict.STYLE)], [button(Verdict.NOT_ERROR)]])


def format_summary(result: CheckResult) -> str:
    if not result.corrections:
        return "✅ <b>Помилок не знайдено.</b>\n\n<i>Перевірка мовною моделлю, вона може помилятися.</i>"
    errors = sum(1 for c in result.corrections if c.severity is Severity.ERROR)
    tips = len(result.corrections) - errors
    head = []
    if errors:
        head.append(f"помилок: {errors}")
    if tips:
        head.append(f"стилістичних порад: {tips}")
    return (
        f"✍️ <b>Виправлений варіант</b> ({esc(', '.join(head))}):\n"
        f"{esc(result.corrected_text)}\n\n"
        "<i>Нижче — кожне виправлення окремо. Оцініть, будь ласка: це допомагає міряти якість. "
        "Перевірка мовною моделлю, вона може помилятися.</i>"
    )


def format_correction(index: int, c: Correction) -> str:
    kind = "Помилка" if c.severity is Severity.ERROR else "Порада"
    label = TYPE_LABELS.get(c.type.value, c.type.value)
    return (
        f"{index}. <b>{kind}</b> · <i>{esc_line(label)}</i>\n"
        f"<s>{esc_line(c.original)}</s> → <b>{esc_line(c.replacement)}</b>\n"
        f"{esc(c.explanation)}"
    )


async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = services(context)
    if not svc.correction.enabled:
        await update.effective_message.reply_text(UNAVAILABLE_MESSAGES["disabled"])
        return
    text = command_text(update)
    reply_to = update.effective_message.reply_to_message
    if not text and reply_to and reply_to.text:
        author = reply_to.from_user.id if reply_to.from_user else None
        if author != user_id(update):
            await update.effective_message.reply_text(
                "Можна перевірити лише власне повідомлення. Надішліть текст після /check."
            )
            return
        text = reply_to.text.strip()
    if not text and not is_private(update):
        await update.effective_message.reply_text(GROUP_CHECK_HINT)
        return
    if not text:
        context.user_data["await_check"] = True
        await update.effective_message.reply_text(
            "✍️ Надішліть наступним повідомленням текст українською — перевірю його."
        )
        return
    await run_check(update, context, text)


async def run_check(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    from bot.handlers.translation import guard

    if not await guard(update, context, text):
        return
    svc = services(context)
    message = update.effective_message
    status = await message.reply_text("⏳ Перевіряю…")
    started = time.monotonic()
    try:
        async with svc.concurrency():
            result = await svc.correction.check(user_id(update), text)
    except CheckUnavailable as exc:
        track(
            update,
            context,
            "check",
            ok=False,
            chars=len(text),
            detail="budget" if exc.reason == "budget" else "unavailable",
        )
        await status.edit_text(UNAVAILABLE_MESSAGES.get(exc.reason, UNAVAILABLE_MESSAGES["provider"]))
        return
    elapsed = int((time.monotonic() - started) * 1000)
    svc.metrics.incr("requests.check", elapsed)
    track(
        update,
        context,
        "check",
        ok=True,
        latency_ms=elapsed,
        chars=len(text),
        detail="has_errors" if result.corrections else "no_errors",
    )
    await status.delete()

    markup = None
    if result.corrections and len(result.corrected_text) <= svc.settings.max_tts_length:
        key = svc.text_cache.put(result.corrected_text)
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔊 Озвучити виправлене", callback_data=f"tts:{key}")]]
        )
    await reply_html(message, format_summary(result), reply_markup=markup)
    for i, correction in enumerate(result.corrections, 1):
        await reply_html(
            message, format_correction(i, correction), reply_markup=feedback_keyboard(correction.id)
        )
