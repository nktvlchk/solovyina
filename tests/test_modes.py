import json

from bot.handlers import modes
from bot.handlers import translation as th
from bot.models.correction import Verdict
from tests.conftest import FakeLLM, FakeMyMemory, make_context, make_update


async def test_say_mode_is_one_shot(make_services):
    svc = make_services(llm=FakeLLM(phrase="Як справи?"), ANTHROPIC_API_KEY="k")
    ctx = make_context(svc)
    await th.on_text(make_update("💬 Як сказати?"), ctx)
    update = make_update("как дела")
    await th.on_text(update, ctx)
    assert "Як справи?" in update.effective_message.sent[-1]["text"]
    assert modes.MODE_KEY not in ctx.user_data  # next message is a normal translation


async def test_say_without_model_uses_translator(make_services):
    svc = make_services(mymemory=FakeMyMemory({"как дела": "як справи"}))
    update = make_update("/say")
    await modes.cmd_say(update, make_context(svc, args=["как", "дела"]))
    text = update.effective_message.sent[-1]["text"]
    assert "як справи" in text and "MyMemory" in text


async def test_check_button_routes_next_message(make_services):
    resp = json.dumps({"corrected_text": "Добре.", "corrections": []})
    llm = FakeLLM(check=resp)
    svc = make_services(llm=llm, ANTHROPIC_API_KEY="k")
    ctx = make_context(svc)
    await th.on_text(make_update("✍️ Перевір мене"), ctx)
    await th.on_text(make_update("Добре."), ctx)
    assert llm.calls == ["check"]


async def test_explain_word(make_services):
    svc = make_services()
    ctx = make_context(svc)
    await th.on_text(make_update("📖 Поясни слово"), ctx)
    update = make_update("кіт")
    await th.on_text(update, ctx)
    assert "кот" in update.effective_message.sent[-1]["text"]


async def test_my_mistakes_lists_only_confirmed(make_services):
    resp = json.dumps(
        {
            "corrected_text": "у магазин",
            "corrections": [
                {
                    "original": "в",
                    "replacement": "у",
                    "type": "grammar",
                    "severity": "error",
                    "explanation": "чергування у/в",
                    "confidence": 0.9,
                },
                {
                    "original": "магазин",
                    "replacement": "крамницю",
                    "type": "style",
                    "severity": "suggestion",
                    "explanation": "стиль",
                    "confidence": 0.5,
                },
            ],
        },
        ensure_ascii=False,
    )
    svc = make_services(llm=FakeLLM(check=resp), ANTHROPIC_API_KEY="k")
    result = await svc.correction.check(1, "в магазин")
    svc.corrections.set_verdict(result.corrections[0].id, 1, Verdict.ERROR)
    svc.corrections.set_verdict(result.corrections[1].id, 1, Verdict.STYLE)
    update = make_update("🗂 Мої помилки")
    await th.on_text(update, make_context(svc))
    text = update.effective_message.sent[-1]["text"]
    assert "чергування" in text and "крамницю" not in text


async def test_my_mistakes_empty(make_services):
    svc = make_services()
    update = make_update("/mistakes")
    await modes.cmd_mistakes(update, make_context(svc))
    assert "Тут з'являться" in update.effective_message.sent[-1]["text"]
