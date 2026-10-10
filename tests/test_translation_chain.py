import pytest

from bot.language import Direction
from bot.models.translation import Source, TranslationResult, WiktionaryEntry
from bot.services.translation import TranslationUnavailable
from tests.conftest import FakeLLM, FakeMyMemory, FakeWiktionary

WIKT = WiktionaryEntry(word="зелепукатий", pos="прикм.", definitions=("изумрудный",), url="u")


async def test_word_dictionary_first_external_not_called(make_services):
    mm, llm = FakeMyMemory(), FakeLLM(translate="x")
    svc = make_services(mymemory=mm, llm=llm, ANTHROPIC_API_KEY="k")
    lookup = await svc.translation.translate_word("кіт", Direction.UK_RU)
    assert lookup.result.source is Source.DICTIONARY
    assert mm.calls == [] and llm.calls == []


async def test_word_wiktionary_second(make_services):
    wk = FakeWiktionary({"зелепукатий": WIKT})
    mm = FakeMyMemory({"зелепукатий": "изумрудный"})
    svc = make_services(wiktionary=wk, mymemory=mm)
    lookup = await svc.translation.translate_word("зелепукатий", Direction.UK_RU)
    assert lookup.result.source is Source.WIKTIONARY
    assert mm.calls == []


async def test_word_claude_after_dictionary_and_labeled_correctly(make_services):
    mm = FakeMyMemory({"зюзюкало": "штуковина"})
    llm = FakeLLM(translate="штука")
    svc = make_services(mymemory=mm, llm=llm, ANTHROPIC_API_KEY="k")
    lookup = await svc.translation.translate_word("зюзюкало", Direction.UK_RU)
    assert lookup.result.source is Source.LLM
    assert mm.calls == []  # no need to ask the next provider


async def test_word_mymemory_when_no_model(make_services):
    mm = FakeMyMemory({"зюзюкало": "штуковина"})
    svc = make_services(mymemory=mm)
    lookup = await svc.translation.translate_word("зюзюкало", Direction.UK_RU)
    assert lookup.result.source is Source.MYMEMORY


async def test_wrong_language_answer_is_rejected(make_services):
    # the real bug: MyMemory answered a ru->uk request in Russian
    mm = FakeMyMemory({"давай проверим": "Ну давай выясним, так ли это, а?"})
    svc = make_services(mymemory=mm)
    with pytest.raises(TranslationUnavailable):
        await svc.translation.translate_phrase("давай проверим", Direction.RU_UK)


async def test_google_between_claude_and_mymemory(make_services):
    class FakeGoogle:
        def __init__(self):
            self.calls = []

        async def translate(self, text, source, target):
            self.calls.append(text)
            return TranslationResult(text="давай перевіримо", source=Source.GOOGLE)

    mm = FakeMyMemory({"давай проверим": "давай перевіримо"})
    google = FakeGoogle()
    svc = make_services(mymemory=mm, google=google, llm=FakeLLM(fail=True), ANTHROPIC_API_KEY="k")
    result = await svc.translation.translate_phrase("давай проверим", Direction.RU_UK)
    assert result.source is Source.GOOGLE and mm.calls == []


async def test_word_llm_when_translator_down(make_services):
    llm = FakeLLM(translate="штуковина")
    svc = make_services(mymemory=FakeMyMemory(fail=True), llm=llm, ANTHROPIC_API_KEY="k")
    lookup = await svc.translation.translate_word("зюзюкало", Direction.UK_RU)
    assert lookup.result.source is Source.LLM
    assert lookup.result.metadata["model"] == "fake-model"


async def test_identical_answer_is_not_a_translation(make_services):
    mm = FakeMyMemory({"зюзюкало": "зюзюкало"})
    svc = make_services(mymemory=mm)
    with pytest.raises(TranslationUnavailable):
        await svc.translation.translate_word("зюзюкало", Direction.UK_RU)


async def test_all_providers_down_raises(make_services):
    svc = make_services(wiktionary=FakeWiktionary(fail=True), mymemory=FakeMyMemory(fail=True))
    with pytest.raises(TranslationUnavailable):
        await svc.translation.translate_word("зюзюкало", Direction.UK_RU)
    errors = {r["name"] for r in svc.metrics.summary()}
    assert {"provider_error.wiktionary", "provider_error.mymemory"} <= errors


async def test_works_without_anthropic_key(make_services):
    svc = make_services(mymemory=FakeMyMemory({"добрий день": "добрый день"}))
    assert svc.correction.enabled is False
    result = await svc.translation.translate_phrase("добрий день", Direction.UK_RU)
    assert result.source is Source.MYMEMORY


async def test_phrase_falls_back_to_llm(make_services):
    llm = FakeLLM(translate="добрый день")
    svc = make_services(mymemory=FakeMyMemory(fail=True), llm=llm, ANTHROPIC_API_KEY="k")
    result = await svc.translation.translate_phrase("добрий день", Direction.UK_RU)
    assert result.source is Source.LLM


async def test_llm_daily_limit(make_services):
    llm = FakeLLM(translate="перевод")
    svc = make_services(mymemory=FakeMyMemory(fail=True), llm=llm, ANTHROPIC_API_KEY="k", LLM_DAILY_LIMIT=1)
    await svc.translation.translate_phrase("перша фраза", Direction.UK_RU)
    with pytest.raises(TranslationUnavailable):
        await svc.translation.translate_phrase("друга фраза", Direction.UK_RU)
    assert llm.calls == ["translate"]


async def test_natural_phrase_prefers_model_then_translator(make_services):
    llm = FakeLLM(phrase="Як справи?")
    svc = make_services(llm=llm, ANTHROPIC_API_KEY="k")
    assert (await svc.translation.natural_ukrainian("как дела")).source is Source.LLM
    svc2 = make_services(mymemory=FakeMyMemory({"как дела": "як справи"}))
    assert (await svc2.translation.natural_ukrainian("как дела")).source is Source.MYMEMORY


async def test_auto_direction_rejected(make_services):
    svc = make_services()
    with pytest.raises(ValueError):
        await svc.translation.translate_phrase("x y", Direction.AUTO)


# --------------------------------------------------------- Claude auto mode

AUTO_RU = '{"source": "ru", "translation": "Давай перевіримо"}'
AUTO_UK = '{"source": "uk", "translation": "Давай проверим"}'


async def test_auto_translate_detects_direction(make_services):
    svc = make_services(llm=FakeLLM(auto=AUTO_RU), ANTHROPIC_API_KEY="k")
    result, direction = await svc.translation.translate_auto("давай проверим")
    assert result.text == "Давай перевіримо" and result.source is Source.LLM
    assert direction is Direction.RU_UK
    svc2 = make_services(llm=FakeLLM(auto=AUTO_UK), ANTHROPIC_API_KEY="k")
    assert (await svc2.translation.translate_auto("давай перевіримо"))[1] is Direction.UK_RU


async def test_auto_translate_rejects_bad_output(make_services):
    for raw in ("not json", '{"source": "ru", "translation": "Давай выясним"}', '{"source": "xx"}'):
        svc = make_services(llm=FakeLLM(auto=raw), ANTHROPIC_API_KEY="k")
        assert await svc.translation.translate_auto("давай проверим") is None


async def test_auto_translate_off_without_key(make_services):
    assert await make_services().translation.translate_auto("давай проверим") is None
