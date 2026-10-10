#!/usr/bin/env python3
"""Build data/dictionary.tsv from tracked source files.

Inputs (all tracked in git, with provenance documented in data/README.md):

* data/sources/apertium/apertium-rus-ukr.rus-ukr.dix
      Apertium rus-ukr bilingual dictionary (GPL-2.0). Left side = Russian,
      right side = Ukrainian.
* data/sources/manual_dictionary.tsv
      Hand-made entries migrated from the old big_dictionary.py.
      Provenance must still be confirmed by the owner (see data/README.md).
* data/overrides.tsv
      Explicit, reviewed corrections. Each row says what it changes and why.

Output:

* data/dictionary.tsv — one row per (uk, ru, pos) meaning; several meanings of
  one word are kept as separate rows. Columns:
  uk, ru, pos, gender, source, example

The script is deterministic: running it twice yields byte-identical output.
``--check`` exits non-zero if the committed file is out of date or if the
quality checks fail (used in CI).
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIX = ROOT / "data/sources/apertium/apertium-rus-ukr.rus-ukr.dix"
MANUAL = ROOT / "data/sources/manual_dictionary.tsv"
OVERRIDES = ROOT / "data/overrides.tsv"
OUTPUT = ROOT / "data/dictionary.tsv"

COLUMNS = ["uk", "ru", "pos", "gender", "source", "example"]

# Apertium tag -> short Ukrainian label shown to users.
POS_LABELS = {
    "n": "ім.",
    "np": "власна назва",
    "adj": "прикм.",
    "vblex": "дієсл.",
    "vbser": "дієсл.",
    "vbmod": "дієсл.",
    "vbhaver": "дієсл.",
    "adv": "присл.",
    "pred": "присудк. сл.",
    "num": "числ.",
    "pr": "прийм.",
    "cnjcoo": "спол.",
    "cnjsub": "спол.",
    "cnjadv": "спол.",
    "prn": "займ.",
    "det": "займ.",
    "ij": "вигук",
    "part": "частка",
    "abbr": "скор.",
}
# Old manual labels -> the same labels as POS_LABELS.
MANUAL_POS_ALIASES = {"виг.": "вигук", "част.": "частка"}
GENDER_LABELS = {"m": "ч.", "f": "ж.", "nt": "с.", "mf": "ч./ж.", "mfn": ""}

APOSTROPHES = "’ʼ‘`´"


def normalize(text: str) -> str:
    """Canonical key form: NFC, lower case, ASCII apostrophe, single spaces."""
    text = unicodedata.normalize("NFC", text)
    for ch in APOSTROPHES:
        text = text.replace(ch, "'")
    return " ".join(text.lower().split())


@dataclass(frozen=True, order=True)
class Entry:
    uk: str
    ru: str
    pos: str
    gender: str
    source: str
    example: str = ""


def _side_text(node: ET.Element) -> tuple[str, list[str]]:
    """Surface text and tag list of an <l>/<r> node. <b/> is a blank."""
    parts: list[str] = [node.text or ""]
    tags: list[str] = []

    def walk(el: ET.Element) -> None:
        for child in el:
            if child.tag == "b":
                parts.append(" ")
            elif child.tag == "s":
                tags.append(child.get("n", ""))
            elif child.tag == "g":
                parts.append(child.text or "")
                walk(child)
            parts.append(child.tail or "")

    walk(node)
    return "".join(parts), tags


def parse_dix(path: Path) -> list[Entry]:
    root = ET.parse(path).getroot()
    entries: list[Entry] = []
    for e in root.iter("e"):
        p = e.find("p")
        if p is None:
            continue
        left, right = p.find("l"), p.find("r")
        if left is None or right is None:
            continue
        ru, _ru_tags = _side_text(left)
        uk, uk_tags = _side_text(right)
        ru, uk = normalize(ru), normalize(uk)
        if not any(ch.isalpha() for ch in ru) or not any(ch.isalpha() for ch in uk):
            continue  # regex/numeric/punctuation rules have no lemma
        pos_tag = uk_tags[0] if uk_tags else ""
        pos = POS_LABELS.get(pos_tag, "")
        gender = ""
        if pos_tag == "n":
            gender = next((GENDER_LABELS[t] for t in uk_tags if t in GENDER_LABELS), "")
        entries.append(Entry(uk=uk, ru=ru, pos=pos, gender=gender, source="apertium"))
    return entries


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t", quoting=csv.QUOTE_NONE))
    return rows


def parse_manual(path: Path) -> list[Entry]:
    out = []
    for row in read_tsv(path):
        uk, ru = normalize(row["uk"]), normalize(row["ru"])
        if uk and ru:
            out.append(
                Entry(
                    uk=uk,
                    ru=ru,
                    pos=MANUAL_POS_ALIASES.get(row["pos"].strip(), row["pos"].strip()),
                    gender=row.get("gender", "").strip(),
                    source="manual",
                    example=" ".join(row.get("example", "").split()),
                )
            )
    return out


def apply_overrides(entries: list[Entry], path: Path) -> list[Entry]:
    """Rows: action (drop|add), uk, ru, pos, gender, example, reason."""
    result = list(entries)
    for row in read_tsv(path):
        action = row["action"].strip()
        uk, ru = normalize(row["uk"]), normalize(row["ru"])
        if not row.get("reason", "").strip():
            raise SystemExit(f"override without reason: {row}")
        if action == "drop":
            result = [e for e in result if not (e.uk == uk and (not ru or e.ru == ru))]
        elif action == "add":
            result.append(
                Entry(
                    uk=uk,
                    ru=ru,
                    pos=row.get("pos", "").strip(),
                    gender=row.get("gender", "").strip(),
                    source="override",
                    example=row.get("example", "").strip(),
                )
            )
        else:
            raise SystemExit(f"unknown override action: {action!r}")
    return result


def dedupe(entries: list[Entry]) -> list[Entry]:
    """Keep one row per (uk, ru, pos). Prefer manual > override > apertium for
    examples, but never invent a part of speech."""
    priority = {"override": 0, "manual": 1, "apertium": 2}
    best: dict[tuple[str, str, str], Entry] = {}
    for e in sorted(entries, key=lambda x: (priority[x.source], x)):
        key = (e.uk, e.ru, e.pos)
        if key not in best:
            best[key] = e
    return sorted(best.values(), key=lambda e: (e.uk, e.ru, e.pos, e.source))


# Known concatenation bugs from the old export (missing <b/> blanks).
GLUED = {"тоесть", "потомучто", "таккак", "томущо", "такяк", "толькочто", "послетого,как"}


def quality_problems(entries: list[Entry]) -> list[str]:
    problems = []
    keys = Counter((e.uk, e.ru, e.pos) for e in entries)
    problems += [f"duplicate: {k}" for k, n in keys.items() if n > 1]
    for e in entries:
        if not e.uk or not e.ru:
            problems.append(f"empty value: {e}")
        if e.uk in GLUED or e.ru in GLUED:
            problems.append(f"glued expression: {e.uk} / {e.ru}")
        if "\t" in e.example or "\n" in e.example:
            problems.append(f"bad example: {e.uk}")
        for value in (e.uk, e.ru):
            if value != normalize(value):
                problems.append(f"not normalized: {value!r}")
    return problems


def render(entries: list[Entry]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_NONE)
    writer.writerow(COLUMNS)
    for e in entries:
        writer.writerow([e.uk, e.ru, e.pos, e.gender, e.source, e.example])
    return buf.getvalue()


def build() -> tuple[str, list[Entry]]:
    entries = parse_dix(DIX) + parse_manual(MANUAL)
    entries = apply_overrides(entries, OVERRIDES)
    entries = dedupe(entries)
    return render(entries), entries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="verify, do not write")
    args = parser.parse_args()

    text, entries = build()
    problems = quality_problems(entries)
    for p in problems[:50]:
        print("PROBLEM", p, file=sys.stderr)
    if problems:
        print(f"{len(problems)} quality problems", file=sys.stderr)
        return 1

    by_source = Counter(e.source for e in entries)
    print(f"{len(entries)} rows, {len({e.uk for e in entries})} Ukrainian headwords, {dict(by_source)}")

    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != text:
            print("data/dictionary.tsv is out of date: run scripts/build_dictionary.py", file=sys.stderr)
            return 1
        return 0
    OUTPUT.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
