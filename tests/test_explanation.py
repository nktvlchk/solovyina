import json

import pytest

from bot.models.explanation import ExplanationParseError, parse_explanation


def _response(**overrides):
    data = {
        "word": "ласий",
        "part_of_speech": "прикметник",
        "stress": "ла́сий",
        "definitions": ["Який дуже любить щось і прагне його отримати."],
        "example": "Він ласий до похвали.",
        "synonyms": ["охочий"],
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def test_parses_ukrainian_dictionary_entry():
    result = parse_explanation(_response(), "ласий")
    assert result.word == "ласий"
    assert result.part_of_speech == "прикметник"
    assert result.definitions == ("Який дуже любить щось і прагне його отримати.",)
    assert result.example == "Він ласий до похвали."


def test_accepts_fenced_json_and_caps_lists():
    raw = "```json\n" + _response(definitions=["а" + str(i) for i in range(8)]) + "\n```"
    assert len(parse_explanation(raw, "ласий").definitions) == 3


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not json",
        "{}",
        _response(definitions=[]),
        _response(example=""),
        _response(definitions=["ласий"]),
        _response(definitions=["жадный до денег"]),
    ],
)
def test_rejects_unusable_or_russian_entries(raw):
    with pytest.raises(ExplanationParseError):
        parse_explanation(raw, "ласий")
