import json

import pytest

from bot.handlers import admin, common
from tests.conftest import FakeLLM, make_context, make_update

RESP = json.dumps(
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


async def test_stats_denied_for_regular_user(make_services):
    svc = make_services(ADMIN_IDS="999")
    update = make_update("/stats", user_id=1)
    await admin.cmd_stats(update, make_context(svc))
    assert "лише адміністратору" in update.effective_message.sent[-1]["text"]


async def test_stats_without_admin_ids_denied_for_everyone(make_services):
    svc = make_services()
    update = make_update("/stats", user_id=1)
    await admin.cmd_stats(update, make_context(svc))
    assert "лише адміністратору" in update.effective_message.sent[-1]["text"]


async def test_admin_stats_are_aggregated_only(make_services):
    svc = make_services(ADMIN_IDS="999", llm=FakeLLM(check=RESP), ANTHROPIC_API_KEY="k")
    await svc.correction.check(424242, "в магазин")
    svc.metrics.incr("requests.word", 120)
    update = make_update("/stats", user_id=999)
    await admin.cmd_stats(update, make_context(svc))
    text = update.effective_message.sent[-1]["text"]
    assert "requests.word" in text
    assert "424242" not in text and "магазин" not in text


def test_metrics_reject_user_derived_names(make_services):
    svc = make_services()
    with pytest.raises(ValueError):
        svc.metrics.incr("word:привіт")


def test_no_identity_columns_in_schema(make_services):
    svc = make_services()
    columns = set()
    for table in ("user_settings", "saved_words", "corrections", "correction_feedback", "metrics_daily"):
        columns |= {r["name"] for r in svc.db.query(f"PRAGMA table_info({table})")}
    assert not {"username", "first_name", "last_name", "message", "full_text"} & columns


async def test_delete_my_data(make_services):
    svc = make_services(llm=FakeLLM(check=RESP), ANTHROPIC_API_KEY="k")
    result = await svc.correction.check(1, "в магазин")
    from bot.models.correction import Verdict

    svc.corrections.set_verdict(result.corrections[0].id, 1, Verdict.ERROR)
    svc.saved_words.save(1, "кіт", "кот")
    await svc.correction.check(2, "в магазин")  # another user stays

    update = make_update(callback="del:yes", user_id=1)
    ctx = make_context(svc, user_data={"pending_text": "x"})
    await common.on_delete_callback(update, ctx)
    for table in ("corrections", "correction_feedback", "saved_words", "user_settings"):
        assert svc.db.query(f"SELECT COUNT(*) AS n FROM {table} WHERE user_id = 1")[0]["n"] == 0
    assert svc.db.query("SELECT COUNT(*) AS n FROM corrections WHERE user_id = 2")[0]["n"] == 1
    assert ctx.user_data == {}
    assert "Видалено" in update.callback_query.edited_text[-1]


async def test_delete_cancel_keeps_data(make_services):
    svc = make_services()
    svc.saved_words.save(1, "кіт", "кот")
    await common.on_delete_callback(make_update(callback="del:no"), make_context(svc))
    assert svc.saved_words.list(1)


def test_retention_purge(make_services):
    svc = make_services()
    svc.db.execute(
        "INSERT INTO corrections(id, user_id, original, replacement, type, severity, explanation, "
        "model, prompt_version, created_at) VALUES ('old', 1, 'a', 'b', 'grammar', 'error', 'x', "
        "'m', 'v', datetime('now', '-400 days'))"
    )
    assert svc.privacy.purge_older_than(180) == 1


async def test_privacy_command_lists_services(make_services):
    svc = make_services()
    update = make_update("/privacy")
    await common.cmd_privacy(update, make_context(svc))
    text = update.effective_message.sent[-1]["text"]
    for name in ("Wiktionary", "MyMemory", "Anthropic", "Google TTS", "/delete_my_data"):
        assert name in text
