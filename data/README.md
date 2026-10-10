# Data and provenance

| File | What | Origin | Status |
|---|---|---|---|
| `sources/apertium/apertium-rus-ukr.rus-ukr.dix` | Apertium bilingual dictionary, unmodified | https://github.com/apertium/apertium-rus-ukr, matches commit `1e8ad98` | GPL-2.0, attributed in `THIRD_PARTY_NOTICES.md` |
| `sources/manual_dictionary.tsv` | ~1 900 hand-made entries with examples (was `big_dictionary.py`) | **Unknown** — no source recorded in the repository | ⚠️ **Blocking question for the owner**: who wrote it, was it generated, copied from a book/site/course? |
| `overrides.tsv` | Reviewed corrections applied on top of the sources | This project | Every row must have a `reason` |
| `dictionary.tsv` | Generated lookup table used by the bot | `scripts/build_dictionary.py` | Do not edit by hand |
| class card deck | Study cards for `drip.py` | Compiled from the owner's class materials | **Not in this repository**: hosted privately, loaded via `DRIP_DECK_URL`. `docs/deck.example.md` is a synthetic example |

## Removed data

* `data.py` (`DICTIONARY`, `TOPICS`, `BLOCK_CONTENT`) — its header said it was
  taken from "14 topics of a Ukrainian course (PDF files)". Course materials are
  normally copyrighted, so this data is **not redistributed**. Only 20 of its 441
  words were not already covered by other sources. It remains in git history;
  see the history-cleanup section of the PR.
* `grammar.py` — not imported anywhere.
* `apertium_dict.py` — a lossy export of the Apertium file: every word had the
  part of speech `n`, multiple meanings were lost and multi-word expressions
  were glued (`тоесть`, `потомучто`, `таккак`). Replaced by a reproducible build
  from the original `.dix`.

## Rebuilding

```bash
python scripts/build_dictionary.py          # writes data/dictionary.tsv
python scripts/build_dictionary.py --check  # CI: fails if stale or if quality checks fail
```

Normalization: Unicode NFC, lower case, all apostrophe variants (’ ʼ ‘ `) mapped
to `'`, whitespace collapsed. Quality checks: duplicates, empty values, known
glued expressions, non-normalized keys.
