import json

from bot.handlers import feedback
from bot.models.correction import Verdict
from tests.conftest import FakeLLM, make_context, make_update

TEXT = "Я ходив в магазін."
RESP = json.dumps(
    {
        "corrected_text": "Я ходив у магазин.",
        "corrections": [
            {
                "original": "магазін",
                "replacement": "магазин",
                "type": "spelling",
                "severity": "error",
                "explanation": "и",
                "confidence": 0.9,
            },
            {
                "original": "в",
                "replacement": "у",
                "type": "grammar",
                "severity": "error",
                "explanation": "чергування",
                "confidence": 0.8,
            },
        ],
    },
    ensure_ascii=False,
)


async def _setup(make_services):
    svc = make_services(llm=FakeLLM(check=RESP), ANTHROPIC_API_KEY="k")
    result = await svc.correction.check(1, TEXT)
    return svc, result.corrections


async def test_vote_is_per_correction(make_services):
    svc, (c1, c2) = await _setup(make_services)
    update = make_update(callback=f"fb:{c1.id}:e")
    await feedback.on_feedback_callback(update, make_context(svc))
    assert svc.corrections.get_verdict(c1.id, 1) is Verdict.ERROR
    assert svc.corrections.get_verdict(c2.id, 1) is None
    assert "Це помилка" in update.callback_query.answers[-1]
    chosen = [b.text for row in update.callback_query.markups[-1].inline_keyboard for b in row]
    assert "● ✅ Це помилка" in chosen


async def test_revote_changes_without_duplicates(make_services):
    svc, (c1, _) = await _setup(make_services)
    ctx = make_context(svc)
    for code in ("e", "e", "n", "s"):
        await feedback.on_feedback_callback(make_update(callback=f"fb:{c1.id}:{code}"), ctx)
    rows = svc.db.query("SELECT verdict FROM correction_feedback WHERE correction_id = ?", (c1.id,))
    assert [r["verdict"] for r in rows] == ["style"]
    counts = {r["name"]: r["count"] for r in svc.metrics.summary()}
    assert counts["feedback.error"] == 1  # second identical press did not count


async def test_only_author_can_vote(make_services):
    svc, (c1, _) = await _setup(make_services)
    update = make_update(user_id=2, callback=f"fb:{c1.id}:e")
    await feedback.on_feedback_callback(update, make_context(svc))
    assert svc.corrections.get_verdict(c1.id, 2) is None
    assert "лише автор" in update.callback_query.answers[-1]


async def test_malformed_callback_ignored(make_services):
    svc, _ = await _setup(make_services)
    update = make_update(callback="fb:../../etc:x")
    await feedback.on_feedback_callback(update, make_context(svc))
    assert svc.db.query("SELECT COUNT(*) AS n FROM correction_feedback")[0]["n"] == 0


async def test_confirmed_errors_query(make_services):
    svc, (c1, c2) = await _setup(make_services)
    svc.corrections.set_verdict(c1.id, 1, Verdict.ERROR)
    svc.corrections.set_verdict(c2.id, 1, Verdict.NOT_ERROR)
    confirmed = svc.corrections.confirmed_for_user(1)
    assert [c.id for c in confirmed] == [c1.id]
    quality = svc.corrections.quality_summary()
    assert {q["verdict"] for q in quality} == {"error", "not_error"}
