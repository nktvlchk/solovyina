"""Redirect mode for a retired bot.

Run the same code with the OLD bot's token and REDIRECT_TO_BOT=@new_bot:
every message, command or button press gets a short "we moved" reply with a
link. No dictionary, database or external services are loaded.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


def moved_text(new_bot: str) -> str:
    return (
        "🐦 Бот переїхав!\n\n"
        f"Тепер я працюю тут: @{new_bot}\n"
        "Там усе те саме і навіть більше: переклад, перевірка тексту, пояснення помилок.\n\n"
        "Натисніть кнопку нижче, щоб продовжити."
    )


def moved_keyboard(new_bot: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(f"Перейти до @{new_bot}", url=f"https://t.me/{new_bot}")]]
    )


def build_redirect_application(token: str, new_bot: str) -> Application:
    async def on_any(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.callback_query:
            await update.callback_query.answer()
        message = update.effective_message
        if message is not None:
            await message.reply_text(moved_text(new_bot), reply_markup=moved_keyboard(new_bot))

    async def post_init(app: Application) -> None:
        await app.bot.set_my_commands([("start", "Бот переїхав")])
        await app.bot.set_my_short_description(f"Бот переїхав: @{new_bot}")

    app = ApplicationBuilder().token(token).post_init(post_init).build()
    app.add_handler(MessageHandler(filters.ALL, on_any))
    app.add_handler(CallbackQueryHandler(on_any))
    return app
