"""Read one photo in a private chat and explain the Ukrainian words on it.

The file is downloaded, sent to the model, and dropped. Nothing about the
image or its caption is written to the database.
"""

from __future__ import annotations

import logging
import time

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot.formatting import esc, esc_line
from bot.handlers.util import has_real_user, reply_html, services, track, user_id
from bot.models.picture import PictureReading
from bot.services.translation import PictureUnavailable

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 4_000_000
_ALBUMS = "_photo_albums"

_MESSAGES = {
    "disabled": "🔒 Розбір зображень зараз вимкнений: не налаштовано мовну модель.",
    "budget": "⏳ Денний ліміт пояснень вичерпано. Спробуйте завтра.",
    "parse": "😔 Не вдалося надійно прочитати зображення. Спробуйте ще раз.",
    "provider": "😔 Сервіс зараз недоступний. Спробуйте пізніше.",
}
_TOO_BIG = "Фото завелике. Надішліть його як звичайне стиснуте фото в Telegram."
_DOWNLOAD_FAILED = "Не вдалося завантажити фото. Спробуйте ще раз."
_ALBUM = "Надішліть одне фото окремим повідомленням."


def format_picture(reading: PictureReading) -> str:
    lines = ["🖼 <b>На зображенні</b>", "", esc(reading.summary)]
    if reading.items:
        lines.append("")
        lines.extend(
            f"{index}. <b>{esc_line(item.word)}</b> — {esc(item.note)}"
            for index, item in enumerate(reading.items, 1)
        )
    lines += ["", "<i>Прочитано мовною моделлю (може помилятися)</i>"]
    return "\n".join(lines)


def _remember_album(context: ContextTypes.DEFAULT_TYPE, group_id: str) -> bool:
    """True when this album was already answered."""
    seen = context.user_data.setdefault(_ALBUMS, [])
    if not isinstance(seen, list):
        seen = []
        context.user_data[_ALBUMS] = seen
    if group_id in seen:
        return True
    seen.append(group_id)
    del seen[:-20]
    return False


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    photos = getattr(message, "photo", None) if message is not None else None
    if not photos or not has_real_user(update):
        return
    from bot.handlers.ingest import LEARN_KEY, ingest_private_photo

    if context.user_data.pop(LEARN_KEY, None):
        await ingest_private_photo(update, context)
        return
    group_id = getattr(message, "media_group_id", None)
    if group_id and _remember_album(context, group_id):
        return
    if group_id:
        await message.reply_text(_ALBUM)
        return

    svc = services(context)
    uid = user_id(update)
    if not svc.rate_limiter.allow(uid):
        svc.metrics.incr("rate_limited")
        track(update, context, "rate_limited")
        await message.reply_text("⏳ Забагато запитів. Спробуйте за хвилину.")
        return
    llm = svc.translation.llm
    if llm is None or not hasattr(llm, "describe_image"):
        track(update, context, "photo", ok=False, detail="unavailable")
        await message.reply_text(_MESSAGES["disabled"])
        return

    photo = photos[-1]
    file_size = getattr(photo, "file_size", None)
    if file_size and file_size > MAX_IMAGE_BYTES:
        await message.reply_text(_TOO_BIG)
        return

    status = await message.reply_text("⏳ Дивлюсь на зображення…")
    try:
        tg_file = await photo.get_file()
        data = bytes(await tg_file.download_as_bytearray())
    except TelegramError as exc:
        logger.warning("photo download failed: %s", type(exc).__name__)
        await status.edit_text(_DOWNLOAD_FAILED)
        return
    if not data or len(data) > MAX_IMAGE_BYTES:
        await status.edit_text(_TOO_BIG)
        return

    caption = " ".join((getattr(message, "caption", None) or "").split())[:300]
    started = time.monotonic()
    try:
        async with svc.concurrency():
            reading = await svc.translation.describe_image(data, "image/jpeg", caption, uid)
    except PictureUnavailable as exc:
        track(
            update,
            context,
            "photo",
            ok=False,
            detail="budget" if exc.reason == "budget" else "unavailable",
        )
        await status.edit_text(_MESSAGES.get(exc.reason, _MESSAGES["provider"]))
        return
    elapsed = int((time.monotonic() - started) * 1000)
    svc.metrics.incr("requests.photo", elapsed)
    track(update, context, "photo", ok=True, latency_ms=elapsed)
    await status.delete()
    await reply_html(message, format_picture(reading))
