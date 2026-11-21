"""🐞 Помилка: report a wrong answer, comment or cancel, admin review."""

import csv
import io
from types import SimpleNamespace

from bot.handlers import bugs, common
from bot.handlers import translation as th
from tests.conftest import make_context, make_update

ANSWER = "кіт\n\n• кот\nДжерело: локальний словник"


def _report_button(message):
    rows = message.sent[-1]["reply_markup"].inline_keyboard
    return next(b for row in rows for b in row if b.callback_data.startswith("bug:"))


async def _press_report(svc, ctx, *, user_id=1, message_id=None):
    """Translate "кіт", then press 🐞 under the answer."""
    update = make_update("кіт", user_id=user_id)
    await th.on_text(update, ctx)
    press = make_update(user_id=user_id, callback=_report_button(update.effective_message).callback_data)
    press.callback_query.message.text = ANSWER
    press.callback_query.message.message_id = message_id
    await bugs.on_report_callback(press, ctx)
    return press


def _reports(svc):
    return svc.db.query("SELECT * FROM bug_reports ORDER BY id")


async def test_button_is_on_its_own_row_after_existing_ones(make_services):
    svc = make_services()
    update = make_update("кіт")
    await th.on_text(update, make_context(svc))
    rows = update.effective_message.sent[-1]["reply_markup"].inline_keyboard
    assert rows[0][0].callback_data.startswith("tts:")  # old buttons keep their place
    assert [b.text for b in rows[-1]] == ["🐞 Помилка"]
    assert len(rows[-1][0].callback_data.encode()) <= 64


async def test_press_saves_report_and_asks_what_is_wrong(make_services):
    svc = make_services()
    ctx = make_context(svc)
    press = await _press_report(svc, ctx)
    (row,) = _reports(svc)
    assert row["feature"] == "translate" and row["source"] == "dictionary"
    assert row["user_text"] == "кіт" and "кот" in row["bot_text"]
    assert row["comment"] == "" and row["status"] == "open"
    assert press.callback_query.answers[-1] == "🐞 Записав"
    prompt = press.callback_query.message.sent[-1]
    assert "Що саме не так" in prompt["text"]
    labels = [b.text for b in prompt["reply_markup"].inline_keyboard[0]]
    assert labels == ["✅ Без коментаря", "↩️ Скасувати"]


async def test_next_message_becomes_comment_not_translation(make_services):
    svc = make_services()
    ctx = make_context(svc)
    await _press_report(svc, ctx)
    comment = make_update("наголос на перший склад")
    await th.on_text(comment, ctx)
    assert _reports(svc)[0]["comment"] == "наголос на перший склад"
    assert "Записав коментар" in comment.effective_message.sent[-1]["text"]
    # The step is over: the following message is translated as usual.
    after = make_update("кіт")
    await th.on_text(after, ctx)
    assert "кот" in after.effective_message.sent[-1]["text"]
    assert _reports(svc)[0]["comment"] == "наголос на перший склад"


async def test_cancel_deletes_the_report(make_services):
    svc = make_services()
    ctx = make_context(svc)
    press = await _press_report(svc, ctx)
    report_id = _reports(svc)[0]["id"]
    cancel = make_update(callback=f"bugx:{report_id}")
    await bugs.on_prompt_callback(cancel, ctx)
    assert _reports(svc) == []
    assert "Скасовано" in cancel.callback_query.edited_text[-1]
    assert bugs.PENDING_KEY not in ctx.user_data
    # The next message is a normal request again.
    after = make_update("кіт")
    await th.on_text(after, ctx)
    assert "кот" in after.effective_message.sent[-1]["text"]
    assert press.callback_query.answers[-1] == "🐞 Записав"


