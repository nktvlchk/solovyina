# Third-party notices

This project is distributed under the **GNU General Public License, version 2 or
(at your option) any later version** (see `LICENSE`). The license was chosen
because the bundled dictionary data comes from a GPL-2.0 project (Apertium,
below). A permissive license such as MIT would not be compatible with
redistributing that data.

### Compatibility note

`python-telegram-bot` is LGPL-3.0, which is compatible with GPL-3.0 but not with
GPL-2.0-only. The project code is therefore licensed "GPL-2.0-or-later", so the
running program can be distributed under GPL-3.0. Upstream Apertium ships the
GPL-2 text without stating "only" or "or later"; the dictionary is a data file
read at runtime, not linked code. If you need certainty for commercial reuse,
ask the Apertium maintainers to confirm "GPL-2.0-or-later" for this pair.

## Bundled data

### Apertium rus-ukr bilingual dictionary

| | |
|---|---|
| Project | apertium-rus-ukr — Apertium language pair Russian ↔ Ukrainian |
| Upstream | https://github.com/apertium/apertium-rus-ukr |
| File | `apertium-rus-ukr.rus-ukr.dix` |
| Bundled as | `data/sources/apertium/apertium-rus-ukr.rus-ukr.dix` (unmodified) |
| Verified against | upstream `master`, commit `1e8ad9857c14da1d076e17c8247d020e6038b38d` (checked 2026-10-10): byte-identical except for a trailing newline |
| SHA-256 of bundled file | `4c29e20504cb0ad25525b874067071ab423f72045af840897a2df05a044a3f20` |
| License | GNU GPL version 2 (`COPYING` in the upstream repository) |
| Copyright | 2009–2017 Francis M. Tyers, Jim O'Regan, Kevin Brubeck Unhammer, Kirill Krylov, Jonathan North Washington, Anthony J. Bentley, Trond Trosterud, Nicolas Vion, John Lyell (from upstream `AUTHORS`) |

`data/dictionary.tsv` is a derived work generated from this file by
`scripts/build_dictionary.py`; it is distributed under the same GPL terms.
Rows from this source are marked `source=apertium`.

### Manual dictionary entries

`data/sources/manual_dictionary.tsv` (rows marked `source=manual`) was migrated
from the former `big_dictionary.py`. **Its origin is not documented.** It is
not claimed as original work of this project. See `data/README.md` — the owner
must confirm the origin before this data can be treated as cleared.

### Spaced-repetition deck

`deck.json` contains study cards used by `drip.py`. Origin to be confirmed by
the owner (see `data/README.md`).

## External services used at runtime (not bundled)

| Service | Used for | Terms / license |
|---|---|---|
| Wiktionary (ru.wiktionary.org, MediaWiki API) | stress, definitions, examples | Text: [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) and GFDL. Content shown to users is attributed to Wiktionary. [Terms of Use](https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use) |
| MyMemory (api.mymemory.translated.net) | phrase translation | [Terms](https://mymemory.translated.net/doc/spec.php), free anonymous quota per day |
| Google Translate (unofficial, via `deep-translator`) | phrase translation | Not an official API; see limitations in README |
| Google Text-to-Speech (unofficial endpoint, via `gTTS`) | audio for a phrase on demand | Not an official API |
| Anthropic API (optional) | text checking, natural phrasing | [Anthropic Commercial Terms](https://www.anthropic.com/legal/commercial-terms) |
| Telegram Bot API | messaging | [Telegram Bot Terms](https://telegram.org/tos/bots) |

## Python dependencies

| Package | License |
|---|---|
| python-telegram-bot | LGPL-3.0 |
| gTTS | MIT |
| deep-translator | MIT |
| python-dotenv | BSD-3-Clause |
| anthropic | MIT |
