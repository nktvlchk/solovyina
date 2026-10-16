"""Add lesson cards when the teacher posts in the class chat.

The class chat itself gets no reply. A short notice goes to the admins in
private. /learn does the same thing from a private chat with an admin.
Nothing from the lesson is stored except the ids of the last batch.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot.handlers.admin import is_admin
from bot.handlers.util import has_real_user, reply_html, services, track, user_id
from bot.services.deck import DeckError
from bot.services.lesson_cards import MIN_CLASS_TEXT, LessonUnavailable, request_drafts

logger = logging.getLogger(__name__)

LEARN_KEY = "learn"
_MAX_BYTES = 4_000_000

_TELL = {
    "disabled": "🔒 Збір карток вимкнено. Потрібні ключ моделі і доступ до деки.",
    "budget": "⏳ Денний ліміт вичерпано. Деку не змінював.",
    "parse": "😔 Не вдалося зібрати надійні картки. Деку не змінював.",
    "provider": "😔 Сервіс зараз недоступний. Деку не змінював.",
    "deck": "😔 Деку зараз не вдалося оновити. Спробуйте ще раз.",
    "download": "Не вдалося завантажити файл. Деку не змінював.",
    "big": "Файл завеликий. Деку не змінював.",
}


def _cards_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "картку"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return "картки"
    return "карток"


def _ready(count: int, ids: tuple[str, ...], day) -> str:
    listed = ", ".join(ids)
    return (
        f"🗂 Додав {count} {_cards_word(count)}: {listed}. "
        f"Перша вийде {day.strftime('%d.%m')}. Скасувати останню партію: /deck_undo"
    )


async def _dm_admins(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    bot = getattr(context, "bot", None)
    if bot is None:
        return
    for admin_id in services(context).settings.admin_ids:
        try:
            await bot.send_message(chat_id=admin_id, text=text)
        except TelegramError as exc:
            logger.warning("deck notice failed: %s", type(exc).__name__)


async def _tell(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, *, private: bool) -> None:
    if private and update.effective_message is not None:
        await update.effective_message.reply_text(text)
        return
    await _dm_admins(context, text)


async def _download(source) -> bytes | None:
    size = getattr(source, "file_size", None)
    if size and size > _MAX_BYTES:
        return None
    tg_file = await source.get_file()
    data = bytes(await tg_file.download_as_bytearray())
    if not data or len(data) > _MAX_BYTES:
        return None
    return data


async def _publish(update: Update, context: ContextTypes.DEFAULT_TYPE, drafts, *, private: bool) -> None:
    svc = services(context)
    editor = svc.deck
    if editor is None:
        await _tell(update, context, _TELL["disabled"], private=private)
        return
    try:
        added = await editor.add(drafts)
    except DeckError:
        logger.info("deck update failed")
        track(update, context, "deck", ok=False, detail="error")
        await _tell(update, context, _TELL["deck"], private=private)
        return
    track(update, context, "deck", ok=True, detail="saved")
    logger.info("deck added %s cards at index %s", len(added.ids), added.index)
    await _tell(update, context, _ready(len(added.ids), added.ids, added.first_day), private=private)


async def _from_model(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    private: bool,
    text: str = "",
    data: bytes = b"",
    media_type: str = "",
    caption: str = "",
) -> None:
    svc = services(context)
    if svc.deck is None:
        if private:
            await _tell(update, context, _TELL["disabled"], private=True)
        return
    try:
        drafts = await request_drafts(
            svc.translation.llm,
            svc.translation.llm_budget,
            user_id(update),
            svc.metrics,
            text=text,
            data=data,
            media_type=media_type,
            caption=caption,
        )
    except LessonUnavailable as exc:
        track(update, context, "deck", ok=False, detail="unavailable" if exc.reason != "parse" else "error")
        await _tell(update, context, _TELL.get(exc.reason, _TELL["provider"]), private=private)
        return
    await _publish(update, context, drafts, private=private)


async def ingest_private_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    if not is_admin(context, user_id(update)):
        await update.effective_message.reply_text("Ця команда доступна лише адміністратору.")
        return
    await _from_model(update, context, private=True, text=text[:8000])


async def ingest_private_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(context, user_id(update)):
        await update.effective_message.reply_text("Ця команда доступна лише адміністратору.")
        return
    message = update.effective_message
    photo = message.photo[-1]
    try:
        data = await _download(photo)
    except TelegramError as exc:
        logger.warning("lesson photo failed: %s", type(exc).__name__)
        await message.reply_text(_TELL["download"])
        return
    if data is None:
        await message.reply_text(_TELL["big"])
        return
    caption = " ".join((message.caption or "").split())[:300]
    await _from_model(update, context, private=True, data=data, media_type="image/jpeg", caption=caption)


async def on_private_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.user_data.pop(LEARN_KEY, None):
        return
    message = update.effective_message
    document = getattr(message, "document", None) if message is not None else None
    if document is None or not is_admin(context, user_id(update)):
        return
    if getattr(document, "mime_type", "") != "application/pdf":
        await message.reply_text("Для конспекту надішліть PDF.")
        return
    try:
        data = await _download(document)
    except TelegramError as exc:
        logger.warning("lesson pdf failed: %s", type(exc).__name__)
        await message.reply_text(_TELL["download"])
        return
    if data is None:
        await message.reply_text(_TELL["big"])
        return
    caption = " ".join((getattr(message, "caption", None) or "").split())[:300]
    await _from_model(update, context, private=True, data=data, media_type="application/pdf", caption=caption)


async def cmd_learn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from bot.handlers.util import command_text

    if not is_admin(context, user_id(update)):
        await update.effective_message.reply_text("Ця команда доступна лише адміністратору.")
        return
    text = command_text(update)
    if not text:
        context.user_data[LEARN_KEY] = True
        await update.effective_message.reply_text(
            "Надішліть текст уроку, фото конспекту або PDF. З цього вийдуть картки для розсилки."
        )
        return
    await ingest_private_text(update, context, text)


async def cmd_deck(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(context, user_id(update)):
        await update.effective_message.reply_text("Ця команда доступна лише адміністратору.")
        return
    editor = services(context).deck
    if editor is None:
        await update.effective_message.reply_text(_TELL["disabled"])
        return
    try:
        number, total, index, first_day, last = await editor.status()
    except DeckError:
        await update.effective_message.reply_text(_TELL["deck"])
        return
    lines = [
        f"🗂 День розсилки: {number}. У деці: {total}.",
        f"Наступна нова картка стане на позицію {index + 1} і вийде {first_day.strftime('%d.%m')}.",
    ]
    if last:
        lines.append("Остання партія: " + ", ".join(last) + ".")
    else:
        lines.append("Останньої партії ще немає.")
    track(update, context, "deck")
    await reply_html(update.effective_message, "\n".join(lines))


async def cmd_deck_undo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(context, user_id(update)):
        await update.effective_message.reply_text("Ця команда доступна лише адміністратору.")
        return
    editor = services(context).deck
    if editor is None:
        await update.effective_message.reply_text(_TELL["disabled"])
        return
    try:
        removed = await editor.undo()
    except DeckError:
        await update.effective_message.reply_text(_TELL["deck"])
        return
    track(update, context, "deck", detail="undo", ok=bool(removed))
    if not removed:
        await update.effective_message.reply_text("Немає партії, яку можна прибрати.")
        return
    await update.effective_message.reply_text("Прибрав з деки: " + ", ".join(removed) + ".")


async def on_class_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Watch the class chat. Never reply there."""
    try:
        await _class_message(update, context)
    except Exception as exc:
        logger.warning("class ingest failed: %s", type(exc).__name__)
        await _dm_admins(context, _TELL["deck"])


