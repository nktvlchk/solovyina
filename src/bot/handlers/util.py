from __future__ import annotations

import logging

from telegram import Message, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.app import Services
from bot.formatting import split_message


def services(context: ContextTypes.DEFAULT_TYPE) -> Services:
    return context.application.bot_data["services"]


def user_id(update: Update) -> int:
    return update.effective_user.id if update.effective_user else 0


def has_real_user(update: Update) -> bool:
    """False for channel posts and anonymous group admins: they share one
    Telegram ID, so per-user settings, limits and data would be mixed."""
    user = update.effective_user
    message = update.effective_message
    if user is None:
        return False
    return getattr(message, "sender_chat", None) is None


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


def track(update: Update, context: ContextTypes.DEFAULT_TYPE, event: str, **props) -> None:
    """Record one product-analytics event. Never breaks the user flow."""
    svc = services(context)
    if svc.analytics is None or not has_real_user(update):
        return
    try:
        svc.analytics.track(user_id(update), event, **props)
    except Exception as exc:  # analytics must never break the bot
        logging.getLogger(__name__).warning("analytics failed: %s", type(exc).__name__)
