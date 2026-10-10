from bot.handlers import translation as th
from tests.conftest import make_context, make_update


async def test_input_length_limit(make_services):
    svc = make_services(MAX_INPUT_LENGTH=20)
    update = make_update("а" * 21)
    await th.on_text(update, make_context(svc))
    assert "Задовгий текст" in update.effective_message.sent[-1]["text"]


async def test_rate_limit(make_services):
    svc = make_services(USER_RATE_LIMIT_PER_MINUTE=2)
    ctx = make_context(svc)
    for _ in range(3):
        update = make_update("кіт")
        await th.on_text(update, ctx)
    assert "Забагато запитів" in update.effective_message.sent[-1]["text"]


def test_rate_limiter_window():
    from bot.services.limits import RateLimiter

    now = [0.0]
    rl = RateLimiter(1, clock=lambda: now[0])
    assert rl.allow(1) and not rl.allow(1)
    now[0] = 61
    assert rl.allow(1)
