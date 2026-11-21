"""🐞 Помилка: the user flags a wrong bot answer and, if they want, says why.

Flow (private chats only, so group chats stay uncluttered):

1. "🐞 Помилка" under an answer: the report is saved at once — the request,
   the answer as shown, which feature and source produced it.
2. The bot asks what exactly is wrong. The next text message becomes the
   comment. Instead the user can press "Без коментаря" (keep the report as
   is) or "Скасувати" (delete it, nothing is kept).
3. The admin goes through the reports later: /bugs, /done_<id>, /export_bugs.
"""

from __future__ import annotations

import contextlib
import csv
import datetime as dt
import io
import json
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot import __version__
from bot.formatting import esc
from bot.handlers.util import is_private, reply_html, services, track, user_id

REPORT_LABEL = "🐞 Помилка"
PENDING_KEY = "bug_pending"
PENDING_TTL_SECONDS = 15 * 60
LIST_LIMIT = 10

FEATURE_LABELS = {
    "translate": "переклад",
    "say": "Як сказати?",
    "explain": "Поясни слово",
    "photo": "фото",
    "check": "перевірка /check",
}

PROMPT = (
    "🐞 Записав, дякую! Що саме не так?\n\n"
    "Напишіть одним повідомленням, наприклад: «неправильний наголос», "
    "«має бути „…“», «це інше значення».\n"
    "Або натисніть кнопку нижче."
)
SAVED_PLAIN = "🐞 Записав без коментаря. Дякую!"
CANCELLED = "↩️ Скасовано — звіт не збережено."
GONE = "Цей звіт уже видалено."


# ------------------------------------------------------------ user side


def report_button(
    svc,
    update: Update,
    feature: str,
    user_text: str = "",
    source: str = "",
) -> InlineKeyboardButton | None:
    """The 🐞 button for an answer. None in groups and if reports are off."""
    if svc.bug_reports is None or not is_private(update):
        return None
    payload = json.dumps({"f": feature, "q": user_text[:1000], "s": source or ""}, ensure_ascii=False)
    return InlineKeyboardButton(REPORT_LABEL, callback_data=f"bug:{svc.text_cache.put(payload)}")


def prompt_keyboard(report_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Без коментаря", callback_data=f"bugs:{report_id}"),
                InlineKeyboardButton("↩️ Скасувати", callback_data=f"bugx:{report_id}"),
            ]
        ]
    )