async def test_skip_keeps_report_without_comment(make_services):
    svc = make_services()
    ctx = make_context(svc)
    await _press_report(svc, ctx)
    report_id = _reports(svc)[0]["id"]
    skip = make_update(callback=f"bugs:{report_id}")
    await bugs.on_prompt_callback(skip, ctx)
    (row,) = _reports(svc)
    assert row["comment"] == ""
    assert "без коментаря" in skip.callback_query.edited_text[-1]
    assert bugs.PENDING_KEY not in ctx.user_data


async def test_only_author_can_cancel(make_services):
    svc = make_services()
    ctx = make_context(svc)
    await _press_report(svc, ctx)
    report_id = _reports(svc)[0]["id"]
    stranger = make_update(user_id=2, callback=f"bugx:{report_id}")
    await bugs.on_prompt_callback(stranger, make_context(svc))
    assert len(_reports(svc)) == 1
    assert "не ваш" in stranger.callback_query.answers[-1]


async def test_forgotten_prompt_expires(make_services):
    svc = make_services()
    ctx = make_context(svc)
    await _press_report(svc, ctx)
    ctx.user_data[bugs.PENDING_KEY]["at"] -= bugs.PENDING_TTL_SECONDS + 1
    later = make_update("кіт")
    await th.on_text(later, ctx)
    assert "кот" in later.effective_message.sent[-1]["text"]
    assert _reports(svc)[0]["comment"] == ""


async def test_mode_button_ends_comment_step(make_services):
    svc = make_services()
    ctx = make_context(svc)
    await _press_report(svc, ctx)
    await th.on_text(make_update("🗂 Мої помилки"), ctx)
    assert bugs.PENDING_KEY not in ctx.user_data
    assert len(_reports(svc)) == 1


async def test_double_press_on_same_answer_is_one_report(make_services):
    svc = make_services()
    ctx = make_context(svc)
    first = await _press_report(svc, ctx, message_id=77)
    again = make_update(callback=first.callback_query.data)
    again.callback_query.message.text = ANSWER
    again.callback_query.message.message_id = 77
    await bugs.on_report_callback(again, ctx)
    assert len(_reports(svc)) == 1


async def test_works_after_restart_when_cache_is_empty(make_services):
    svc = make_services()
    press = make_update(callback="bug:stale-key")
    press.callback_query.message.text = ANSWER
    await bugs.on_report_callback(press, make_context(svc))
    (row,) = _reports(svc)
    assert row["user_text"] == "" and "кот" in row["bot_text"]


async def test_no_button_in_groups(make_services):
    svc = make_services()
    update = make_update("/tr кіт")
    update.effective_chat = SimpleNamespace(type="group", id=-100)
    assert bugs.report_button(svc, update, "translate", "кіт") is None


async def test_delete_my_data_and_retention_remove_reports(make_services):
    svc = make_services()
    ctx = make_context(svc)
    await _press_report(svc, ctx, user_id=1)
    await _press_report(svc, make_context(svc), user_id=2)
    await common.on_delete_callback(make_update(callback="del:yes", user_id=1), make_context(svc))
    assert [r["user_id"] for r in _reports(svc)] == [2]
    svc.db.execute("UPDATE bug_reports SET created_at = datetime('now', '-400 days')")
    svc.privacy.purge_older_than(180)
    assert _reports(svc) == []


# ----------------------------------------------------------------- admin


async def test_bugs_list_is_admin_only(make_services):
    svc = make_services(ADMIN_IDS="999")
    update = make_update("/bugs", user_id=1)
    await bugs.cmd_bugs(update, make_context(svc))
    assert "лише адміністратору" in update.effective_message.sent[-1]["text"]


