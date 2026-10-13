from __future__ import annotations

import contextlib

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from bot.handlers.correction import CODE_TO_VERDICT, VERDICT_LABELS, feedback_keyboard
from bot.handlers.util import services, track, user_id
from bot.models.correction import Verdict


async def on_feedback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """callback_data = fb:<correction_id>:<e|n|s> (well under 64 bytes)."""
    query = update.callback_query
    parts = query.data.split(":")
    if len(parts) != 3 or parts[2] not in CODE_TO_VERDICT:
        await query.answer()
        return
    correction_id, verdict = parts[1], CODE_TO_VERDICT[parts[2]]
    svc = services(context)
    uid = user_id(update)
    owner = svc.corrections.owner(correction_id)
    if owner is None:
        await query.answer("Це виправлення вже видалено.")
        return
    if owner != uid:
        await query.answer("Оцінити може лише автор тексту.")
        return
    changed = svc.corrections.set_verdict(correction_id, uid, verdict)
    if changed:
        svc.metrics.incr(f"feedback.{verdict.value}")
        track(update, context, "feedback", detail=verdict.value)
    text = f"Збережено: {VERDICT_LABELS[verdict]}"
    if changed and verdict is Verdict.ERROR:
        text += ". Буде в картках"
    await query.answer(text)
    # BadRequest: markup unchanged (same button pressed twice)
    with contextlib.suppress(BadRequest):
        await query.edit_message_reply_markup(reply_markup=feedback_keyboard(correction_id, verdict))
