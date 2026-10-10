"""Structured result of a text check and its strict validation."""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass, field
from enum import StrEnum

MAX_CORRECTIONS = 3


class CorrectionType(StrEnum):
    SPELLING = "spelling"
    GRAMMAR = "grammar"
    CALQUE = "calque"  # кальки з російської
    PUNCTUATION = "punctuation"
    VOCABULARY = "vocabulary"
    STYLE = "style"


class Severity(StrEnum):
    ERROR = "error"  # objective norm violation
    SUGGESTION = "suggestion"  # stylistic recommendation, not a mistake


class Verdict(StrEnum):
    """User feedback on one correction."""

    ERROR = "error"  # ✅ Це помилка
    NOT_ERROR = "not_error"  # ❌ Тут немає помилки
    STYLE = "style"  # ✍️ Лише стиль


class ParseError(ValueError):
    """Model output does not match the schema. Never shown to the user raw."""


@dataclass(frozen=True)
class Correction:
    id: str
    original: str
    replacement: str
    type: CorrectionType
    severity: Severity
    explanation: str
    confidence: float | None = None


@dataclass(frozen=True)
class CheckResult:
    corrected_text: str
    corrections: tuple[Correction, ...] = field(default_factory=tuple)

    @property
    def has_errors(self) -> bool:
        return any(c.severity is Severity.ERROR for c in self.corrections)


def new_short_id() -> str:
    """8 url-safe chars: fits easily in Telegram callback_data (64 bytes)."""
    return secrets.token_urlsafe(6)


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_check_response(raw: str, source_text: str) -> CheckResult:
    """Validate model JSON. Drops corrections that are not grounded in the
    user's text or that change nothing; caps the number of explanations."""
    if not raw or not raw.strip():
        raise ParseError("empty response")
    cleaned = _FENCE_RE.sub("", raw.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ParseError("no JSON object")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ParseError("invalid JSON") from exc
    if not isinstance(data, dict):
        raise ParseError("top level is not an object")

    corrected = data.get("corrected_text")
    items = data.get("corrections", [])
    if not isinstance(corrected, str) or not corrected.strip():
        raise ParseError("corrected_text missing")
    if not isinstance(items, list):
        raise ParseError("corrections is not a list")

    corrections: list[Correction] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        original = str(item.get("original", "")).strip()
        replacement = str(item.get("replacement", "")).strip()
        explanation = str(item.get("explanation", "")).strip()
        if not original or original == replacement or not explanation:
            continue  # not a real correction
        if original.lower() not in source_text.lower():
            continue  # hallucinated fragment: not in the user's text
        try:
            ctype = CorrectionType(str(item.get("type", "grammar")).lower())
        except ValueError:
            ctype = CorrectionType.GRAMMAR
        try:
            severity = Severity(str(item.get("severity", "error")).lower())
        except ValueError:
            severity = Severity.SUGGESTION
        if ctype is CorrectionType.STYLE:
            severity = Severity.SUGGESTION  # style is never an objective error
        confidence = item.get("confidence")
        if not isinstance(confidence, int | float) or isinstance(confidence, bool):
            confidence = None
        else:
            confidence = max(0.0, min(1.0, float(confidence)))
        key = (original.lower(), replacement.lower())
        if key in seen:
            continue
        seen.add(key)
        corrections.append(
            Correction(
                id=new_short_id(),
                original=original[:200],
                replacement=replacement[:200],
                type=ctype,
                severity=severity,
                explanation=explanation[:400],
                confidence=confidence,
            )
        )
    # Errors first, then suggestions; keep only the most important ones.
    corrections.sort(key=lambda c: (c.severity is not Severity.ERROR, -(c.confidence or 0)))
    corrections = corrections[:MAX_CORRECTIONS]
    corrected_text = corrected.strip() if corrections else source_text.strip()
    return CheckResult(corrected_text=corrected_text[:4000], corrections=tuple(corrections))
