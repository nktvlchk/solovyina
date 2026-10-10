from __future__ import annotations

from telegram import Message, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.app import Services
from bot.formatting import split_message


def services(context: ContextTypes.DEFAULT_TYPE) -> Services:
    return context.application.bot_data["services"]


def user_id(update: Update) -> int:
    return update.effective_user.id if update.effective_user else 0


async def reply_html(message: Message, text: str, reply_markup=None) -> Message:
    """Send HTML, splitting long replies; the keyboard goes on the last part."""
    parts = split_message(text)
    sent = None
    for i, part in enumerate(parts):
        markup = reply_markup if i == len(parts) - 1 else None
        sent = await message.reply_text(
            part,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    return sent