def _parse_payload(raw: str | None) -> dict:
    """Context cached with the button. After a restart the cache is empty:
    the report still works, the answer text itself is read from the message."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in ("f", "q", "s") and isinstance(v, str)}


async def on_report_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """bug:<cache key> — the 🐞 button under an answer."""
    query = update.callback_query
    if query is None or not query.data:
        return
    svc = services(context)
    if svc.bug_reports is None:
        await query.answer()
        return
    if not is_private(update):
        await query.answer("Повідомити про помилку можна в особистому чаті з ботом.")
        return
    message = query.message
    bot_text = ""
    if message is not None:
        bot_text = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
    if not bot_text:
        await query.answer("Не бачу цієї відповіді. Надішліть запит ще раз.")
        return
    payload = _parse_payload(svc.text_cache.get(query.data.partition(":")[2]))
    report_id = svc.bug_reports.create(
        user_id(update),
        payload.get("f", "translate"),
        bot_text,
        user_text=payload.get("q", ""),
        source=payload.get("s", ""),
        message_id=getattr(message, "message_id", None),
        bot_version=__version__,
    )
    svc.metrics.incr("feedback.bug")
    track(update, context, "bug_report", detail="reported")
    await query.answer("🐞 Записав")
    prompt = await message.reply_text(PROMPT, reply_markup=prompt_keyboard(report_id))
    context.user_data[PENDING_KEY] = {
        "id": report_id,
        "at": time.time(),
        "prompt": getattr(prompt, "message_id", None),
    }


async def on_prompt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """bugs:<id> keeps the report without a comment; bugx:<id> deletes it."""
    query = update.callback_query
    if query is None or not query.data:
        return
    action, _, raw_id = query.data.partition(":")
    if action not in ("bugs", "bugx") or not raw_id.isdigit():
        await query.answer()
        return
    report_id = int(raw_id)
    svc = services(context)
    uid = user_id(update)
    owner = svc.bug_reports.owner(report_id) if svc.bug_reports is not None else None
    if owner is None:
        _drop_pending(context, report_id)
        await query.answer()
        with contextlib.suppress(TelegramError):
            await query.edit_message_text(GONE)
        return
    if owner != uid:
        await query.answer("Це не ваш звіт.")
        return
    _drop_pending(context, report_id)
    if action == "bugx":
        svc.bug_reports.delete(report_id, uid)
        track(update, context, "bug_report", detail="cancelled")
        await query.answer("Скасовано")
        with contextlib.suppress(TelegramError):
            await query.edit_message_text(CANCELLED)
        return
    track(update, context, "bug_report", detail="skipped")
    await query.answer("Збережено")
    with contextlib.suppress(TelegramError):
        await query.edit_message_text(SAVED_PLAIN)


def _drop_pending(context: ContextTypes.DEFAULT_TYPE, report_id: int) -> None:
    pending = context.user_data.get(PENDING_KEY)
    if isinstance(pending, dict) and pending.get("id") == report_id:
        context.user_data.pop(PENDING_KEY, None)


def clear_pending(context: ContextTypes.DEFAULT_TYPE) -> None:
    """A mode button was pressed: the report stays, just without a comment."""
    context.user_data.pop(PENDING_KEY, None)


async def take_comment(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """If the user was asked what is wrong, this message is the comment.
    Returns False when nothing is pending, so the message is handled as usual."""
    pending = context.user_data.pop(PENDING_KEY, None)
    if not isinstance(pending, dict):
        return False
    report_id = pending.get("id")
    started = pending.get("at")
    if not isinstance(report_id, int) or not isinstance(started, int | float):
        return False
    if time.time() - started > PENDING_TTL_SECONDS:
        return False  # forgotten prompt: treat the message as a new request
    svc = services(context)
    if svc.bug_reports is None or not svc.bug_reports.set_comment(report_id, user_id(update), text):
        return False
    track(update, context, "bug_report", detail="commented")
    prompt_id = pending.get("prompt")
    bot = getattr(context, "bot", None)
    chat = getattr(update, "effective_chat", None)
    if bot is not None and chat is not None and isinstance(prompt_id, int):
        with contextlib.suppress(TelegramError):
            await bot.edit_message_reply_markup(chat_id=chat.id, message_id=prompt_id, reply_markup=None)
    await update.effective_message.reply_text("🐞 Дякую! Записав коментар — розберу цю помилку.")
    return True


# ----------------------------------------------------------- admin side


def _is_admin(context: ContextTypes.DEFAULT_TYPE, update: Update) -> bool:
    from bot.handlers.admin import is_admin

    return is_admin(context, user_id(update))


def _short(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _when(created_at: str) -> str:
    try:
        moment = dt.datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return created_at
    return moment.strftime("%d.%m %H:%M")


def format_reports(reports, counts: dict[str, int], status: str) -> str:
    title = "Розібрані звіти" if status == "done" else "Звіти про помилки"
    lines = [
        f"🐞 <b>{title}</b> · відкритих: {counts.get('open', 0)}, розібраних: {counts.get('done', 0)}",
    ]
    if not reports:
        lines += ["", "Поки порожньо." if status == "done" else "Відкритих звітів немає 🎉"]
    for r in reports:
        feature = FEATURE_LABELS.get(r.feature, r.feature)
        meta = " · ".join(x for x in (_when(r.created_at), feature, r.source, f"v{r.bot_version}") if x)
        lines += ["", f"<b>#{r.id}</b> · {esc(meta)}"]
        if r.user_text:
            lines.append(f"Запит: <i>{esc(_short(r.user_text, 200))}</i>")
        lines.append(f"Відповідь: {esc(_short(r.bot_text, 300))}")
        lines.append(f"💬 {esc(_short(r.comment, 300))}" if r.comment else "💬 <i>без коментаря</i>")
        lines.append(f"Розібрано: /done_{r.id}" if status == "open" else f"Повернути: /reopen_{r.id}")
    lines += ["", "Усі звіти файлом: /export_bugs"]
    if status == "open":
        lines.append("Розібрані: /bugs done")
    return "\n".join(lines)


async def cmd_bugs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(context, update):
        await update.effective_message.reply_text("Ця команда доступна лише адміністратору.")
        return
    svc = services(context)
    status = "done" if context.args and context.args[0].lower() in ("done", "розібрані") else "open"
    reports = svc.bug_reports.list(status, LIST_LIMIT)
    await reply_html(update.effective_message, format_reports(reports, svc.bug_reports.counts(), status))


async def on_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/done_12 and /reopen_12 — tappable links from the /bugs list."""
    message = update.effective_message
    if message is None or not message.text or not _is_admin(context, update):
        return
    command = message.text.split()[0].lstrip("/").split("@")[0]
    action, _, raw_id = command.partition("_")
    if action not in ("done", "reopen") or not raw_id.isdigit():
        return
    svc = services(context)
    report_id = int(raw_id)
    if not svc.bug_reports.set_status(report_id, "done" if action == "done" else "open"):
        await message.reply_text(f"Звіту #{report_id} немає.")
        return
    counts = svc.bug_reports.counts()
    verb = "розібрано ✅" if action == "done" else "знову відкрито"
    await message.reply_text(f"#{report_id} {verb}. Відкритих: {counts['open']}. Список: /bugs")


async def cmd_export_bugs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(context, update):
        await update.effective_message.reply_text("Ця команда доступна лише адміністратору.")
        return
    rows = services(context).bug_reports.export_rows()
    buffer = io.StringIO()
    fields = [
        "id",
        "created_at",
        "status",
        "feature",
        "source",
        "bot_version",
        "user_text",
        "bot_text",
        "comment",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    # utf-8-sig: Cyrillic opens correctly in Excel as well as Google Sheets.
    data = buffer.getvalue().encode("utf-8-sig")
    await update.effective_message.reply_document(
        document=InputFile(data, filename="solovyina-bug-reports.csv"),
        caption=f"Звітів: {len(rows)}. Без Telegram ID.",
    )