async def _class_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None or not has_real_user(update):
        return
    svc = services(context)
    settings = svc.settings
    if not settings.drip_chat_id or str(chat.id) != settings.drip_chat_id:
        return
    if user_id(update) not in settings.teacher_ids or svc.deck is None:
        return

    photos = getattr(message, "photo", None)
    document = getattr(message, "document", None)
    caption = " ".join((getattr(message, "caption", None) or "").split())[:300]
    text = (getattr(message, "text", None) or "").strip()
    is_pdf = getattr(document, "mime_type", "") == "application/pdf"
    actionable = bool(photos) or is_pdf or len(text) >= MIN_CLASS_TEXT
    if not actionable:
        return
    if not svc.rate_limiter.allow(user_id(update)):
        svc.metrics.incr("rate_limited")
        track(update, context, "rate_limited")
        await _dm_admins(context, "⏳ Забагато повідомлень поспіль. Надішліть урок ще раз за хвилину.")
        return
    if photos:
        try:
            data = await _download(photos[-1])
        except TelegramError as exc:
            logger.warning("class photo failed: %s", type(exc).__name__)
            await _dm_admins(context, _TELL["download"])
            return
        if data is None:
            await _dm_admins(context, _TELL["big"])
            return
        await _from_model(update, context, private=False, data=data, media_type="image/jpeg", caption=caption)
        return
    if document is not None:
        if getattr(document, "mime_type", "") != "application/pdf":
            return
        try:
            data = await _download(document)
        except TelegramError as exc:
            logger.warning("class pdf failed: %s", type(exc).__name__)
            await _dm_admins(context, _TELL["download"])
            return
        if data is None:
            await _dm_admins(context, _TELL["big"])
            return
        await _from_model(
            update, context, private=False, data=data, media_type="application/pdf", caption=caption
        )
        return

    await _from_model(update, context, private=False, text=text[:8000])
