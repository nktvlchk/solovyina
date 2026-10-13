"""Scheduling for the personal review deck.

A small SM-2-style rule, not full Anki FSRS. Four grades, day-sized steps:

* again — back to "due now", ease down, progress reset
* hard  — a short step, ease down slightly
* good  — 1 day, then 6, then interval × ease
* easy  — longer than good, ease up

Ease stays between 1.3 and 3.0.
"""

from __future__ import annotations

from dataclasses import dataclass

ASK_FIX = "fix"
ASK_SAY = "say"
ASK_MEAN = "mean"
ASKS = frozenset({ASK_FIX, ASK_SAY, ASK_MEAN})

GRADE_AGAIN = 1
GRADE_HARD = 2
GRADE_GOOD = 3
GRADE_EASY = 4
GRADES = frozenset({GRADE_AGAIN, GRADE_HARD, GRADE_GOOD, GRADE_EASY})

GRADE_NAMES = {
    GRADE_AGAIN: "again",
    GRADE_HARD: "hard",
    GRADE_GOOD: "good",
    GRADE_EASY: "easy",
}

EASE_MIN = 1.3
EASE_MAX = 3.0
EASE_DEFAULT = 2.5


@dataclass(frozen=True)
class SrsState:
    ease: float = EASE_DEFAULT
    interval_days: int = 0
    reps: int = 0
    lapses: int = 0


def _ease(value: float) -> float:
    return round(min(EASE_MAX, max(EASE_MIN, value)), 2)


def _days(value: float) -> int:
    """Round half up to a whole number of days, at least one."""
    return max(1, int(value + 0.5))


def good_interval(state: SrsState) -> int:
    """Interval a Good press would assign, without changing ease."""
    if state.reps <= 0:
        return 1
    if state.reps == 1:
        return 6
    return _days(state.interval_days * state.ease)


def apply_grade(state: SrsState, grade: int) -> SrsState:
    if grade not in GRADES:
        raise ValueError(f"unknown grade: {grade}")
    if grade == GRADE_AGAIN:
        lapses = state.lapses + (1 if state.interval_days > 0 or state.reps > 0 else 0)
        return SrsState(_ease(state.ease - 0.2), 0, 0, lapses)
    if grade == GRADE_HARD:
        interval = 1 if state.interval_days <= 0 else _days(state.interval_days * 1.2)
        return SrsState(_ease(state.ease - 0.15), interval, state.reps, state.lapses)
    if grade == GRADE_GOOD:
        return SrsState(state.ease, good_interval(state), state.reps + 1, state.lapses)
    easy_interval = 4 if state.reps <= 0 and state.interval_days <= 0 else _days(good_interval(state) * 1.3)
    return SrsState(_ease(state.ease + 0.15), easy_interval, state.reps + 1, state.lapses)
