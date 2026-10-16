# Deployment guide

This guide uses [Railway](https://railway.com). Any host that can run a
long-lived Python process works the same way.

> **Never put real tokens or keys in this repository** — not in code, not in
> docs, not in examples. They go only into the hosting provider's variables
> (or a local `.env`, which is git-ignored).

## Cost

Railway is a paid service with usage-based billing; trial and plan terms change.
Check the current prices before deploying: https://railway.com/pricing.
This guide intentionally gives no cost estimate.

## 1. Create a bot token

1. Open [@BotFather](https://t.me/BotFather), send `/newbot`.
2. Copy the token. Do not paste it into chats, issues, screenshots or files in the repo.

## 2. Deploy

1. Railway → **New Project** → **Deploy from GitHub repo** → select this repository.
2. Railway installs `requirements.txt` and runs the `worker` process from `Procfile`:
   `PYTHONPATH=src python -m bot`.

## 3. Set variables

Service → **Variables**:

| Variable | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | `your_telegram_bot_token` |
| `ANTHROPIC_API_KEY` | `your_anthropic_api_key` (optional) |
| `ADMIN_IDS` | `123456789` (optional, your numeric Telegram ID) |
| `DATABASE_PATH` | `/data/bot.sqlite3` (if you attach a volume, see below) |

All other variables have safe defaults (see `.env.example` and README).

### Persistent storage (optional)

Without a volume, the SQLite file is lost on every redeploy (settings,
feedback, daily model limit). To keep it, attach a Railway **Volume** mounted
at `/data` and set `DATABASE_PATH=/data/bot.sqlite3`.

Runtime files (`*.sqlite3`, `*.db`, `stats.json`, `favorites.json`, logs) must
never be committed; `.gitignore` and CI block them.

## 4. Check it works

1. **Deployments** shows *Active*.
2. In Telegram send `/start` to the bot.
3. **Logs**: you should see `dictionary: … headwords; model features: on/off`.

Logs contain technical events only (provider name, HTTP status, error class,
timings). They never contain message texts, and anything that looks like a
bot token or API key is replaced with `[REDACTED]`. Do not set
`LOG_LEVEL=DEBUG` in production.

## 5. If a token or key leaks

1. **Telegram:** @BotFather → `/revoke` → choose the bot. The old token stops
   working immediately. Then `/token` to get a new one.
2. Put the new value into Railway **Variables** (`TELEGRAM_BOT_TOKEN`) and
   redeploy. If you also run `drip.py` as a separate service, update it there too.
3. **Anthropic:** console.anthropic.com → API keys → delete the key, create a
   new one, update `ANTHROPIC_API_KEY`.
4. Removing the value from the files is not enough: it stays in git history.
   Rotate first, then clean history if needed (see the pull request notes).

## Optional: spaced-repetition poster (`drip.py`)

A separate, stdlib-only process that posts study cards to a class chat **once a
day** — one message, at a time drawn at random inside a daily window
(`08:00-20:00` by default). The message carries the day's new card plus
whatever is due for repetition (after 4, 10, 21 and 45 days), up to three cards.

The time of day is derived from the date, not from `random()`: every process
agrees on it, so a restart or a redeploy can neither move the send nor repeat
it. A send missed while the service was down is skipped, not caught up.

The deck itself is **not** stored in this public repository: class material
stays private. Put the deck file (Markdown format, see `docs/deck.example.md`)
into a **secret GitHub gist** and use its *Raw* URL.

Railway setup: a second service from this repository with
- Custom Start Command: `python drip.py`
- Variables: `TELEGRAM_BOT_TOKEN` (a bot that is a member of the chat),
  `DRIP_CHAT_ID`, `DRIP_DECK_URL` (raw gist URL), optionally `DRIP_START`,
  `DRIP_WINDOW` (e.g. `08:00-20:00`), `DRIP_SEED`, `DRIP_TZ`.
  `DRIP_SLOTS` from the old three-a-day schedule is ignored; the process logs a
  warning if it is still set.

The deck is re-downloaded before every send, so editing the gist updates the
cards without a redeploy. `DRIP_DECK_URL` must be the raw file without a
revision hash (`.../raw/deck.md`). A URL that contains `/raw/<sha>/` keeps
serving the old file.

Cards are scheduled by their position: card *i* is introduced `i` days after
`DRIP_START`. Moving `DRIP_START` back by *n* days is how you skip the first
*n* cards — useful after a change of cadence, to carry on where the previous
schedule had got to instead of starting the deck again. The chat ID is
configuration, never code.

### Lessons from the class chat

When `GITHUB_TOKEN` and `DECK_GIST_ID` are set on the **bot** service (not only
on the drip worker), an admin can send `/learn` plus the lesson text, a photo,
or a PDF in a private chat. The model drafts cards, the code checks them
(Ukrainian only, a vocabulary card has one to five items, ids and emoji are
added by the code) and inserts them at tomorrow's position. Cards that have
already been introduced stay on their days. Cards that have not gone out yet
move later by the size of the batch. If the draft is unusable, the gist is
left untouched.

The same thing happens on its own when a user listed in `TEACHER_IDS` posts in
`DRIP_CHAT_ID`. Only a word, a short word list, or a rule becomes cards.
Greetings and ordinary chat are ignored, and a word or rule already in the
deck is not added again. A vocabulary card holds one to five words, never
padded. The bot does not answer in the class chat. It sends a short note to
each `ADMIN_IDS` user only when cards were actually added.
`/deck` shows the current day and the last batch. `/deck_undo` removes that
batch.

Telegram only delivers ordinary group messages to a bot that is an admin of
the group, or whose privacy mode is disabled in BotFather (`/setprivacy`).
Without that, the teacher can post and the bot will not see it.

Set `DRIP_START` and `DRIP_TZ` on the bot service to the same values as the
drip worker. The token needs the GitHub **gist** scope only. The lesson text
is not stored; SQLite keeps the ids of the last batch so undo can find them.

## Build note

If a build fails with "No GitHub artifact attestations found for python",
add the variable `MISE_PYTHON_GITHUB_ATTESTATIONS=false` to the service.
