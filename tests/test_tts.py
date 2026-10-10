import glob
import os
import tempfile

import pytest

from bot.handlers import translation as th
from bot.services.tts import TtsError, TtsService
from tests.conftest import make_context, make_update


def _fake_synth(text, path):
    with open(path, "wb") as fh:
        fh.write(b"ID3fake")


def _failing_synth(text, path):
    raise OSError("network")


def _tts_files():
    return set(glob.glob(os.path.join(tempfile.gettempdir(), "tts-*.mp3")))


async def test_temp_file_removed_after_success():
    before = _tts_files()
    service = TtsService(100, synth=_fake_synth)
    async with service.synthesize("привіт") as path:
        assert path.exists()
    assert not path.exists()
    assert _tts_files() == before


async def test_temp_file_removed_after_failure():
    before = _tts_files()
    service = TtsService(100, synth=_failing_synth)
    with pytest.raises(TtsError):
        async with service.synthesize("привіт"):
            pass
    assert _tts_files() == before


async def test_temp_file_removed_if_sending_fails():
    service = TtsService(100, synth=_fake_synth)
    with pytest.raises(RuntimeError):
        async with service.synthesize("привіт") as path:
            raise RuntimeError("telegram down")
    assert not path.exists()


async def test_length_limit():
    with pytest.raises(TtsError):
        async with TtsService(5, synth=_fake_synth).synthesize("задовгий текст"):
            pass


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
