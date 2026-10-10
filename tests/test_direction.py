from bot.handlers import translation as th
from bot.language import Direction
from tests.conftest import make_context, make_update


def test_saved_direction_overrides_detection(make_services):
    svc = make_services()
    svc.user_settings.set_direction(1, Direction.RU_UK)
    direction, detected = th._resolve_direction(svc, 1, "Привіт")
    assert direction is Direction.RU_UK and detected is False


def test_auto_detection_used_when_no_preference(make_services):
    svc = make_services()
    assert th._resolve_direction(svc, 1, "Привіт") == (Direction.UK_RU, True)


async def test_unknown_language_asks_for_direction(make_services):
    svc = make_services()
    update, ctx = make_update("мама"), None
    ctx = make_context(svc)
    await th.on_text(update, ctx)
    sent = update.effective_message.sent[-1]
    assert "Оберіть напрям" in sent["text"]
    buttons = [b.text for row in sent["reply_markup"].inline_keyboard for b in row]
    assert buttons == ["Українська → російська", "Російська → українська"]
    assert ctx.user_data[th.PENDING_KEY] == "мама"


async def test_direction_button_translates_pending_text(make_services):
    svc = make_services()
    ctx = make_context(svc, user_data={th.PENDING_KEY: "кіт"})
    update = make_update(callback="dir1:uk-ru")
    await th.on_direction_callback(update, ctx)
    assert "кот" in update.effective_message.sent[-1]["text"]


async def test_direction_command_persists(make_services):
    svc = make_services()
    update = make_update(callback="dir0:ru-uk")
    await th.on_direction_callback(update, make_context(svc))
    assert svc.user_settings.get_direction(1) is Direction.RU_UK
