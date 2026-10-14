"""SQLite storage. The database file lives outside git (data/runtime/)."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Only what the bot needs: no names, usernames or message history.
CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER PRIMARY KEY,
    direction TEXT NOT NULL DEFAULT 'auto',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS saved_words (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    word TEXT NOT NULL,
    translation TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, word)
);

-- One row per suggested fix. Stores the fragment, not the full text,
-- unless STORE_RAW_TEXT=true (off by default).
CREATE TABLE IF NOT EXISTS corrections (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    original TEXT NOT NULL,
    replacement TEXT NOT NULL,
    type TEXT NOT NULL,
    severity TEXT NOT NULL,
    explanation TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    raw_text TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_corrections_user ON corrections(user_id);

-- Personal Anki-like deck. `kind` is mistake (a confirmed correction) or word
-- (something the user asked to remember). Scheduling columns are SM-2-ish;
-- due_at is UTC 'YYYY-MM-DD HH:MM:SS'.
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('mistake', 'word')),
    source_key TEXT NOT NULL,
    ask TEXT NOT NULL CHECK (ask IN ('fix', 'say', 'mean')),
    front TEXT NOT NULL,
    back TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    ease REAL NOT NULL DEFAULT 2.5,
    interval_days INTEGER NOT NULL DEFAULT 0,
    reps INTEGER NOT NULL DEFAULT 0,
    lapses INTEGER NOT NULL DEFAULT 0,
    due_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, kind, source_key)
);
CREATE INDEX IF NOT EXISTS idx_cards_due ON cards(user_id, due_at);

-- One row per typed practice answer. Stores only whether it was right,
-- never the answer itself.
CREATE TABLE IF NOT EXISTS practice_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    ok INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_practice_user ON practice_log(user_id, created_at);

CREATE TABLE IF NOT EXISTS correction_feedback (
    correction_id TEXT NOT NULL REFERENCES corrections(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    verdict TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (correction_id, user_id)
);

-- Product analytics: one row per user action. Pseudonymous user key
-- (salted hash, not the Telegram ID), no message text, allow-listed fields.
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL DEFAULT (datetime('now')),
    user_key TEXT NOT NULL,
    event TEXT NOT NULL,
    source TEXT,
    direction TEXT,
    ok INTEGER,
    latency_ms INTEGER,
    chars INTEGER,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_key);

-- Aggregated counters only. Never per-user, never message text.
CREATE TABLE IF NOT EXISTS metrics_daily (
    day TEXT NOT NULL,
    name TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    total_ms INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, name)
);
"""


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.executescript(SCHEMA)
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def transaction(self, statements: list[tuple[str, tuple]]) -> None:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                for sql, params in statements:
                    self._conn.execute(sql, params)
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            self._conn.close()
