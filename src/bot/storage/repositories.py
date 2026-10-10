"""Data access. Every query is parameterized."""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets
from dataclasses import dataclass

from bot.language import Direction
from bot.models.correction import Correction, Verdict
from bot.storage.database import Database


def today() -> str:
    return dt.datetime.now(dt.UTC).date().isoformat()


class SettingsRepository:
    def __init__(self, db: Database):
        self.db = db

    def get_direction(self, user_id: int) -> Direction:
        rows = self.db.query("SELECT direction FROM user_settings WHERE user_id = ?", (user_id,))
        if not rows:
            return Direction.AUTO
        try:
            return Direction(rows[0]["direction"])
        except ValueError:
            return Direction.AUTO

    def set_direction(self, user_id: int, direction: Direction) -> None:
        self.db.execute(
            "INSERT INTO user_settings(user_id, direction) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET direction = excluded.direction, "
            "updated_at = datetime('now')",
            (user_id, direction.value),
        )


class SavedWordsRepository:
    def __init__(self, db: Database):
        self.db = db

    def save(self, user_id: int, word: str, translation: str) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO saved_words(user_id, word, translation) VALUES (?, ?, ?)",
            (user_id, word[:100], translation[:300]),
        )

    def list(self, user_id: int, limit: int = 20) -> list[tuple[str, str]]:
        rows = self.db.query(
            "SELECT word, translation FROM saved_words WHERE user_id = ? "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (user_id, limit),
        )
        return [(r["word"], r["translation"]) for r in rows]


@dataclass(frozen=True)
class StoredCorrection:
    id: str
    original: str
    replacement: str
    type: str
    explanation: str
    verdict: str | None


class CorrectionsRepository:
    def __init__(self, db: Database):
        self.db = db

    def add(
        self,
        user_id: int,
        correction: Correction,
        model: str,
        prompt_version: str,
        raw_text: str | None = None,
    ) -> None:
        self.db.execute(
            "INSERT INTO corrections(id, user_id, original, replacement, type, severity, "
            "explanation, model, prompt_version, raw_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                correction.id,
                user_id,
                correction.original,
                correction.replacement,
                correction.type.value,
                correction.severity.value,
                correction.explanation,
                model,
                prompt_version,
                raw_text,
            ),
        )

    def owner(self, correction_id: str) -> int | None:
        rows = self.db.query("SELECT user_id FROM corrections WHERE id = ?", (correction_id,))
        return rows[0]["user_id"] if rows else None

    def set_verdict(self, correction_id: str, user_id: int, verdict: Verdict) -> bool:
        """Upsert: re-voting changes the verdict, never duplicates it.
        Returns True if the stored verdict changed."""
        rows = self.db.query(
            "SELECT verdict FROM correction_feedback WHERE correction_id = ? AND user_id = ?",
            (correction_id, user_id),
        )
        if rows and rows[0]["verdict"] == verdict.value:
            return False
        self.db.execute(
            "INSERT INTO correction_feedback(correction_id, user_id, verdict) VALUES (?, ?, ?) "
            "ON CONFLICT(correction_id, user_id) DO UPDATE SET verdict = excluded.verdict, "
            "updated_at = datetime('now')",
            (correction_id, user_id, verdict.value),
        )
        return True

    def get_verdict(self, correction_id: str, user_id: int) -> Verdict | None:
        rows = self.db.query(
            "SELECT verdict FROM correction_feedback WHERE correction_id = ? AND user_id = ?",
            (correction_id, user_id),
        )
        return Verdict(rows[0]["verdict"]) if rows else None

    def confirmed_for_user(self, user_id: int, limit: int = 10) -> list[StoredCorrection]:
        rows = self.db.query(
            "SELECT c.id, c.original, c.replacement, c.type, c.explanation, f.verdict "
            "FROM corrections c JOIN correction_feedback f ON f.correction_id = c.id "
            "WHERE c.user_id = ? AND f.user_id = ? AND f.verdict = ? "
            "ORDER BY f.updated_at DESC LIMIT ?",
            (user_id, user_id, Verdict.ERROR.value, limit),
        )
        return [StoredCorrection(**dict(r)) for r in rows]

    def quality_summary(self) -> list[dict]:
        """Aggregate quality by model / prompt version / type (admin only)."""
        rows = self.db.query(
            "SELECT c.model, c.prompt_version, c.type, f.verdict, COUNT(*) AS n "
            "FROM corrections c JOIN correction_feedback f ON f.correction_id = c.id "
            "GROUP BY c.model, c.prompt_version, c.type, f.verdict "
            "ORDER BY c.model, c.prompt_version, c.type, f.verdict"
        )
        return [dict(r) for r in rows]


