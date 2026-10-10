from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot import __version__
from bot.handlers.util import reply_html, services, user_id

PROJECT_URL = "https://github.com/nktvlchk/ukrainian-bot"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = services(context)
    text = (
        "👋 <b>Помічник для переходу на українську</b>\n\n"
        "Надішліть слово чи фразу — перекладу між українською та російською "
        "і покажу, звідки взято відповідь.\n"
        f"📚 Локальний словник: {len(svc.dictionary):,} українських слів.\n\n"
    )
    if svc.correction.enabled:
        text += "✍️ /check — перевірю ваш український текст і поясню 1–3 головні помилки.\n"
    text += (
        "🧭 /direction — вибрати напрям перекладу.\n"
        "🔒 /privacy — що бот обробляє та зберігає.\n\n"
        "Бот не замінює викладача чи професійного перекладача."
    )
    await reply_html(update.effective_message, text)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = services(context)
    max_len = svc.settings.max_input_length
    text = (
        "📖 <b>Як користуватися</b>\n\n"
        "• <b>Слово</b>: локальний словник, потім Wiktionary, потім MyMemory; "
        "мовна модель — лише як останній варіант.\n"
        "• <b>Фраза</b>: MyMemory, а якщо недоступний — мовна модель.\n"
        "• Під відповіддю завжди вказано фактичне джерело.\n"
        "• 🔊 — озвучка лише за натисканням кнопки (Google TTS).\n\n"
        "Мову визначаю автоматично лише як підказку. Якщо не впевнений — запитаю. "
        "Напрям можна закріпити: /direction.\n\n"
    )
    if svc.correction.enabled:
        text += (
            "✍️ <b>/check</b> текст — перевірка українського тексту. Під кожним "
            "виправленням є кнопки «✅ Це помилка», «❌ Тут немає помилки», "
            "«✍️ Лише стиль» — ваша оцінка допомагає міряти якість.\n\n"
        )
    else:
        text += "✍️ Перевірка тексту зараз вимкнена (немає ключа мовної моделі).\n\n"
    text += f"Максимальна довжина повідомлення: {max_len} символів.\n/about — джерела та ліцензії."
    await reply_html(update.effective_message, text)


async def cmd_privacy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = services(context)
    raw = "так (STORE_RAW_TEXT=true)" if svc.settings.store_raw_text else "ні"
    text = (
        "🔒 <b>Приватність</b>\n\n"
        "<b>Що обробляється:</b> текст, який ви надсилаєте, і ваш числовий Telegram ID.\n\n"
        "<b>Кому передається текст:</b>\n"
        "• Wiktionary (ru.wiktionary.org) — окреме слово;\n"
        "• MyMemory (translated.net) — слово чи фраза для перекладу;\n"
        "• Anthropic — для /check, природних формулювань і як останній варіант перекладу "
        "(лише якщо функцію увімкнено);\n"
        "• Google TTS — лише коли ви натискаєте 🔊.\n\n"
        "<b>Що зберігається:</b>\n"
        "• вибраний напрям перекладу;\n"
        "• для /check — лише виправлені фрагменти (було / стало / пояснення) "
        "і ваша оцінка кнопками;\n"
        f"• повний текст повідомлення: {raw};\n"
        "• агреговані лічильники без прив'язки до людей.\n\n"
        "<b>Що НЕ зберігається:</b> ім'я, username, історія повідомлень.\n\n"
        f"<b>Строк зберігання:</b> {svc.settings.retention_days} днів, потім автоматичне видалення.\n"
        "<b>Видалити все зараз:</b> /delete_my_data"
    )
    await reply_html(update.effective_message, text)


async def cmd_about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        f"ℹ️ <b>Про проєкт</b> (v{__version__})\n\n"
        "Telegram-помічник для російськомовних, які переходять на українську: "
        "писати природніше, розуміти свої помилки й запам'ятовувати виправлення.\n\n"
        "<b>Джерела даних:</b>\n"
        "• Apertium rus-ukr — GPL-2.0, github.com/apertium/apertium-rus-ukr\n"
        "• Wiktionary — CC BY-SA 4.0\n"
        "• MyMemory — переклад фраз\n"
        "• Anthropic Claude — перевірка тексту (може помилятися)\n\n"
        f"Код: {PROJECT_URL} (GPL-2.0-or-later)\n"
        "Це навчальний інструмент, а не заміна викладачу чи перекладачу."
    )
    await reply_html(update.effective_message, text)


async def cmd_delete_my_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🗑 Так, видалити", callback_data="del:yes"),
                InlineKeyboardButton("Скасувати", callback_data="del:no"),
            ]
        ]
    )
    await reply_html(
        update.effective_message,
        "Видалити всі збережені дані про вас (налаштування, виправлення, оцінки, слова)? Це незворотно.",
        reply_markup=keyboard,
    )


async def on_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.data != "del:yes":
        await query.edit_message_text("Скасовано. Нічого не видалено.")
        return
    svc = services(context)
    counts = svc.privacy.delete_user(user_id(update))
    context.user_data.clear()
    total = sum(counts.values())
    await query.edit_message_text(
        f"✅ Видалено записів: {total}. Агреговані лічильники не містять ваших даних."
    )
