#!/usr/bin/env python3
"""Fail if a tracked file looks like it contains a real secret or runtime
user data. Prints only the file, line and secret type — never the value."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PATTERNS = {
    "Telegram bot token": re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_-]{30,}"),
    "Google API key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    "Anthropic API key": re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"),
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9]{32,}"),
    "Private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}
FORBIDDEN_FILES = re.compile(
    r"(^|/)(\.env|stats\.json|favorites\.json)$|\.(db|sqlite3?|log)$|(^|/)__pycache__/"
)


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [f for f in out.splitlines() if f]


def main() -> int:
    problems = []
    for name in tracked_files():
        if FORBIDDEN_FILES.search(name):
            problems.append(f"{name}: file must not be committed")
            continue
        path = ROOT / name
        if not path.is_file() or path.stat().st_size > 5_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for kind, pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                problems.append(f"{name}:{line}: possible {kind}")
    for p in problems:
        print(p)
    if problems:
        print(f"{len(problems)} problem(s). Values are not printed.", file=sys.stderr)
        return 1
    print("no secrets found in tracked files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