class MetricsRepository:
    """Aggregated daily counters. Allowed metric names are an allow-list so
    nothing user-derived can ever become a metric key."""

    ALLOWED_PREFIXES = (
        "requests.",
        "source.",
        "provider_error.",
        "feedback.",
        "llm.calls",
        "tts.",
        "rate_limited",
    )

    def __init__(self, db: Database):
        self.db = db

    def incr(self, name: str, elapsed_ms: int = 0, day: str | None = None) -> None:
        if not name.startswith(self.ALLOWED_PREFIXES):
            raise ValueError(f"metric name not allowed: {name}")
        self.db.execute(
            "INSERT INTO metrics_daily(day, name, count, total_ms) VALUES (?, ?, 1, ?) "
            "ON CONFLICT(day, name) DO UPDATE SET count = count + 1, total_ms = total_ms + ?",
            (day or today(), name, elapsed_ms, elapsed_ms),
        )

    def count(self, name: str, day: str | None = None) -> int:
        rows = self.db.query(
            "SELECT count FROM metrics_daily WHERE day = ? AND name = ?", (day or today(), name)
        )
        return rows[0]["count"] if rows else 0

    def summary(self, days: int = 7) -> list[dict]:
        since = (dt.datetime.now(dt.UTC).date() - dt.timedelta(days=days - 1)).isoformat()
        rows = self.db.query(
            "SELECT name, SUM(count) AS count, SUM(total_ms) AS total_ms FROM metrics_daily "
            "WHERE day >= ? GROUP BY name ORDER BY name",
            (since,),
        )
        return [dict(r) for r in rows]


class PrivacyRepository:
    def __init__(self, db: Database):
        self.db = db

    def delete_user(self, user_id: int) -> dict[str, int]:
        counts = {}
        for table, column in (
            ("correction_feedback", "user_id"),
            ("corrections", "user_id"),
            ("saved_words", "user_id"),
            ("user_settings", "user_id"),
        ):
            rows = self.db.query(f"SELECT COUNT(*) AS n FROM {table} WHERE {column} = ?", (user_id,))
            counts[table] = rows[0]["n"]
        self.db.transaction(
            [
                ("DELETE FROM correction_feedback WHERE user_id = ?", (user_id,)),
                (
                    "DELETE FROM correction_feedback WHERE correction_id IN "
                    "(SELECT id FROM corrections WHERE user_id = ?)",
                    (user_id,),
                ),
                ("DELETE FROM corrections WHERE user_id = ?", (user_id,)),
                ("DELETE FROM saved_words WHERE user_id = ?", (user_id,)),
                ("DELETE FROM user_settings WHERE user_id = ?", (user_id,)),
            ]
        )
        return counts

    def purge_older_than(self, days: int) -> int:
        cutoff = f"-{int(days)} days"
        before = self.db.query("SELECT COUNT(*) AS n FROM corrections")[0]["n"]
        self.db.transaction(
            [
                (
                    "DELETE FROM correction_feedback WHERE correction_id IN "
                    "(SELECT id FROM corrections WHERE created_at < datetime('now', ?))",
                    (cutoff,),
                ),
                ("DELETE FROM corrections WHERE created_at < datetime('now', ?)", (cutoff,)),
                ("DELETE FROM saved_words WHERE created_at < datetime('now', ?)", (cutoff,)),
                ("DELETE FROM metrics_daily WHERE day < date('now', ?)", (cutoff,)),
            ]
        )
        after = self.db.query("SELECT COUNT(*) AS n FROM corrections")[0]["n"]
        return before - after


