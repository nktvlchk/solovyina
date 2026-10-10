import json

import pytest

from bot.handlers import correction as ch
from bot.models.correction import MAX_CORRECTIONS, ParseError, Severity, parse_check_response
from tests.conftest import FakeLLM, make_context, make_update

TEXT = "Я ходив в магазін і купив молоко."


def _resp(corrections, corrected="Я ходив у магазин і купив молоко."):
    return json.dumps({"corrected_text": corrected, "corrections": corrections}, ensure_ascii=False)


GOOD = {
    "original": "магазін",
    "replacement": "магазин",
    "type": "spelling",
    "severity": "error",
    "explanation": "Пишется через и.",
    "confidence": 0.95,
}


def test_parse_valid():
    result = parse_check_response(_resp([GOOD]), TEXT)
    assert len(result.corrections) == 1
    c = result.corrections[0]
    assert c.severity is Severity.ERROR and c.replacement == "магазин" and len(c.id) <= 10


def test_no_errors_answer():
    result = parse_check_response(_resp([], corrected=TEXT), TEXT)
    assert result.corrections == () and result.corrected_text == TEXT
    assert result.has_errors is False


def test_style_is_never_an_error():
    item = {**GOOD, "type": "style", "severity": "error"}
    assert parse_check_response(_resp([item]), TEXT).corrections[0].severity is Severity.SUGGESTION


def test_hallucinated_fragment_dropped():
    item = {**GOOD, "original": "чого немає в тексті"}
    assert parse_check_response(_resp([item]), TEXT).corrections == ()


def test_noop_correction_dropped():
    item = {**GOOD, "replacement": "магазін"}
    assert parse_check_response(_resp([item]), TEXT).corrections == ()


def test_max_corrections_and_errors_first():
    items = [
        {**GOOD, "original": "Я", "replacement": "я", "type": "style", "severity": "suggestion"},
        {**GOOD, "original": "ходив", "replacement": "ходив би"},
        {**GOOD, "original": "в", "replacement": "у", "type": "grammar"},
        GOOD,
        {**GOOD, "original": "купив", "replacement": "придбав", "type": "style"},
    ]
    result = parse_check_response(_resp(items), TEXT)
    assert len(result.corrections) == MAX_CORRECTIONS
    assert all(c.severity is Severity.ERROR for c in result.corrections)


def test_fenced_json_accepted():
    assert parse_check_response("```json\n" + _resp([GOOD]) + "\n```", TEXT).corrections


@pytest.mark.parametrize("raw", ["", "not json", "{bad json}", "[1,2]", '{"corrections": []}'])
def test_invalid_output_raises(raw):
    with pytest.raises(ParseError):
        parse_check_response(raw, TEXT)


# ------------------------------------------------------------- handler


async def test_check_handler_shows_corrections_with_buttons(make_services):
    svc = make_services(llm=FakeLLM(check=_resp([GOOD])), ANTHROPIC_API_KEY="k")
    update = make_update()
    await ch.run_check(update, make_context(svc), TEXT)
    sent = update.effective_message.sent[1:]  # [0] is the "checking…" status
    assert update.effective_message.deleted
    assert "магазин" in sent[0]["text"]
    fb = sent[1]["reply_markup"].inline_keyboard
    labels = [b.text for row in fb for b in row]
    assert labels == ["✅ Це помилка", "✍️ Лише стиль", "❌ Тут немає помилки"]
    assert all(len(b.callback_data.encode()) <= 64 for row in fb for b in row)


async def test_check_handler_no_errors(make_services):
    svc = make_services(llm=FakeLLM(check=_resp([], corrected=TEXT)), ANTHROPIC_API_KEY="k")
    update = make_update()
    await ch.run_check(update, make_context(svc), TEXT)
    sent = update.effective_message.sent[1:]
    assert "Помилок не знайдено" in sent[0]["text"]
    assert len(sent) == 1


async def test_bad_model_output_never_shown_raw(make_services):
    svc = make_services(llm=FakeLLM(check='{"oops": <raw>'), ANTHROPIC_API_KEY="k")
    update = make_update()
    await ch.run_check(update, make_context(svc), TEXT)
    msg = update.effective_message.edited[-1]
    assert "oops" not in msg and "{" not in msg


async def test_check_disabled_without_key(make_services):
    svc = make_services()
    update = make_update("/check")
    await ch.cmd_check(update, make_context(svc, args=["текст"]))
    assert "вимкнена" in update.effective_message.sent[-1]["text"]


async def test_full_text_not_stored_by_default(make_services):
    svc = make_services(llm=FakeLLM(check=_resp([GOOD])), ANTHROPIC_API_KEY="k")
    await svc.correction.check(1, TEXT)
    rows = svc.db.query("SELECT original, raw_text, model, prompt_version FROM corrections")
    assert rows[0]["raw_text"] is None
    assert rows[0]["original"] == "магазін"
    assert rows[0]["model"] == "fake-model" and rows[0]["prompt_version"].startswith("check-")


async def test_full_text_stored_only_when_enabled(make_services):
    svc = make_services(llm=FakeLLM(check=_resp([GOOD])), ANTHROPIC_API_KEY="k", STORE_RAW_TEXT="true")
    await svc.correction.check(1, TEXT)
    assert svc.db.query("SELECT raw_text FROM corrections")[0]["raw_text"] == TEXT


def test_check_prompt_requires_ukrainian_explanations():
    from bot.providers.anthropic import CHECK_PROMPT_VERSION, CHECK_SYSTEM

    assert "in Ukrainian only, never in Russian" in CHECK_SYSTEM
    assert "in Russian>" not in CHECK_SYSTEM
    assert CHECK_PROMPT_VERSION == "check-v3"
