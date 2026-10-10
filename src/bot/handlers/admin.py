"""Admin-only aggregated statistics. No user lists, IDs, names or texts."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from bot.formatting import esc
from bot.handlers.util import reply_html, services, user_id


def is_admin(context: ContextTypes.DEFAULT_TYPE, uid: int) -> bool:
    return uid in services(context).settings.admin_ids


def format_stats(metrics: list[dict], quality: list[dict], days: int) -> str:
    lines = [f"📊 <b>Статистика за {days} дн.</b> (агреговано)", ""]
    if not metrics:
        lines.append("Даних поки немає.")
    for row in metrics:
        avg = f", сер. {row['total_ms'] // row['count']} мс" if row["total_ms"] and row["count"] else ""
        lines.append(f"• {esc(row['name'])}: {row['count']}{avg}")
    if quality:
        lines += ["", "🧪 <b>Оцінки виправлень</b> (модель / шаблон / тип / оцінка):"]
        for q in quality:
            lines.append(
                f"• {esc(q['model'])} / {esc(q['prompt_version'])} / {esc(q['type'])} / "
                f"{esc(q['verdict'])}: {q['n']}"
            )
    return "\n".join(lines)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(context, user_id(update)):
        await update.effective_message.reply_text("Ця команда доступна лише адміністратору.")
        return
    svc = services(context)
    await reply_html(
        update.effective_message,
        format_stats(svc.metrics.summary(7), svc.corrections.quality_summary(), 7),
    )
