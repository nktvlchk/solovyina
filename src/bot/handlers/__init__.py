from __future__ import annotations

import contextlib
import logging

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.handlers import admin, common, correction, feedback, modes, review, translation

logger = logging.getLogger(__name__)

# Private chats get everything. In groups (e.g. a class chat where the same
# bot posts study cards) the bot stays silent unless asked: only /tr, /check,
# /say and the admin-only /del work; ordinary messages are never translated or processed.
PRIVATE = filters.ChatType.PRIVATE
ANY_CHAT = filters.ChatType.PRIVATE | filters.ChatType.GROUPS


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Exception class only: tracebacks may contain user text.
    logger.error("unhandled error: %s", type(context.error).__name__)
    if isinstance(update, Update) and update.effective_message:
        with contextlib.suppress(Exception):
            await update.effective_message.reply_text("⚠️ Щось пішло не так. Спробуйте ще раз пізніше.")


def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", common.cmd_start, filters=PRIVATE))
    app.add_handler(CommandHandler("help", common.cmd_help, filters=PRIVATE))
    app.add_handler(CommandHandler("privacy", common.cmd_privacy, filters=PRIVATE))
    app.add_handler(CommandHandler("about", common.cmd_about, filters=PRIVATE))
    app.add_handler(CommandHandler("delete_my_data", common.cmd_delete_my_data, filters=PRIVATE))
    app.add_handler(CommandHandler("direction", translation.cmd_direction, filters=PRIVATE))
    app.add_handler(CommandHandler("check", correction.cmd_check, filters=ANY_CHAT))
    app.add_handler(CommandHandler("say", modes.cmd_say, filters=ANY_CHAT))
    app.add_handler(CommandHandler("del", admin.cmd_delete_bot_message, filters=ANY_CHAT))
    app.add_handler(CommandHandler(["tr", "translate"], translation.cmd_translate, filters=ANY_CHAT))
    app.add_handler(CommandHandler("mistakes", modes.cmd_mistakes, filters=PRIVATE))
    app.add_handler(CommandHandler("review", review.cmd_review, filters=PRIVATE))
    app.add_handler(CommandHandler("remember", review.cmd_remember, filters=PRIVATE))
    app.add_handler(CommandHandler("stats", admin.cmd_stats, filters=PRIVATE))
    app.add_handler(CommandHandler("export_stats", admin.cmd_export_stats, filters=PRIVATE))

    app.add_handler(CallbackQueryHandler(common.on_delete_callback, pattern=r"^del:"))
    app.add_handler(CallbackQueryHandler(translation.on_direction_callback, pattern=r"^dir[01]:"))
    app.add_handler(CallbackQueryHandler(translation.on_tts_callback, pattern=r"^tts:"))
    app.add_handler(CallbackQueryHandler(feedback.on_feedback_callback, pattern=r"^fb:"))
    app.add_handler(CallbackQueryHandler(review.on_review_callback, pattern=r"^rv:"))
    app.add_handler(CallbackQueryHandler(review.on_remember_callback, pattern=r"^mem:"))

    app.add_handler(MessageHandler(PRIVATE & filters.TEXT & ~filters.COMMAND, translation.on_text))
    app.add_error_handler(on_error)
