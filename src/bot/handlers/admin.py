"""Admin-only product analytics. Pseudonymous keys only: no Telegram IDs,
names or message texts are shown or exported."""

from __future__ import annotations

import contextlib
import csv
import io

from telegram import InputFile, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot.formatting import esc
from bot.handlers.util import reply_html, services, user_id

EVENT_LABELS = {
    "start": "старт",
    "translate": "переклад",
    "say": "Як сказати?",
    "explain": "Поясни слово",
    "check": "перевірка /check",
    "feedback": "оцінка виправлення",
    "tts": "озвучка",
    "mistakes": "Мої помилки",
    "direction": "зміна напряму",
    "delete_data": "видалення даних",
    "rate_limited": "ліміт запитів",
}


def is_admin(context: ContextTypes.DEFAULT_TYPE, uid: int) -> bool:
    return uid in services(context).settings.admin_ids


def _pct(part: int, total: int) -> str:
    return f"{round(100 * part / total)}%" if total else "—"


def format_report(report: dict, quality: list[dict]) -> str:
    days = report["days"]
    lines = [
        f"📊 <b>Продуктова статистика за {days} дн.</b>",
        "",
        f"👥 Активні користувачі: <b>{report['users']}</b>",
        f"🆕 Нові: <b>{report['new_users']}</b>",
        f"🔁 Повернулися (активні ≥2 дні): <b>{report['returning_users']}</b> "
        f"({_pct(report['returning_users'], report['users'])})",
    ]
    if report["daily"]:
        lines += ["", "<b>По днях</b> (користувачі / дії):"]
        lines += [f"• {d['day']}: {d['users']} / {d['events']}" for d in report["daily"]]
    if report["by_event"]:
        lines += ["", "<b>Дії</b> (кількість, помилки, сер. час):"]
        for e in report["by_event"]:
            name = EVENT_LABELS.get(e["event"], e["event"])
            detail = f" · {e['detail']}" if e["detail"] else ""
            failed = f", невдало {e['failed']}" if e["failed"] else ""
            avg = f", {e['avg_ms']} мс" if e["avg_ms"] else ""
            lines.append(f"• {esc(name)}{esc(detail)}: {e['n']}{failed}{avg}")
    if report["sources"]:
        total = sum(s["n"] for s in report["sources"])
        lines += ["", "<b>Джерела перекладу:</b>"]
        lines += [f"• {esc(s['source'])}: {s['n']} ({_pct(s['n'], total)})" for s in report["sources"]]
    fb = report["feedback"]
    total_fb = sum(fb.values())
    lines += ["", f"<b>Оцінки виправлень</b> (усього {total_fb}):"]
    lines += [
        f"• ✅ це помилка: {fb.get('error', 0)} ({_pct(fb.get('error', 0), total_fb)})",
        f"• ❌ немає помилки: {fb.get('not_error', 0)} ({_pct(fb.get('not_error', 0), total_fb)})",
        f"• ✍️ лише стиль: {fb.get('style', 0)} ({_pct(fb.get('style', 0), total_fb)})",
    ]
    if quality:
        lines += ["", "🧪 <b>Якість за моделлю / шаблоном / типом</b> (за весь час):"]
        for q in quality:
            key = f"{q['model']} / {q['prompt_version']} / {q['type']} / {q['verdict']}"
            lines.append(f"• {esc(key)}: {q['n']}")
    lines += ["", "Вивантаження подій за 30 днів: /export_stats"]
    return "\n".join(lines)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(context, user_id(update)):
        await update.effective_message.reply_text("Ця команда доступна лише адміністратору.")
        return
    svc = services(context)
    days = 7
    if context.args and context.args[0].isdigit():
        days = max(1, min(90, int(context.args[0])))
    await reply_html(
        update.effective_message,
        format_report(svc.analytics.report(days), svc.corrections.quality_summary()),
    )


async def cmd_export_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(context, user_id(update)):
        await update.effective_message.reply_text("Ця команда доступна лише адміністратору.")
        return
    rows = services(context).analytics.export_rows(30)
    buffer = io.StringIO()
    fields = ["ts", "user_key", "event", "detail", "source", "direction", "ok", "latency_ms", "chars"]
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    data = buffer.getvalue().encode("utf-8")
    await update.effective_message.reply_document(
        document=InputFile(data, filename="solovyina-events-30d.csv"),
        caption=f"Подій: {len(rows)}. Ключ користувача — псевдонім, без Telegram ID і текстів.",
    )


async def cmd_delete_bot_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/del as a reply to one of the bot's messages: the bot deletes it.
    Admins only (ADMIN_IDS); works in groups without admin rights for the bot,
    because a bot may always delete its own messages (Telegram: within 48 h)."""
    message = update.effective_message
    if message is None or not is_admin(context, user_id(update)):
        return  # stay silent for everyone else
    target = message.reply_to_message
    if target is None or target.from_user is None or target.from_user.id != context.bot.id:
        with contextlib.suppress(TelegramError):
            await message.reply_text("Відповідайте командою /del на повідомлення бота.")
        return
    try:
        await target.delete()
    except TelegramError:
        with contextlib.suppress(TelegramError):
            await message.reply_text("Не вдалося видалити: Telegram дозволяє це лише протягом 48 годин.")
        return
    # Deleting the command itself needs admin rights in a group; skip if not allowed.
    with contextlib.suppress(TelegramError):
        await message.delete()
