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

from bot.handlers import admin, common, correction, feedback, modes, translation

logger = logging.getLogger(__name__)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Exception class only: tracebacks may contain user text.
    logger.error("unhandled error: %s", type(context.error).__name__)
    if isinstance(update, Update) and update.effective_message:
        with contextlib.suppress(Exception):
            await update.effective_message.reply_text("⚠️ Щось пішло не так. Спробуйте ще раз пізніше.")


def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", common.cmd_start))
    app.add_handler(CommandHandler("help", common.cmd_help))
    app.add_handler(CommandHandler("privacy", common.cmd_privacy))
    app.add_handler(CommandHandler("about", common.cmd_about))
    app.add_handler(CommandHandler("delete_my_data", common.cmd_delete_my_data))
    app.add_handler(CommandHandler("direction", translation.cmd_direction))
    app.add_handler(CommandHandler("check", correction.cmd_check))
    app.add_handler(CommandHandler("say", modes.cmd_say))
    app.add_handler(CommandHandler("mistakes", modes.cmd_mistakes))
    app.add_handler(CommandHandler("stats", admin.cmd_stats))

    app.add_handler(CallbackQueryHandler(common.on_delete_callback, pattern=r"^del:"))
    app.add_handler(CallbackQueryHandler(translation.on_direction_callback, pattern=r"^dir[01]:"))
    app.add_handler(CallbackQueryHandler(translation.on_tts_callback, pattern=r"^tts:"))
    app.add_handler(CallbackQueryHandler(feedback.on_feedback_callback, pattern=r"^fb:"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, translation.on_text))
    app.add_error_handler(on_error)
