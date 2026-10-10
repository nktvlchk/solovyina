import subprocess
import sys
from collections import Counter
from pathlib import Path

from bot.config import DEFAULT_DICTIONARY
from bot.services.dictionary import Dictionary

ROOT = Path(__file__).resolve().parents[1]


def _dict() -> Dictionary:
    return Dictionary.load(DEFAULT_DICTIONARY)


def test_exact_lookup_uk():
    entries, form = _dict().lookup_uk("кіт")
    assert form is None
    assert any(e.ru == "кот" for e in entries)


def test_multiple_meanings_are_kept():
    entries, _ = _dict().lookup_uk("ніж")
    assert {"нож", "чем"} <= {e.ru for e in entries}


def test_reverse_lookup_ru():
    entries, _ = _dict().lookup_ru("нож")
    assert any(e.uk == "ніж" for e in entries)


def test_apostrophe_variants_are_equivalent():
    d = _dict()
    assert d.lookup_uk("п’ять")[0] == d.lookup_uk("п'ять")[0] == d.lookup_uk("пʼять")[0]


def test_case_and_spaces_normalized():
    assert _dict().lookup_uk("  КІТ ")[0]


def test_approximate_match_is_flagged():
    entries, form = _dict().lookup_uk("книгою")
    assert entries and form is not None and form != "книгою"


def test_unknown_word():
    assert _dict().lookup_uk("абвгджзщ") == ([], None)


# ------------------------------------------------------------ data quality


def test_build_is_reproducible_and_passes_quality_checks():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/build_dictionary.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_parts_of_speech_are_not_all_nouns():
    lines = DEFAULT_DICTIONARY.read_text(encoding="utf-8").splitlines()[1:]
    pos = Counter(line.split("\t")[2] for line in lines)
    assert pos["ім."] < 0.8 * len(lines)
    assert pos["спол."] > 0 and pos["дієсл."] > 0 and pos["числ."] > 0


def test_no_glued_expressions():
    text = DEFAULT_DICTIONARY.read_text(encoding="utf-8")
    for glued in ("тоесть", "потомучто", "таккак", "томущо", "такяк"):
        assert f"\t{glued}\t" not in text and f"\n{glued}\t" not in text
    entries, _ = _dict().lookup_uk("тобто")
    assert any(e.ru == "то есть" and e.pos == "спол." for e in entries)


def test_no_empty_values_and_valid_sources():
    lines = DEFAULT_DICTIONARY.read_text(encoding="utf-8").splitlines()
    assert lines[0].split("\t") == ["uk", "ru", "pos", "gender", "source", "example"]
    for line in lines[1:]:
        uk, ru, _pos, _gender, source, _example = line.split("\t")
        assert uk and ru
        assert source in {"apertium", "manual", "override"}
