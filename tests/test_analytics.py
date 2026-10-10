import csv
import io

import pytest

from bot.handlers import admin, feedback
from bot.handlers import translation as th
from tests.conftest import FakeLLM, make_context, make_update


def test_user_key_is_pseudonymous_and_stable(make_services):
    svc = make_services()
    a, b = svc.analytics.user_key(424242), svc.analytics.user_key(424242)
    assert a == b and "424242" not in a and len(a) == 16
    assert svc.analytics.user_key(1) != a


def test_only_allowlisted_events_and_details(make_services):
    svc = make_services()
    with pytest.raises(ValueError):
        svc.analytics.track(1, "message_text")
    with pytest.raises(ValueError):
        svc.analytics.track(1, "translate", detail="привіт світ")


def test_no_text_columns(make_services):
    svc = make_services()
    cols = {r["name"] for r in svc.db.query("PRAGMA table_info(events)")}
    assert not {"text", "message", "user_id", "username"} & cols


async def test_translation_is_tracked_without_text(make_services):
    llm = FakeLLM(auto='{"source": "ru", "translation": "Надішли фразу"}')
    svc = make_services(llm=llm, ANTHROPIC_API_KEY="k")
    await th.on_text(make_update("отправь фразу", user_id=5), make_context(svc))
    rows = svc.analytics.export_rows()
    assert rows[0]["event"] == "translate" and rows[0]["detail"] == "auto"
    assert rows[0]["source"] == "llm" and rows[0]["direction"] == "ru-uk" and rows[0]["chars"] == 13
    assert "отправь" not in str(rows)


async def test_feedback_is_tracked(make_services):
    import json

    resp = json.dumps(
        {
            "corrected_text": "у магазин",
            "corrections": [
                {
                    "original": "в",
                    "replacement": "у",
                    "type": "grammar",
                    "severity": "error",
                    "explanation": "x",
                    "confidence": 0.9,
                }
            ],
        }
    )
    svc = make_services(llm=FakeLLM(check=resp), ANTHROPIC_API_KEY="k")
    result = await svc.correction.check(1, "в магазин")
    await feedback.on_feedback_callback(
        make_update(callback=f"fb:{result.corrections[0].id}:n"), make_context(svc)
    )
    assert svc.analytics.report()["feedback"] == {"not_error": 1}


async def test_delete_my_data_removes_events(make_services):
    from bot.handlers import common

    svc = make_services()
    svc.analytics.track(1, "start")
    svc.analytics.track(2, "start")
    await common.on_delete_callback(make_update(callback="del:yes", user_id=1), make_context(svc))
    assert svc.analytics.report()["users"] == 1


async def test_export_is_admin_only_and_pseudonymous(make_services):
    svc = make_services(ADMIN_IDS="999")
    svc.analytics.track(424242, "start")
    denied = make_update("/export_stats", user_id=1)
    denied.effective_message.reply_document = None
    await admin.cmd_export_stats(denied, make_context(svc))
    assert "лише адміністратору" in denied.effective_message.sent[-1]["text"]

    sent = {}

    async def reply_document(document, caption):
        sent["data"] = document.input_file_content.decode()

    update = make_update("/export_stats", user_id=999)
    update.effective_message.reply_document = reply_document
    await admin.cmd_export_stats(update, make_context(svc))
    rows = list(csv.DictReader(io.StringIO(sent["data"])))
    assert rows[0]["event"] == "start" and "424242" not in sent["data"]
