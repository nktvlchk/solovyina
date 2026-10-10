from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers import admin
from bot.handlers import translation as th
from bot.services.translation import fix_case
from tests.conftest import FakeMyMemory, make_context, make_update

BOT_ID = 4242


def _ctx(svc):
    ctx = make_context(svc)
    ctx.bot = SimpleNamespace(id=BOT_ID)
    return ctx


def _bot_message():
    return SimpleNamespace(from_user=SimpleNamespace(id=BOT_ID), delete=AsyncMock())


async def test_del_removes_bot_message_for_admin(make_services):
    svc = make_services(ADMIN_IDS="1")
    update = make_update("/del", user_id=1)
    target = _bot_message()
    update.effective_message.reply_to_message = target
    update.effective_message.delete = AsyncMock()
    await admin.cmd_delete_bot_message(update, _ctx(svc))
    target.delete.assert_awaited_once()
    update.effective_message.delete.assert_awaited_once()


async def test_del_ignored_for_non_admin(make_services):
    svc = make_services(ADMIN_IDS="1")
    update = make_update("/del", user_id=2)
    target = _bot_message()
    update.effective_message.reply_to_message = target
    await admin.cmd_delete_bot_message(update, _ctx(svc))
    target.delete.assert_not_awaited()
    assert update.effective_message.sent == []


async def test_del_never_deletes_other_peoples_messages(make_services):
    svc = make_services(ADMIN_IDS="1")
    update = make_update("/del", user_id=1)
    target = SimpleNamespace(from_user=SimpleNamespace(id=7), delete=AsyncMock())
    update.effective_message.reply_to_message = target
    await admin.cmd_delete_bot_message(update, _ctx(svc))
    target.delete.assert_not_awaited()


def test_fix_case():
    assert fix_case("вИсновки") == "висновки"
    assert fix_case("Привіт, Київ") == "Привіт, Київ"
    assert fix_case("ЗСУ і NASA") == "ЗСУ і NASA"


async def test_garbled_memory_answer_is_rejected(make_services):
    svc = make_services(mymemory=FakeMyMemory({"висновки": "сВисновки"}))
    update = make_update("/tr вИсновки")
    await th.cmd_translate(update, make_context(svc))
    assert "сВисновки" not in update.effective_message.sent[-1]["text"]


async def test_no_direction_hint_in_groups(make_services):
    svc = make_services(mymemory=FakeMyMemory({"как дела": "як справи"}))
    update = make_update("/tr как дела")
    update.effective_chat = SimpleNamespace(type="supergroup")
    await th.cmd_translate(update, make_context(svc))
    text = update.effective_message.sent[-1]["text"]
    assert "як справи" in text and "/direction" not in text
