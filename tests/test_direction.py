from bot.handlers import translation as th
from bot.language import Direction
from bot.providers.base import ProviderError
from tests.conftest import FakeMyMemory, make_context, make_update


def test_saved_direction_overrides_detection(make_services):
    svc = make_services()
    svc.user_settings.set_direction(1, Direction.RU_UK)
    direction, detected = th._resolve_direction(svc, 1, "Привіт")
    assert direction is Direction.RU_UK and detected is False


def test_auto_detection_used_when_no_preference(make_services):
    svc = make_services()
    assert th._resolve_direction(svc, 1, "Привіт") == (Direction.UK_RU, True)


async def test_unknown_language_does_not_interrupt(make_services):
    # "мама" is the same in both languages: guess ru->uk, then try uk->ru
    svc = make_services(mymemory=FakeMyMemory({"мама": "мама"}))
    update = make_update("мама")
    ctx = make_context(svc)
    await th.on_text(update, ctx)
    text = update.effective_message.sent[-1]["text"]
    assert "Оберіть напрям" not in text


async def test_unknown_phrase_tries_both_directions(make_services):
    class DirectionalMyMemory(FakeMyMemory):
        async def translate(self, text, source, target):
            if source != "uk":
                raise ProviderError("mymemory", "no match")
            return await super().translate(text, source, target)

    mm = DirectionalMyMemory({"мама тут": "мама здесь"})
    svc = make_services(mymemory=mm)
    update = make_update("мама тут")
    ctx = make_context(svc)
    await th.on_text(update, ctx)
    assert "мама здесь" in update.effective_message.sent[-1]["text"]
    assert ctx.user_data[th.LAST_DIRECTION_KEY] is Direction.UK_RU


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


async def test_plain_text_goes_through_claude_auto(make_services):
    from tests.conftest import FakeLLM

    llm = FakeLLM(auto='{"source": "ru", "translation": "Надішли фразу"}')
    svc = make_services(llm=llm, ANTHROPIC_API_KEY="k")
    update = make_update("отправь фразу")
    ctx = make_context(svc)
    await th.on_text(update, ctx)
    text = update.effective_message.sent[-1]["text"]
    assert "Надішли фразу" in text and "мовна модель" in text
    assert "Оберіть напрям" not in text
    assert llm.calls == ["auto"]


async def test_exact_dictionary_word_stays_local(make_services):
    from tests.conftest import FakeLLM

    llm = FakeLLM(auto='{"source": "uk", "translation": "кот"}')
    svc = make_services(llm=llm, ANTHROPIC_API_KEY="k")
    update = make_update("кіт")
    await th.on_text(update, make_context(svc))
    assert llm.calls == []
    assert "локальний словник" in update.effective_message.sent[-1]["text"]
