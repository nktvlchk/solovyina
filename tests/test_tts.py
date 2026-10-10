import glob
import os
import tempfile
import time

import pytest

from bot.handlers import translation as th
from bot.services.tts import TtsError, TtsService
from tests.conftest import make_context, make_update


def _fake_synth(text):
    return b"ID3fake"


def _failing_synth(text):
    raise OSError("network")


def _slow_synth(text):
    time.sleep(0.3)
    return b"late"


def _tmp_audio():
    tmp = tempfile.gettempdir()
    return set(glob.glob(os.path.join(tmp, "*.mp3")) + glob.glob(os.path.join(tmp, "tts-*")))


async def test_audio_in_memory_no_temp_files():
    before = _tmp_audio()
    assert await TtsService(100, synth=_fake_synth).synthesize("привіт") == b"ID3fake"
    assert _tmp_audio() == before


async def test_failure_leaves_nothing():
    before = _tmp_audio()
    with pytest.raises(TtsError):
        await TtsService(100, synth=_failing_synth).synthesize("привіт")
    assert _tmp_audio() == before


async def test_timeout_is_handled():
    with pytest.raises(TtsError):
        await TtsService(100, synth=_slow_synth, timeout=0.05).synthesize("привіт")


async def test_length_limit_and_empty():
    with pytest.raises(TtsError):
        await TtsService(5, synth=_fake_synth).synthesize("задовгий текст")
    with pytest.raises(TtsError):
        await TtsService(5, synth=_fake_synth).synthesize("   ")


async def test_tts_only_on_button_press(make_services):
    svc = make_services()
    svc.tts = TtsService(100, synth=_fake_synth)
    update = make_update("кіт")
    await th.on_text(update, make_context(svc))
    message = update.effective_message
    assert message.voices == []  # nothing generated automatically
    button = message.sent[-1]["reply_markup"].inline_keyboard[0][0]
    press = make_update(callback=button.callback_data)
    await th.on_tts_callback(press, make_context(svc))
    assert press.effective_message.voices[0]["data"] == b"ID3fake"


async def test_tts_error_is_friendly(make_services):
    svc = make_services()
    svc.tts = TtsService(100, synth=_failing_synth)
    key = svc.text_cache.put("привіт")
    press = make_update(callback=f"tts:{key}")
    await th.on_tts_callback(press, make_context(svc))
    text = press.effective_message.sent[-1]["text"]
    assert "Не вдалося озвучити" in text and "OSError" not in text


def test_button_keys_are_not_guessable(make_services):
    svc = make_services()
    keys = {svc.text_cache.put("x") for _ in range(50)}
    assert len(keys) == 50
    assert "1" not in keys and all(len(k) >= 8 for k in keys)
