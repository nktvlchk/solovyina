"""Text-to-speech on demand (button press only).

gTTS sends the text to Google's TTS endpoint — mentioned in /privacy.
This generates audio; it does NOT assess the user's pronunciation.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import tempfile
from collections.abc import AsyncIterator, Callable
from pathlib import Path

logger = logging.getLogger(__name__)


class TtsError(Exception):
    pass


def _gtts_save(text: str, path: str) -> None:
    from gtts import gTTS

    gTTS(text=text, lang="uk", slow=False).save(path)


class TtsService:
    def __init__(self, max_length: int, synth: Callable[[str, str], None] = _gtts_save):
        self.max_length = max_length
        self._synth = synth

    @contextlib.asynccontextmanager
    async def synthesize(self, text: str) -> AsyncIterator[Path]:
        """Yields a path to a unique temporary MP3 that is always deleted."""
        text = text.strip()
        if not text:
            raise TtsError("empty text")
        if len(text) > self.max_length:
            raise TtsError("text too long")
        fd, name = tempfile.mkstemp(prefix="tts-", suffix=".mp3")
        os.close(fd)
        path = Path(name)
        try:
            try:
                await asyncio.wait_for(asyncio.to_thread(self._synth, text, name), timeout=20)
            except Exception as exc:
                logger.warning("tts failed: %s", type(exc).__name__)
                raise TtsError("synthesis failed") from None
            yield path
        finally:
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
