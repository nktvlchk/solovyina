"""Text-to-speech on demand (button press only).

gTTS sends the text to Google's TTS endpoint — mentioned in /privacy.
This generates audio; it does NOT assess the user's pronunciation.

Audio is produced in memory (no temporary files), so nothing is left on
disk even if synthesis times out while the worker thread is still running.
"""

from __future__ import annotations

import asyncio
import io
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


class TtsError(Exception):
    pass


def _gtts_bytes(text: str) -> bytes:
    from gtts import gTTS

    buffer = io.BytesIO()
    gTTS(text=text, lang="uk", slow=False).write_to_fp(buffer)
    return buffer.getvalue()


class TtsService:
    def __init__(self, max_length: int, synth: Callable[[str], bytes] = _gtts_bytes, timeout: float = 20):
        self.max_length = max_length
        self._synth = synth
        self._timeout = timeout

    async def synthesize(self, text: str) -> bytes:
        text = text.strip()
        if not text:
            raise TtsError("empty text")
        if len(text) > self.max_length:
            raise TtsError("text too long")
        try:
            audio = await asyncio.wait_for(asyncio.to_thread(self._synth, text), timeout=self._timeout)
        except Exception as exc:
            logger.warning("tts failed: %s", type(exc).__name__)
            raise TtsError("synthesis failed") from None
        if not audio:
            raise TtsError("empty audio")
        return audio
