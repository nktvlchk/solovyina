"""Safe text output for Telegram HTML parse mode."""

from __future__ import annotations

import html

TELEGRAM_MESSAGE_LIMIT = 4096
TELEGRAM_CAPTION_LIMIT = 1024


def esc(text: object) -> str:
    """Escape any value for Telegram HTML. Use for ALL user/provider text."""
    return html.escape(str(text), quote=False)


def esc_line(text: object) -> str:
    """Escape and collapse newlines: for values placed inside <b>/<i>/<s>,
    so split_message never cuts a tag across chunks."""
    return esc(" ".join(str(text).split()))


def truncate(text: str, limit: int, suffix: str = "…") -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))] + suffix


def split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Split on line boundaries so no chunk exceeds the Telegram limit.

    Expects already-formatted HTML where tags do not span lines (true for
    all messages this bot builds). Over-long single lines are hard-split.
    """
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            cut = _safe_cut(line, limit)
            chunks.append(line[:cut])
            line = line[cut:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _safe_cut(line: str, limit: int) -> int:
    """Cut position <= limit that does not split an HTML entity like &amp;."""
    amp = line.rfind("&", max(0, limit - 10), limit)
    if amp != -1 and line.find(";", amp, limit) == -1:
        return amp or limit
    return limit
