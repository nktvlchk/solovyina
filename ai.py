"""
AI-модуль: переклад через LLM (запасний варіант) + перевірка тексту "/check".
Працює, тільки якщо задано змінну оточення ANTHROPIC_API_KEY.
Якщо ключа немає — усі функції повертають "" і бот працює як раніше.
"""

import os
import logging

logger = logging.getLogger(__name__)

MODEL = os.getenv("AI_MODEL", "claude-haiku-4-5-20251001")

_client = None
_client_tried = False


def _get_client():
    global _client, _client_tried
    if _client_tried:
        return _client
    _client_tried = True
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        logger.warning("ANTHROPIC_API_KEY не задано — AI-функції вимкнено")
        return None
    try:
        from anthropic import Anthropic
        _client = Anthropic(api_key=key)
    except Exception as e:
        logger.warning(f"Не вдалося створити Anthropic client: {e}")
        _client = None
    return _client


def ai_enabled() -> bool:
    return _get_client() is not None


def _ask(system: str, user: str, max_tokens: int = 700) -> str:
    client = _get_client()
    if client is None:
        return ""
    try:
        msg = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in msg.content if b.type == "text").strip()
    except Exception as e:
        logger.warning(f"AI помилка: {e}")
        return ""


# ---------------------------------------------------------------- переклад

_LANG = {"uk": "українську", "ru": "російську"}


def llm_translate(text: str, source: str, target: str) -> str:
    """Запасний переклад, коли Google і MyMemory віддали сміття."""
    target_name = _LANG.get(target, target)
    return _ask(
        "Ти перекладач українська↔російська. "
        "Відповідай ТІЛЬКИ перекладом — без пояснень, лапок, преамбул і варіантів.",
        f"Переклади на {target_name}:\n\n{text}",
        max_tokens=600,
    )


# ------------------------------------------------------------- перевірка

_CHECK_SYSTEM = """Ти — вчитель української мови. Користувач надсилає свій текст і хоче перевірити, чи все написано правильно.

Перевір орфографію, граматику, відмінки, узгодження, кальки з російської та пунктуацію.

Відповідай СТРОГО у цьому форматі. Дозволені HTML-теги: <b>, <i>, <code>. Жодних інших тегів, жодного markdown, нічого зайвого до чи після.

<b>✅ Виправлено:</b>
{повний виправлений текст}

<b>📝 Помилки:</b>
• <code>було</code> → <code>стало</code> — коротке пояснення російською
• ...

Якщо помилок немає — замість списку напиши рівно один рядок: • Помилок немає 👍
Максимум 8 пунктів. Не вигадуй помилок там, де їх немає."""


def llm_check(text: str) -> str:
    """Перевірка тексту користувача. Повертає готовий HTML для Telegram."""
    return _ask(_CHECK_SYSTEM, text, max_tokens=1200)
