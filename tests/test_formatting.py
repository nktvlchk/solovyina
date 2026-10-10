from bot.formatting import TELEGRAM_MESSAGE_LIMIT, esc, split_message, truncate
from bot.handlers.translation import format_phrase, format_word
from bot.language import Direction
from bot.models.translation import DictionaryEntry, Source, TranslationResult, WiktionaryEntry
from bot.services.translation import WordLookup


def test_escape_html():
    assert esc("<b>x</b> & y") == "&lt;b&gt;x&lt;/b&gt; &amp; y"


def test_user_input_cannot_inject_markup():
    lookup = WordLookup(
        query='<a href="http://evil">x</a>',
        direction=Direction.UK_RU,
        result=TranslationResult("<i>t</i>", Source.MYMEMORY),
    )
    out = format_word(lookup)
    assert "<a href" not in out and "&lt;a href" in out
    assert "<i>t</i>" not in out


def test_provider_and_wiktionary_text_escaped():
    lookup = WordLookup(
        query="кіт",
        direction=Direction.UK_RU,
        result=TranslationResult("кот", Source.DICTIONARY),
        entries=[DictionaryEntry("кіт", "кот<script>", "ім.")],
        wiktionary=WiktionaryEntry("кіт", "ім.", ("def <b>bold</b>",), url="https://x/?a=1&b=2"),
    )
    out = format_word(lookup)
    assert "<script>" not in out and "<b>bold</b>" not in out
    assert "a=1&amp;b=2" in out


def test_phrase_format_shows_actual_source():
    out = format_phrase("a < b", TranslationResult("c", Source.MYMEMORY))
    assert "a &lt; b" in out and "MyMemory" in out


def test_split_long_message():
    text = "\n".join(["рядок " * 50] * 100)
    parts = split_message(text)
    assert len(parts) > 1
    assert all(len(p) <= TELEGRAM_MESSAGE_LIMIT for p in parts)
    assert "".join(p.replace("\n", "") for p in parts) == text.replace("\n", "")


def test_split_does_not_cut_entities():
    text = ("a" * 4093) + "&amp;" + ("b" * 100)
    parts = split_message(text)
    assert all(len(p) <= TELEGRAM_MESSAGE_LIMIT for p in parts)
    assert parts[1].startswith("&amp;")


def test_truncate():
    assert truncate("abcdef", 4) == "abc…"
    assert truncate("ab", 4) == "ab"
