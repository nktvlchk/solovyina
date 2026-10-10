"""Logging that never prints the bot token or user message text."""

from __future__ import annotations

import logging
import re

_TOKEN_RE = re.compile(r"\d{6,12}:[A-Za-z0-9_-]{20,}")
_KEY_RE = re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}")


def redact(text: str) -> str:
    return _KEY_RE.sub("[REDACTED]", _TOKEN_RE.sub("[REDACTED]", text))


class RedactingFormatter(logging.Formatter):
    """Redacts the fully formatted record, tracebacks included."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class RedactSecretsFilter(logging.Filter):
    """Masks anything that looks like a Telegram token or Anthropic key."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = _KEY_RE.sub("[REDACTED]", _TOKEN_RE.sub("[REDACTED]", message))
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True


def setup_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactSecretsFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # httpx logs full request URLs (bot token, user text in query strings);
    # python-telegram-bot at DEBUG dumps whole updates (user messages).
    # These stay quiet even when LOG_LEVEL=DEBUG.
    for noisy in ("httpx", "httpcore", "telegram", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
