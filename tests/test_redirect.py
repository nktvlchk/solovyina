import pytest

from bot.config import ConfigError
from bot.redirect import build_redirect_application, moved_keyboard, moved_text
from tests.conftest import make_settings, make_update


def test_redirect_setting_parsing():
    assert make_settings(REDIRECT_TO_BOT="@solovyinamova_bot").redirect_to_bot == "solovyinamova_bot"
    assert make_settings().redirect_to_bot is None
    with pytest.raises(ConfigError):
        make_settings(REDIRECT_TO_BOT="not a bot!")


def test_moved_message_has_link():
    assert "@solovyinamova_bot" in moved_text("solovyinamova_bot")
    button = moved_keyboard("solovyinamova_bot").inline_keyboard[0][0]
    assert button.url == "https://t.me/solovyinamova_bot"


async def test_any_message_gets_redirect():
    app = build_redirect_application("123456:TEST", "solovyinamova_bot")
    handler = app.handlers[0][0]
    update = make_update("/check something")
    await handler.callback(update, None)
    sent = update.effective_message.sent[-1]
    assert "переїхав" in sent["text"]
    assert sent["reply_markup"].inline_keyboard[0][0].url.endswith("solovyinamova_bot")


def test_redirect_ignores_group_chats():
    from telegram.ext import MessageHandler

    app = build_redirect_application("123456:TEST", "solovyinamova_bot")
    handler = next(h for h in app.handlers[0] if isinstance(h, MessageHandler))
    assert "PRIVATE" in repr(handler.filters).upper()


def test_main_bot_in_groups_only_explicit_commands():
    from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler

    from bot.handlers import register_handlers

    app = ApplicationBuilder().token("123456:TEST").build()
    register_handlers(app)
    for handler in app.handlers[0]:
        if isinstance(handler, CommandHandler):
            text = repr(handler.filters).upper()
            assert "PRIVATE" in text, handler
            assert ("GROUP" in text) == bool(handler.commands & {"check", "say", "tr"}), handler.commands
        elif isinstance(handler, MessageHandler):
            text = repr(handler.filters).upper()
            assert "PRIVATE" in text and "GROUP" not in text


def test_command_text_keeps_line_breaks():
    from types import SimpleNamespace

    from bot.handlers.util import command_text

    update = SimpleNamespace(
        effective_message=SimpleNamespace(text="/check@solovyinamova_bot Рядок 1\nРядок 2")
    )
    assert command_text(update) == "Рядок 1\nРядок 2"
