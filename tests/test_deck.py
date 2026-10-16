"""Class-deck cards: validation, schedule position, gist update, and a silent chat."""

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest

from bot.config import ConfigError
from bot.handlers import ingest, translation
from bot.services.deck import DeckEditor, DeckError, GistDeck, insertion_index, load_cards, replace_cards
from bot.services.lesson_cards import LessonRejected, looks_like_lesson, only_new, parse_lesson
from bot.storage.repositories import DeckBatchRepository
from tests.conftest import FakeLLM, make_context, make_settings, make_update

START = date(2026, 10, 8)
LESSON = json.dumps(
    {
        "cards": [
            {
                "type": "rule",
                "title": "Разом, окремо, дефіс",
                "body": "Прислівники пишемо разом, окремо або через дефіс.",
                "wrong": "по новому",
                "right": "по-новому",
                "tag": "правопис",
            },
            {
                "type": "vocab",
                "title": "Частота",
                "tag": "частота",
                "items": [
                    {"word": "завжди", "note": "увесь час"},
                    {"word": "зазвичай", "note": "у більшості випадків"},
                    {"word": "часто", "note": "багато разів"},
                    {"word": "іноді", "note": "час від часу"},
                    {"word": "ніколи", "note": "жодного разу"},
                ],
            },
        ]
    },
    ensure_ascii=False,
)
DECK = """# header
<!-- CARDS_START -->

---

<!-- R01 -->
🧠 Перше

текст першої картки

---

<!-- V01 -->
📚 Друге

1. а — б

<!-- CARDS_END -->
"""
MARKER = "УРОКМАРКЕР"
LESSON_TEXT = "Сьогодні на уроці розібрали правопис прислівників " + MARKER


class MemoryDeck:
    def __init__(self, text: str):
        self.text = text
        self.writes = 0

    async def read(self) -> str:
        return self.text

    async def write(self, text: str) -> None:
        self.text = text
        self.writes += 1


class FakeBot:
    def __init__(self):
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text))


class FakeFile:
    def __init__(self, data: bytes, file_size: int | None = 4):
        self.file_size = file_size
        self._data = data

    async def get_file(self):
        return self

    async def download_as_bytearray(self):
        return bytearray(self._data)


def _editor(svc, text=DECK):
    memory = MemoryDeck(text)
    editor = DeckEditor(memory, DeckBatchRepository(svc.db), START, ZoneInfo("Europe/Lisbon"))
    svc.deck = editor
    return memory


def test_parse_lesson_accepts_a_rule_and_five_words():
    drafts = parse_lesson(LESSON)
    assert drafts[0].title.startswith("Разом")
    assert len(drafts[1].items) == 5


def test_parse_lesson_rejects_russian_bad_counts_and_half_examples():
    russian = LESSON.replace("завжди", "обычно")
    with pytest.raises(LessonRejected):
        parse_lesson(russian)
    one = json.loads(LESSON)
    one["cards"] = [one["cards"][1]]
    one["cards"][0]["items"] = one["cards"][0]["items"][:1]
    assert len(parse_lesson(json.dumps(one, ensure_ascii=False))[0].items) == 1
    too_many = json.loads(LESSON)
    too_many["cards"][1]["items"] = too_many["cards"][1]["items"] + [{"word": "рідко", "note": "нечасто"}]
    with pytest.raises(LessonRejected):
        parse_lesson(json.dumps(too_many, ensure_ascii=False))
    half = json.loads(LESSON)
    half["cards"][0]["right"] = ""
    with pytest.raises(LessonRejected):
        parse_lesson(json.dumps(half, ensure_ascii=False))
    assert parse_lesson('{"cards": []}') == ()


def test_insertion_keeps_cards_that_already_went_out():
    assert insertion_index(START, date(2026, 10, 8), 20) == 1
    assert insertion_index(START, date(2026, 10, 16), 20) == 9
    assert insertion_index(START, date(2026, 10, 16), 4) == 4


def test_example_deck_roundtrip_keeps_ids_and_header():
    original = Path("docs/deck.example.md").read_text(encoding="utf-8")
    cards = load_cards(original)
    assert [card.id for card in cards] == ["R01", "V01"]
    rewritten = replace_cards(original, cards)
    assert rewritten.startswith("# Example deck")
    again = load_cards(rewritten)
    assert [card.id for card in again] == ["R01", "V01"]
    assert "Кличний" in again[0].text and "🧠 R01" in again[0].text


async def test_add_puts_new_cards_on_the_next_day_and_undo_removes_only_them(make_services):
    svc = make_services()
    memory = _editor(svc)
    added = await svc.deck.add(parse_lesson(LESSON), today=date(2026, 10, 8))
    cards = load_cards(memory.text)
    assert [card.id for card in cards] == ["R01", "R02", "V02", "V01"]
    assert added.index == 1 and added.first_day == date(2026, 10, 9)
    assert "🧠 Разом, окремо, дефіс" in cards[1].text
    assert "#правило #правопис" in cards[1].text
    assert "5. ніколи" in cards[2].text
    assert "R02" not in cards[0].text
    assert svc.deck.batches.get() == ["R02", "V02"]
    removed = await svc.deck.undo()
    assert removed == ("R02", "V02")
    assert [card.id for card in load_cards(memory.text)] == ["R01", "V01"]
    assert svc.deck.batches.get() == []
    assert memory.writes == 2