class AnalyticsRepository:
    """Per-action product analytics without personal data.

    * user_key: salted SHA-256 of the Telegram ID (the salt lives only in this
      database), so behaviour can be followed over time (retention, funnels)
      without storing who the user is;
    * events and their fields are allow-listed; message text is never stored.
    """

    EVENTS = frozenset(
        {
            "start",
            "help",
            "translate",
            "say",
            "explain",
            "check",
            "feedback",
            "tts",
            "mistakes",
            "direction",
            "delete_data",
            "rate_limited",
        }
    )
    DETAILS = frozenset(
        {
            "auto",
            "word",
            "phrase",
            "dictionary",
            "error",
            "not_error",
            "style",
            "changed",
            "no_errors",
            "has_errors",
            "unavailable",
            "budget",
            "button",
            "command",
        }
    )

    def __init__(self, db: Database):
        self.db = db
        rows = db.query("SELECT value FROM meta WHERE key = 'analytics_salt'")
        if rows:
            self._salt = rows[0]["value"]
        else:
            self._salt = secrets.token_hex(16)
            db.execute("INSERT INTO meta(key, value) VALUES ('analytics_salt', ?)", (self._salt,))

    def user_key(self, user_id: int) -> str:
        return hashlib.sha256(f"{self._salt}:{user_id}".encode()).hexdigest()[:16]

    def track(
        self,
        user_id: int,
        event: str,
        *,
        source: str | None = None,
        direction: str | None = None,
        ok: bool | None = None,
        latency_ms: int | None = None,
        chars: int | None = None,
        detail: str | None = None,
    ) -> None:
        if event not in self.EVENTS:
            raise ValueError(f"event not allowed: {event}")
        if detail is not None and detail not in self.DETAILS:
            raise ValueError(f"detail not allowed: {detail}")
        self.db.execute(
            "INSERT INTO events(user_key, event, source, direction, ok, latency_ms, chars, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.user_key(user_id),
                event,
                source,
                direction,
                None if ok is None else int(ok),
                latency_ms,
                chars,
                detail,
            ),
        )

    def delete_user(self, user_id: int) -> int:
        key = self.user_key(user_id)
        n = self.db.query("SELECT COUNT(*) AS n FROM events WHERE user_key = ?", (key,))[0]["n"]
        self.db.execute("DELETE FROM events WHERE user_key = ?", (key,))
        return n

    def purge_older_than(self, days: int) -> None:
        self.db.execute("DELETE FROM events WHERE ts < datetime('now', ?)", (f"-{int(days)} days",))

    # ------------------------------------------------------------- reports
    def report(self, days: int = 7) -> dict:
        since = f"-{int(days)} days"
        q = self.db.query
        users = q("SELECT COUNT(DISTINCT user_key) AS n FROM events WHERE ts >= datetime('now', ?)", (since,))
        new_users = q(
            "SELECT COUNT(*) AS n FROM (SELECT user_key, MIN(ts) AS first FROM events GROUP BY user_key) "
            "WHERE first >= datetime('now', ?)",
            (since,),
        )
        returning = q(
            "SELECT COUNT(*) AS n FROM (SELECT user_key FROM events WHERE ts >= datetime('now', ?) "
            "GROUP BY user_key HAVING COUNT(DISTINCT date(ts)) >= 2)",
            (since,),
        )
        daily = q(
            "SELECT date(ts) AS day, COUNT(DISTINCT user_key) AS users, COUNT(*) AS events FROM events "
            "WHERE ts >= datetime('now', ?) GROUP BY day ORDER BY day",
            (since,),
        )
        by_event = q(
            "SELECT event, COALESCE(detail, '') AS detail, COUNT(*) AS n, "
            "SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END) AS failed, CAST(AVG(latency_ms) AS INTEGER) AS avg_ms "
            "FROM events WHERE ts >= datetime('now', ?) GROUP BY event, detail ORDER BY n DESC",
            (since,),
        )
        sources = q(
            "SELECT source, COUNT(*) AS n FROM events WHERE event = 'translate' AND source IS NOT NULL "
            "AND ts >= datetime('now', ?) GROUP BY source ORDER BY n DESC",
            (since,),
        )
        feedback = q(
            "SELECT detail, COUNT(*) AS n FROM events WHERE event = 'feedback' "
            "AND ts >= datetime('now', ?) GROUP BY detail",
            (since,),
        )
        return {
            "days": days,
            "users": users[0]["n"],
            "new_users": new_users[0]["n"],
            "returning_users": returning[0]["n"],
            "daily": [dict(r) for r in daily],
            "by_event": [dict(r) for r in by_event],
            "sources": [dict(r) for r in sources],
            "feedback": {r["detail"]: r["n"] for r in feedback},
        }

    def export_rows(self, days: int = 30) -> list[dict]:
        rows = self.db.query(
            "SELECT ts, user_key, event, detail, source, direction, ok, latency_ms, chars FROM events "
            "WHERE ts >= datetime('now', ?) ORDER BY ts",
            (f"-{int(days)} days",),
        )
        return [dict(r) for r in rows]
