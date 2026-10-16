from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot import __version__
from bot.handlers.modes import modes_keyboard
from bot.handlers.util import reply_html, services, track, user_id

PROJECT_URL = "https://github.com/nktvlchk/solovyina"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = services(context)
    track(update, context, "start")
    text = (
        "👋 <b>Солов'їна</b> — помічник для переходу на українську\n\n"
        "Надішліть слово чи фразу — перекладу між українською та російською "
        "і покажу, звідки взято відповідь.\n"
        f"📚 Локальний словник: {len(svc.dictionary):,} українських слів.\n\n"
    )
    if svc.correction.enabled:
        text += "✍️ /check — перевірю ваш український текст і поясню 1–3 головні помилки.\n"
    text += (
        "💬 «Як сказати?» — природний український варіант російської чи змішаної фрази.\n"
        "📖 «Поясни слово» (/explain) — значення слова або вислову українською.\n"
        "🖼 Фото — прочитаю напис і коротко поясню слова.\n"
        "🗂 «Мої помилки» — помилки, які ви підтвердили.\n"
        "🃏 «Повторити» — картки за цими помилками і словами, які ви просите запам'ятати.\n"
        "🎯 «Тренування» (/practice) — до п'яти завдань: відповідь треба написати самому.\n"
        "📈 /progress — скільки вийшло за останні 7 днів.\n"
        "🧭 /direction — вибрати напрям перекладу.\n"
        "🔒 /privacy — що бот обробляє та зберігає.\n\n"
        "Бот не замінює викладача чи професійного перекладача."
    )
    await reply_html(update.effective_message, text, reply_markup=modes_keyboard())


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = services(context)
    max_len = svc.settings.max_input_length
    text = (
        "📖 <b>Як користуватися</b>\n\n"
        "• Просто напишіть фразу російською або українською — перекладу в інший бік. "
        "Мову визначає Claude, перекладає природно.\n"
        "• Звичайне повідомлення з одним словом — переклад із локального словника і Wiktionary.\n"
        "• «Поясни слово» або /explain — визначення слова чи короткого вислову "
        "українською, а не переклад російською.\n"
        "• Фото в особистому чаті — прочитаю, що на ньому, і коротко поясню українські слова. "
        "Зображення не зберігається.\n"
        "• Якщо Claude недоступний — Google Translate або MyMemory.\n"
        "• Під відповіддю завжди вказано фактичне джерело.\n"
        "• 🔊 — озвучка лише за натисканням кнопки (Google TTS).\n\n"
        "Мову визначаю автоматично лише як підказку. Якщо не впевнений — запитаю. "
        "Напрям можна закріпити: /direction.\n\n"
        "🃏 <b>/review</b> — повторення картками: спочатку питання, потім відповідь, "
        "далі «Знову / Важко / Добре / Легко». У колоді — підтверджені помилки і слова "
        "з кнопки «📌 Запам'ятати» або /remember.\n"
        "🎯 <b>/practice</b> — до п'яти завдань з карток, які вже час повторити. "
        "Напишіть відповідь: якщо вона збігається, картка відкладається; якщо ні — "
        "покажу правильний варіант і пояснення. Кнопка «Не знаю» теж підходить.\n"
        "📈 <b>/progress</b> — вірні відповіді за 7 днів і найчастіші типи помилок.\n\n"
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
        "<b>Що обробляється:</b> текст, який ви надсилаєте, фото в особистому чаті, "
        "повідомлення викладача з класового чату, якщо це увімкнено, "
        "і ваш числовий Telegram ID.\n\n"
        "<b>Кому передається текст:</b>\n"
        "• Wiktionary (ru.wiktionary.org) — окреме слово;\n"
        "• Google Translate і MyMemory (translated.net) — запасний переклад;\n"
        "• Anthropic — для /check, словникових пояснень, природних формулювань, "
        "фото, яке ви надсилаєте для розбору, "
        "і матеріал уроку, з якого збираються картки розсилки, "
        "і як останній варіант перекладу "
        "(лише якщо функцію увімкнено);\n"
        "• Google TTS — лише коли ви натискаєте 🔊.\n\n"
        "<b>Що зберігається:</b>\n"
        "• вибраний напрям перекладу;\n"
        "• для /check — лише виправлені фрагменти (було / стало / пояснення) "
        "і ваша оцінка кнопками;\n"
        "• картки повторення: слово або фрагмент помилки, відповідь і коли повторити;\n"
        "• результати тренувань: лише «вірно» чи «ні», без тексту, який ви написали;\n"
        "• номери карток останньої партії в колоді уроків, без тексту уроку;\n"
        f"• повний текст повідомлення: {raw};\n"
        "• журнал дій для покращення бота: яка функція, джерело, час відповіді, оцінки — "
        "під псевдонімом, без вашого Telegram ID і без текстів повідомлень;\n"
        "• агреговані лічильники.\n\n"
        "<b>Що НЕ зберігається:</b> ім'я, username, історія повідомлень, "
        "фото і підписи до них.\n\n"
        f"<b>Строк зберігання:</b> {svc.settings.retention_days} днів, потім автоматичне видалення. "
        "Картки, які ви повторюєте, лишаються, доки ви ними користуєтесь. "
        "Результати тренувань видаляються разом із рештою даних.\n"
        "<b>Видалити все зараз:</b> /delete_my_data"
    )
    await reply_html(update.effective_message, text)


async def cmd_about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        f"ℹ️ <b>Солов'їна</b> (v{__version__})\n\n"
        "Telegram-помічник для російськомовних, які переходять на українську: "
        "писати природніше, розуміти свої помилки й запам'ятовувати виправлення.\n\n"
        "<b>Джерела даних:</b>\n"
        "• Apertium rus-ukr — GPL-2.0, github.com/apertium/apertium-rus-ukr\n"
        "• Wiktionary — CC BY-SA 4.0\n"
        "• Google Translate, MyMemory — запасний переклад\n"
        "• Anthropic Claude — перевірка тексту та пояснення слів (може помилятися)\n\n"
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
        "Видалити всі збережені дані про вас (налаштування, виправлення, оцінки, слова, "
        "картки, результати тренувань)? "
        "Це незворотно.",
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
    if svc.analytics is not None:
        counts["events"] = svc.analytics.delete_user(user_id(update))
    context.user_data.clear()
    total = sum(counts.values())
    await query.edit_message_text(
        f"✅ Видалено записів: {total}. Агреговані лічильники не містять ваших даних."
    )