async def test_bad_draft_does_not_write(make_services):
    memory = _editor(make_services())
    with pytest.raises(LessonRejected):
        parse_lesson("не картки")
    assert memory.writes == 0


async def test_gist_read_write_and_truncated_file():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["Authorization"]
        if request.method == "GET":
            return httpx.Response(200, json={"files": {"deck.md": {"content": DECK, "truncated": False}}})
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        store = GistDeck("test-token", "ab" * 16, client=client)
        assert (await store.read()).startswith("# header")
        await store.write("оновлена дека")
    assert seen["auth"] == "Bearer test-token"
    assert seen["body"]["files"]["deck.md"]["content"] == "оновлена дека"

    def truncated(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"files": {"deck.md": {"content": "x", "truncated": True}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(truncated)) as client:
        store = GistDeck("test-token", "ab" * 16, client=client)
        with pytest.raises(DeckError):
            await store.read()


def test_deck_settings_are_optional_and_paired():
    assert make_settings().deck_gist_id == ""
    assert make_settings().teacher_ids == frozenset()
    ready = make_settings(GITHUB_TOKEN="test-token", DECK_GIST_ID="ab" * 16, TEACHER_IDS="7, 8")
    assert ready.teacher_ids == {7, 8}
    with pytest.raises(ConfigError):
        make_settings(GITHUB_TOKEN="test-token")
    with pytest.raises(ConfigError):
        make_settings(GITHUB_TOKEN="test-token", DECK_GIST_ID="not a gist")
    with pytest.raises(ConfigError):
        make_settings(DRIP_TZ="Not/AZone")


def _group(text: str, user_id: int, chat_id: int = -100):
    update = make_update(text, user_id=user_id)
    update.effective_chat = SimpleNamespace(id=chat_id, type="supergroup")
    return update


def _services(make_services, lesson=LESSON):
    llm = FakeLLM(lesson=lesson)
    svc = make_services(
        llm=llm,
        ANTHROPIC_API_KEY="k",
        ADMIN_IDS="5",
        TEACHER_IDS="7",
        DRIP_CHAT_ID="-100",
    )
    memory = _editor(svc)
    return svc, llm, memory


async def test_teacher_message_updates_the_deck_and_stays_out_of_the_chat(make_services):
    svc, llm, memory = _services(make_services)
    bot = FakeBot()
    ctx = make_context(svc)
    ctx.bot = bot
    update = _group(LESSON_TEXT, user_id=7)
    await ingest.on_class_message(update, ctx)
    assert update.effective_message.sent == []
    assert memory.writes == 1
    assert bot.messages and bot.messages[0][0] == 5
    notice = bot.messages[0][1]
    today = datetime.now(ZoneInfo("Europe/Lisbon")).date()
    first = START + timedelta(days=insertion_index(START, today, 2))
    assert "R02" in notice and first.strftime("%d.%m") in notice and MARKER not in notice
    assert llm.seen_lesson == LESSON_TEXT
    blob = _dump(svc)
    assert MARKER not in blob and "Разом, окремо" not in blob
    assert svc.analytics.export_rows()[-1]["event"] == "deck"
    assert MARKER not in str(svc.analytics.export_rows())


def test_only_words_and_new_rules_are_kept():
    assert looks_like_lesson("блакитний")
    assert looks_like_lesson("надвечір'я, світанок")
    assert not looks_like_lesson("привіт!")
    assert not looks_like_lesson("дякую")
    drafts = parse_lesson(LESSON)
    kept = only_new(["🧠 Разом, окремо, дефіс\n\nвже є\n\n#правило", "1. завжди — увесь час"], drafts)
    assert [draft.title for draft in kept] == ["Частота"]
    assert [word for word, _note in kept[0].items] == ["зазвичай", "часто", "іноді", "ніколи"]


async def test_a_single_word_is_taken_and_chat_with_nothing_to_learn_is_silent(make_services):
    word = json.dumps(
        {
            "cards": [
                {
                    "type": "vocab",
                    "title": "Колір",
                    "tag": "колір",
                    "items": [{"word": "блакитний", "note": "світлий синій"}],
                }
            ]
        },
        ensure_ascii=False,
    )
    svc, llm, memory = _services(make_services, lesson=word)
    ctx = make_context(svc)
    ctx.bot = FakeBot()
    await ingest.on_class_message(_group("блакитний", 7), ctx)
    assert llm.calls == ["lesson"] and memory.writes == 1
    assert any("блакитний" in card.text for card in load_cards(memory.text))

    llm._lesson = '{"cards": []}'
    await ingest.on_class_message(_group("Хто зробив домашнє завдання на завтра?", 7), ctx)
    assert memory.writes == 1 and ctx.bot.messages[-1][1].count("Додав") == 1
    assert ctx.bot.messages[-1][0] == 5


async def test_a_word_already_in_the_deck_is_not_added_again(make_services):
    known = json.dumps(
        {
            "cards": [
                {
                    "type": "vocab",
                    "title": "Літера",
                    "tag": "літера",
                    "items": [{"word": "а", "note": "перша літера"}],
                }
            ]
        },
        ensure_ascii=False,
    )
    svc, _llm, memory = _services(make_services, lesson=known)
    ctx = make_context(svc)
    ctx.bot = FakeBot()
    await ingest.on_class_message(_group("а", 7), ctx)
    assert memory.writes == 0 and ctx.bot.messages == []


async def test_chatter_other_people_and_other_chats_are_ignored(make_services):
    svc, llm, memory = _services(make_services)
    ctx = make_context(svc)
    ctx.bot = FakeBot()
    await ingest.on_class_message(_group("привіт", 7), ctx)
    await ingest.on_class_message(_group(LESSON_TEXT, 9), ctx)
    await ingest.on_class_message(_group(LESSON_TEXT, 7, chat_id=-200), ctx)
    assert llm.calls == [] and memory.writes == 0 and ctx.bot.messages == []


async def test_unusable_model_output_does_not_touch_the_deck(make_services):
    svc, llm, memory = _services(make_services, lesson="немає карток")
    ctx = make_context(svc)
    ctx.bot = FakeBot()
    await ingest.on_class_message(_group(LESSON_TEXT, 7), ctx)
    assert memory.writes == 0
    assert "не змінював" in ctx.bot.messages[-1][1]
    assert llm.calls == ["lesson"]


async def test_class_photo_is_not_answered_in_the_chat(make_services):
    svc, llm, memory = _services(make_services)
    ctx = make_context(svc)
    ctx.bot = FakeBot()
    update = _group("", 7)
    update.effective_message.photo = [FakeFile(b"jpeg-bytes")]
    update.effective_message.caption = MARKER
    await ingest.on_class_message(update, ctx)
    assert update.effective_message.sent == []
    assert llm.calls == ["lesson-media"] and llm.seen_caption == MARKER
    assert memory.writes == 1
    assert MARKER not in _dump(svc)


async def test_learn_deck_and_undo_are_admin_only(make_services):
    svc, _llm, memory = _services(make_services)
    stranger = make_update("/learn текст уроку про правопис прислівників сьогодні", user_id=1)
    await ingest.cmd_learn(stranger, make_context(svc))
    assert "адміністратору" in stranger.effective_message.sent[-1]["text"]
    assert memory.writes == 0

    admin = make_update("/learn " + LESSON_TEXT, user_id=5)
    await ingest.cmd_learn(admin, make_context(svc))
    assert "R02" in admin.effective_message.sent[-1]["text"]
    assert MARKER not in admin.effective_message.sent[-1]["text"]

    status = make_update("/deck", user_id=5)
    await ingest.cmd_deck(status, make_context(svc))
    report = status.effective_message.sent[-1]["text"]
    assert "У деці: 4" in report and "R02" in report

    undo = make_update("/deck_undo", user_id=5)
    await ingest.cmd_deck_undo(undo, make_context(svc))
    assert "R02" in undo.effective_message.sent[-1]["text"]
    assert [card.id for card in load_cards(memory.text)] == ["R01", "V01"]


async def test_learn_mode_takes_the_next_message_and_a_button_cancels_it(make_services):
    svc, llm, memory = _services(make_services)
    ctx = make_context(svc)
    await ingest.cmd_learn(make_update("/learn", user_id=5), ctx)
    assert ctx.user_data[ingest.LEARN_KEY] is True
    follow = make_update(LESSON_TEXT, user_id=5)
    await translation.on_text(follow, ctx)
    assert memory.writes == 1 and ingest.LEARN_KEY not in ctx.user_data
    assert llm.calls == ["lesson"]

    await ingest.cmd_learn(make_update("/learn", user_id=5), ctx)
    button = make_update("💬 Як сказати?", user_id=5)
    await translation.on_text(button, ctx)
    assert ingest.LEARN_KEY not in ctx.user_data
    assert "Надішліть фразу" in button.effective_message.sent[-1]["text"]


async def test_missing_deck_tells_the_admin_and_stays_quiet_in_the_class(make_services):
    svc = make_services(llm=FakeLLM(lesson=LESSON), ANTHROPIC_API_KEY="k", ADMIN_IDS="5", TEACHER_IDS="7")
    assert svc.deck is None
    ctx = make_context(svc)
    ctx.bot = FakeBot()
    update = _group(LESSON_TEXT, 7)
    await ingest.on_class_message(update, ctx)
    assert update.effective_message.sent == [] and ctx.bot.messages == []
    private = make_update("/learn " + LESSON_TEXT, user_id=5)
    await ingest.cmd_learn(private, make_context(svc))
    assert "вимкнено" in private.effective_message.sent[-1]["text"]


def _dump(svc) -> str:
    parts = []
    for table in svc.db.query("SELECT name FROM sqlite_master WHERE type = 'table'"):
        rows = svc.db.query(f"SELECT * FROM {table['name']}")
        parts.append(str([dict(row) for row in rows]))
    return "\n".join(parts)
