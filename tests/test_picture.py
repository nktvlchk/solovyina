"""One private-chat photo: the model reads it, and the file is not stored."""

import json

import pytest

from bot.handlers import picture
from bot.models.picture import PictureParseError, parse_picture
from bot.services.translation import PictureUnavailable
from tests.conftest import FakeLLM, make_context, make_update

READING = json.dumps(
    {
        "summary": "Таблиця прислівників частоти.",
        "items": [
            {"word": "Завжди", "note": "Увесь час, без винятку."},
            {"word": "Ніколи", "note": "Жодного разу."},
        ],
    },
    ensure_ascii=False,
)
SECRET = "СЕКРЕТНИЙПІДПИС"


class FakePhoto:
    def __init__(self, data: bytes = b"\xff\xd8jpeg", file_size: int | None = 4):
        self.file_size = file_size
        self._data = data

    async def get_file(self):
        return self

    async def download_as_bytearray(self):
        return bytearray(self._data)


def _photo_update(
    caption: str = "", *, data: bytes = b"\xff\xd8jpeg", file_size: int | None = 4, group: str = ""
):
    update = make_update()
    message = update.effective_message
    message.photo = [FakePhoto(data, file_size)]
    message.caption = caption
    message.media_group_id = group or None
    return update


def test_parse_picture_keeps_ukrainian_items_and_rejects_russian():
    reading = parse_picture(READING)
    assert reading.summary.startswith("Таблиця")
    assert reading.items[0].word == "Завжди"
    assert parse_picture('{"summary": "Немає напису.", "items": []}').items == ()
    with pytest.raises(PictureParseError):
        parse_picture('{"summary": "ы", "items": []}')


async def test_photo_is_explained_and_not_stored(make_services):
    llm = FakeLLM(image=READING)
    svc = make_services(llm=llm, ANTHROPIC_API_KEY="k")
    update = _photo_update(SECRET, data=b"UNIQUEIMAGEBYTES")
    await picture.on_photo(update, make_context(svc))
    text = update.effective_message.sent[-1]["text"]
    assert "Завжди" in text and "Ніколи" in text and "може помилятися" in text
    assert "<b>Завжди</b>" in text
    assert llm.seen_image == b"UNIQUEIMAGEBYTES"
    assert llm.seen_caption == SECRET
    assert llm.calls == ["image"]
    assert svc.metrics.count("requests.photo") == 1
    exported = str(svc.analytics.export_rows())
    assert "photo" in exported and SECRET not in exported and "Завжди" not in exported
    for table in svc.db.query("SELECT name FROM sqlite_master WHERE type = 'table'"):
        rows = svc.db.query(f"SELECT * FROM {table['name']}")
        blob = str([dict(row) for row in rows])
        assert SECRET not in blob and "UNIQUEIMAGEBYTES" not in blob and "Завжди" not in blob


async def test_photo_escapes_model_text(make_services):
    raw = json.dumps(
        {"summary": "Напис <b>", "items": [{"word": "кіт", "note": "a < b"}]},
        ensure_ascii=False,
    )
    svc = make_services(llm=FakeLLM(image=raw), ANTHROPIC_API_KEY="k")
    update = _photo_update()
    await picture.on_photo(update, make_context(svc))
    text = update.effective_message.sent[-1]["text"]
    assert "Напис &lt;b&gt;" in text and "a &lt; b" in text
    assert "Напис <b>" not in text


async def test_photo_without_model_is_refused(make_services):
    svc = make_services()
    update = _photo_update()
    await picture.on_photo(update, make_context(svc))
    assert "вимкнен" in update.effective_message.sent[-1]["text"]
    assert svc.metrics.count("llm.calls") == 0


async def test_photo_budget_and_bad_reading(make_services):
    svc = make_services(llm=FakeLLM(image=READING), ANTHROPIC_API_KEY="k", LLM_DAILY_LIMIT=0)
    update = _photo_update()
    await picture.on_photo(update, make_context(svc))
    assert "ліміт" in update.effective_message.edited[-1]

    svc = make_services(llm=FakeLLM(image="не json"), ANTHROPIC_API_KEY="k")
    update = _photo_update()
    await picture.on_photo(update, make_context(svc))
    assert "Не вдалося" in update.effective_message.edited[-1]
    assert svc.metrics.count("provider_error.llm_parse") == 1


async def test_oversized_photo_is_not_sent_to_the_model(make_services):
    llm = FakeLLM(image=READING)
    svc = make_services(llm=llm, ANTHROPIC_API_KEY="k")
    update = _photo_update(file_size=picture.MAX_IMAGE_BYTES + 1)
    await picture.on_photo(update, make_context(svc))
    assert "завелике" in update.effective_message.sent[-1]["text"].lower()
    assert llm.calls == []


async def test_album_is_answered_once(make_services):
    llm = FakeLLM(image=READING)
    svc = make_services(llm=llm, ANTHROPIC_API_KEY="k")
    ctx = make_context(svc)
    first = _photo_update(group="album-1")
    second = _photo_update(group="album-1")
    await picture.on_photo(first, ctx)
    await picture.on_photo(second, ctx)
    assert "одне фото" in first.effective_message.sent[-1]["text"]
    assert second.effective_message.sent == []
    assert llm.calls == []


async def test_provider_failure(make_services):
    svc = make_services(llm=FakeLLM(image=READING, fail=True), ANTHROPIC_API_KEY="k")
    update = _photo_update()
    await picture.on_photo(update, make_context(svc))
    assert "недоступний" in update.effective_message.edited[-1]


async def test_privacy_mentions_photos(make_services):
    from bot.handlers import common

    update = make_update("/privacy")
    await common.cmd_privacy(update, make_context(make_services()))
    text = update.effective_message.sent[-1]["text"]
    assert "фото" in text and "фото і підписи" in text


async def test_describe_image_requires_the_model(make_services):
    svc = make_services()
    with pytest.raises(PictureUnavailable) as caught:
        await svc.translation.describe_image(b"jpeg", "image/jpeg", "", 1)
    assert caught.value.reason == "disabled"
