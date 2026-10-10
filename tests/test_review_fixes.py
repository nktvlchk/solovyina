import json
import logging
from types import SimpleNamespace

from bot.formatting import esc_line, split_message
from bot.handlers import correction as ch
from bot.handlers import translation as th
from bot.handlers.util import has_real_user
from bot.language import Direction
from bot.logging_setup import RedactingFormatter, setup_logging
from bot.services.translation import TranslationUnavailable
from tests.conftest import FakeLLM, FakeMyMemory, make_context, make_update

SECRET = "1234567890:AAH" + "y" * 32


def test_traceback_is_redacted():
    try:
        raise RuntimeError(f"failed url https://api.telegram.org/bot{SECRET}/getMe")
    except RuntimeError:
        import sys

        record = logging.LogRecord("x", logging.ERROR, "f", 1, "boom", None, sys.exc_info())
    out = RedactingFormatter("%(message)s").format(record)
    assert SECRET not in out and "[REDACTED]" in out


def test_library_loggers_stay_quiet_in_debug():
    setup_logging("DEBUG")
    assert logging.getLogger("telegram").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    setup_logging("INFO")


async def test_per_user_model_cap(make_services):
    import pytest

    llm = FakeLLM(translate="перевод")
    svc = make_services(
        mymemory=FakeMyMemory(fail=True), llm=llm, ANTHROPIC_API_KEY="k", LLM_USER_DAILY_LIMIT=2
    )
    for i in range(2):
        await svc.translation.translate_phrase(f"фраза номер {i}", Direction.UK_RU, user_id=1)
    with pytest.raises(TranslationUnavailable):
        await svc.translation.translate_phrase("ще одна фраза", Direction.UK_RU, user_id=1)
    # another user is not affected
    await svc.translation.translate_phrase("інша фраза", Direction.UK_RU, user_id=2)


async def test_check_reply_only_to_own_message(make_services):
    resp = json.dumps({"corrected_text": "x", "corrections": []})
    llm = FakeLLM(check=resp)
    svc = make_services(llm=llm, ANTHROPIC_API_KEY="k")
    update = make_update("/check", user_id=1)
    update.effective_message.reply_to_message = SimpleNamespace(
        text="чужий текст", from_user=SimpleNamespace(id=2)
    )
    await ch.cmd_check(update, make_context(svc))
    assert llm.calls == []
    assert "лише власне" in update.effective_message.sent[-1]["text"]


async def test_anonymous_sender_ignored(make_services):
    svc = make_services()
    update = make_update("кіт")
    update.effective_message.sender_chat = SimpleNamespace(id=-100)
    assert has_real_user(update) is False
    await th.on_text(update, make_context(svc))
    assert update.effective_message.sent == []


def test_values_inside_tags_have_no_newlines():
    assert esc_line("a\n<b>\nc") == "a &lt;b&gt; c"
    lines = [f"{i}. <s>{esc_line('x' * 50 + chr(10) + 'y')}</s>" for i in range(300)]
    for part in split_message("\n".join(lines)):
        assert part.count("<s>") == part.count("</s>")