async def test_admin_reviews_and_resolves(make_services):
    svc = make_services(ADMIN_IDS="999")
    ctx = make_context(svc)
    await _press_report(svc, ctx)
    await th.on_text(make_update("має бути <кішка>"), ctx)
    report_id = _reports(svc)[0]["id"]

    listing = make_update("/bugs", user_id=999)
    await bugs.cmd_bugs(listing, make_context(svc))
    text = listing.effective_message.sent[-1]["text"]
    assert f"#{report_id}" in text and "кіт" in text and "переклад" in text
    assert "має бути &lt;кішка&gt;" in text  # escaped HTML
    assert f"/done_{report_id}" in text and "відкритих: 1" in text

    done = make_update(f"/done_{report_id}", user_id=999)
    await bugs.on_status_command(done, make_context(svc))
    assert "розібрано" in done.effective_message.sent[-1]["text"]
    assert _reports(svc)[0]["status"] == "done"

    empty = make_update("/bugs", user_id=999)
    await bugs.cmd_bugs(empty, make_context(svc))
    assert "немає" in empty.effective_message.sent[-1]["text"]

    archive = make_update("/bugs done", user_id=999)
    await bugs.cmd_bugs(archive, make_context(svc, args=["done"]))
    assert f"/reopen_{report_id}" in archive.effective_message.sent[-1]["text"]


async def test_status_command_ignored_for_non_admin(make_services):
    svc = make_services(ADMIN_IDS="999")
    ctx = make_context(svc)
    await _press_report(svc, ctx)
    report_id = _reports(svc)[0]["id"]
    update = make_update(f"/done_{report_id}", user_id=1)
    await bugs.on_status_command(update, make_context(svc))
    assert _reports(svc)[0]["status"] == "open"
    assert update.effective_message.sent == []


async def test_export_has_texts_but_no_telegram_ids(make_services):
    svc = make_services(ADMIN_IDS="999")
    await _press_report(svc, make_context(svc), user_id=424242)
    update = make_update("/export_bugs", user_id=999)
    sent = {}

    async def reply_document(document, caption):
        sent["data"] = document.input_file_content
        sent["caption"] = caption

    update.effective_message.reply_document = reply_document
    await bugs.cmd_export_bugs(update, make_context(svc))
    content = sent["data"].decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(content)))
    assert rows[0]["user_text"] == "кіт" and rows[0]["feature"] == "translate"
    assert "424242" not in content and "user_id" not in rows[0]
    assert "Звітів: 1" in sent["caption"]


async def test_stats_shows_open_reports(make_services):
    from bot.handlers import admin

    svc = make_services(ADMIN_IDS="999")
    await _press_report(svc, make_context(svc))
    update = make_update("/stats", user_id=999)
    await admin.cmd_stats(update, make_context(svc))
    assert "відкритих 1" in update.effective_message.sent[-1]["text"]


async def test_flow_is_tracked_without_text(make_services):
    svc = make_services()
    ctx = make_context(svc)
    await _press_report(svc, ctx)
    await th.on_text(make_update("наголос"), ctx)
    rows = svc.db.query("SELECT detail FROM events WHERE event = 'bug_report' ORDER BY id")
    assert [r["detail"] for r in rows] == ["reported", "commented"]
    assert svc.metrics.count("feedback.bug") == 1
    stored = " ".join(str(v) for r in svc.db.query("SELECT * FROM events") for v in dict(r).values())
    assert "наголос" not in stored


async def test_check_without_errors_can_be_reported(make_services):
    """A wrong "no errors found" can be reported too: the summary carries 🐞."""
    from bot.handlers import correction as ch
    from tests.conftest import FakeLLM

    text = "Я ходив в магазин."
    resp = '{"corrected_text": "Я ходив в магазин.", "corrections": []}'
    svc = make_services(llm=FakeLLM(check=resp), ANTHROPIC_API_KEY="k")
    update = make_update()
    await ch.run_check(update, make_context(svc), text)
    summary = update.effective_message.sent[-1]
    assert "Помилок не знайдено" in summary["text"]
    button = summary["reply_markup"].inline_keyboard[-1][0]
    assert button.text == "🐞 Помилка"
    press = make_update(callback=button.callback_data)
    press.callback_query.message.text = "Помилок не знайдено."
    await bugs.on_report_callback(press, make_context(svc))
    (row,) = _reports(svc)
    assert row["feature"] == "check" and row["user_text"] == text
