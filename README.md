<img src="docs/logo.png" alt="Solovyina logo" width="96" align="right">

# Solovyina (Солов'їна)

**Try it in Telegram: [@solovyinamova_bot](https://t.me/solovyinamova_bot)**

*Ukrainian is often called «солов'їна мова», the nightingale language.*

**English** · [Українська](README.uk.md)

A Telegram assistant for Russian speakers who are switching to Ukrainian and
want to write more naturally, understand their mistakes and remember the
corrections.

It is a learning aid. It does **not** replace a professional translator or a
teacher, and its model-based checks can be wrong.

<img src="docs/demo.svg" alt="Illustrative mock-up of a text check with feedback buttons (synthetic text)" width="360">

*The screenshot is a mock-up with synthetic text, not a real conversation.*

## The problem

Many people who grew up with Russian now want to use Ukrainian every day. The
hard part is not vocabulary but the small things: calques from Russian, case
government, the `у/в` alternation, stress. Generic translators answer
"what does it mean", not "how would a native speaker say it" or "what exactly
did I get wrong".

## Demo scenario

1. `/start` shows four modes on the keyboard.
2. **💬 Як сказати?** → `мне нужно отпроситься с работы` → a natural Ukrainian
   phrasing, with the source of the answer and a 🔊 button.
3. **✍️ Перевір мене** → `Я ходив в магазін.` → the corrected text and up to
   three explanations. Every correction has its own buttons:
   `✅ Це помилка` · `❌ Тут немає помилки` · `✍️ Лише стиль`.
4. **🗂 Мої помилки** → the corrections you confirmed.
5. **🃏 Повторити** → flashcards (question, then the answer, then Again / Hard /
   Good / Easy) from those mistakes and from words you asked to remember.
6. Any other message is translated (uk ⇄ ru). Every reply names the source
   it actually came from.

## Features (what the code really does)

| Feature | How |
|---|---|
| Translation ru ⇄ uk | Claude detects the language and translates; dictionary for single known words; Google Translate / MyMemory as fallback |
| `/check` | explanations in Ukrainian only |
| "Як сказати?" (natural phrasing) | model → MyMemory fallback |
| "Перевір мене" (`/check`) | model returns validated JSON: max 3 corrections, error vs style, "no errors" allowed |
| Feedback per correction | stored with model name and prompt version, re-voting allowed, no duplicates |
| "Мої помилки" (`/mistakes`) | corrections you confirmed as errors |
| Review cards (`/review`) | Anki-like again / hard / good / easy. Deck = confirmed mistakes + words saved with 📌 or `/remember` |
| "Поясни слово" | dictionary entry, part of speech, Wiktionary definitions, stress/IPA, example |
| Audio | gTTS, **only on button press**, generated in memory (no temp files); speech synthesis, not pronunciation assessment |
| Direction | auto-detection is only a hint; `unknown` → the bot asks; `/direction` pins it |
| Privacy | `/privacy`, `/delete_my_data`, retention, no names/usernames/message history |
| Product analytics | `/stats [days]` for `ADMIN_IDS`: active/new/returning users, actions by feature, translation sources, share of "error / not an error / style" ratings, quality per model and prompt version; `/export_stats` sends a CSV of pseudonymous events for 30 days |

Commands: `/start`, `/help`, `/tr`, `/say`, `/check`, `/mistakes`, `/review`,
`/remember`, `/direction`, `/privacy`, `/delete_my_data`, `/about`, `/stats` (admin).

**In group chats** the bot stays silent unless asked: it ignores ordinary
messages and answers only `/tr` (translate the text after the command, or the
message you reply to), `/check` and `/say`. The optional `drip.py` worker can
post study cards into the same group once a day, at a time drawn at random
inside a daily window.

## Architecture

```
src/bot/
  main.py, app.py, config.py      entry point, wiring, env validation
  handlers/                       Telegram only: common, translation, modes,
                                  correction, feedback, review, admin
  services/                       business logic: dictionary, translation chain,
                                  correction, tts, limits
  providers/                      external APIs: wiktionary, mymemory, anthropic
                                  (shared HTTP helper with timeouts/retries)
  storage/                        SQLite + repositories
  models/                         TranslationResult, Correction, parsing
  language.py, formatting.py      detection, HTML escaping, message splitting
data/                             dictionary + sources (see data/README.md)
scripts/                          build_dictionary.py, check_secrets.py
tests/                            pytest, no network
drip.py                           optional spaced-repetition poster for a class chat (deck hosted privately)
```

Every translation returns `TranslationResult(text, source, confidence, metadata)`
where `source` is the provider that actually produced the answer.

## Translation order

**Main path (any text, direction not pinned):** Claude detects whether the
text is Russian or Ukrainian and translates it into the other language in one
call. No language question is asked.

**Single word with an exact dictionary hit:** answered locally (instant, free,
several meanings, part of speech) plus Wiktionary stress/definitions.

**Fallback (no `ANTHROPIC_API_KEY`, budget exhausted, or a pinned direction):**
Claude → Google Translate (official API, if `GOOGLE_TRANSLATE_API_KEY` is set) →
MyMemory. Every answer must look like the target language (e.g. no `ы/э/ё/ъ`
in a Ukrainian result), otherwise the next provider is tried. If the language
was only guessed, the opposite direction is tried too.

**Як сказати?:** Claude (natural phrasing) → Google Translate → MyMemory.

Providers are called one after another, never in parallel "just in case".
If a provider returns the input unchanged, it is not treated as a translation.

## Run locally

Requires Python 3.11+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # put your own token in .env
PYTHONPATH=src python -m bot
```

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | **yes** | — | from @BotFather |
| `ANTHROPIC_API_KEY` | no (recommended) | — | Claude: main translator, `/check`, "Як сказати?" |
| `GOOGLE_TRANSLATE_API_KEY` | no | — | official Google Cloud Translation API as fallback |
| `REDIRECT_TO_BOT` | no | — | redirect mode for a retired bot: answers everything with "moved to @…" |
| `LLM_MODEL` | no | `claude-haiku-4-5-20251001` | model name (stored with each correction) |
| `LLM_DAILY_LIMIT` | no | `500` | max paid model calls per day (all users) |
| `LLM_USER_DAILY_LIMIT` | no | `60` | max model calls per user per day (in memory) |
| `ADMIN_IDS` | no | — | comma-separated Telegram IDs allowed to use `/stats` |
| `DATABASE_PATH` | no | `data/runtime/bot.sqlite3` | SQLite file (git-ignored) |
| `DICTIONARY_PATH` | no | `data/dictionary.tsv` | generated dictionary file |
| `STORE_RAW_TEXT` | no | `false` | store the full checked text (off by default) |
| `RETENTION_DAYS` | no | `180` | stored corrections are deleted after this |
| `MAX_INPUT_LENGTH` | no | `1000` | max characters per message |
| `MAX_TTS_LENGTH` | no | `300` | max characters for audio |
| `USER_RATE_LIMIT_PER_MINUTE` | no | `20` | per-user request limit |
| `MAX_CONCURRENT_REQUESTS` | no | `8` | concurrent external requests |
| `LOG_LEVEL` | no | `INFO` | logging level |

The bot exits with a clear message if `TELEGRAM_BOT_TOKEN` is missing; it never
prints the value.

## Deployment

See [DEPLOY_GUIDE.md](DEPLOY_GUIDE.md). Secrets live only in the hosting
provider's variables, never in the repository.

## Privacy

* Processed: the text you send and your numeric Telegram ID.
* Sent to third parties: Wiktionary (single words), MyMemory (translation),
  Anthropic (checks/phrasing, if enabled), Google TTS (only when 🔊 is pressed).
* Stored: chosen direction; for checks only the corrected fragments, the
  explanation, model name, prompt version and your rating; review cards (the
  fragment or word, the answer, and when it is due); a product-analytics
  log of actions (feature, source, direction, success, latency, text length,
  rating) under a salted pseudonymous key, never the Telegram ID or the text;
  aggregated counters.
* Not stored: names, usernames, message history, full texts (unless
  `STORE_RAW_TEXT=true`).
* Logs contain no message text (library loggers that could print updates are
  kept at WARNING even with `LOG_LEVEL=DEBUG`); tokens/keys are redacted,
  tracebacks included.
* `/delete_my_data` removes everything linked to your ID; old records are
  purged after `RETENTION_DAYS`.

## Limitations

* Language detection is heuristic; Ukrainian and Russian share most letters.
* The local dictionary has no inflection tables; inflected forms are matched
  approximately and labeled as such.
* MyMemory has an anonymous daily quota; when it is exhausted phrases fall back
  to the model (if configured) or fail with a clear message.
* gTTS uses an unofficial Google endpoint and may stop working.
* Model checks can miss mistakes or flag correct text. Quality has **not been
  measured yet**; the feedback buttons exist to measure it.
* Wiktionary coverage of Ukrainian is partial.

## Data sources and licenses

The project is licensed under **GPL-2.0-or-later** ([LICENSE](LICENSE)),
because the dictionary is built from GPL-2.0 data.

* **Apertium rus-ukr** — https://github.com/apertium/apertium-rus-ukr, GPL-2.0;
  bundled unmodified in `data/sources/apertium/`.
* **Wiktionary** (ru.wiktionary.org) — fetched at runtime, CC BY-SA 4.0,
  attributed in every reply that uses it.
* **Manual dictionary entries** — origin not documented yet; see
  [data/README.md](data/README.md).
* External services: [MyMemory](https://mymemory.translated.net/doc/spec.php),
  [Anthropic](https://www.anthropic.com/legal/commercial-terms),
  [Telegram Bot API](https://core.telegram.org/bots/api), gTTS.

Details: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Testing

```bash
ruff check . && ruff format --check .
python scripts/build_dictionary.py --check   # dictionary is reproducible
pytest                                       # no network, no real keys
pip-audit -r requirements.txt
python scripts/check_secrets.py
```

CI runs all of the above on every pull request
([.github/workflows/ci.yml](.github/workflows/ci.yml)), plus gitleaks.

## Roadmap

1. Measure check quality: share of confirmed errors and false corrections
   per model / prompt version (data is already collected).
2. Word forms in "Поясни слово" (declension/conjugation from an open source).

Out of scope for now: a full course, a voice tutor, pronunciation scoring.
