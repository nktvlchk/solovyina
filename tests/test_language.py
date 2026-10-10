from bot.language import Direction, Lang, detect_language


def test_ukrainian_specific_letters():
    assert detect_language("Привіт, як справи?").lang is Lang.UK


def test_russian_specific_letters():
    assert detect_language("Это очень интересно").lang is Lang.RU


def test_shared_cyrillic_only_is_unknown():
    # "мама" is the same word in both languages
    result = detect_language("мама")
    assert result.lang is Lang.UNKNOWN
    assert result.confidence == 0.0


def test_ukrainian_letter_is_a_signal_not_the_only_rule():
    # one Ukrainian letter against strong Russian evidence must not win
    result = detect_language("что это такое, і вот")
    assert result.lang is not Lang.UK


def test_function_words_without_specific_letters():
    assert detect_language("що це таке").lang is Lang.UK
    assert detect_language("как дела").lang in {Lang.RU, Lang.UNKNOWN}


def test_dictionary_signal_for_single_word():
    result = detect_language("собака", in_uk_dictionary=lambda w: False, in_ru_dictionary=lambda w: True)
    assert result.lang is Lang.RU


def test_apostrophe_signal():
    assert "uk-apostrophe" in detect_language("м'ясо").signals


def test_empty_and_non_letters():
    assert detect_language("12345 !!!").lang is Lang.UNKNOWN


def test_direction_mapping():
    assert Direction.from_source(Lang.UK) is Direction.UK_RU
    assert Direction.from_source(Lang.RU) is Direction.RU_UK
    assert Direction.from_source(Lang.UNKNOWN) is Direction.AUTO
    assert Direction.UK_RU.target is Lang.RU
