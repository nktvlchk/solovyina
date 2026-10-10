"""'Перевір мене': model-based check with a strict structured result."""

from __future__ import annotations

import logging
import time

from bot.models.correction import CheckResult, ParseError, parse_check_response
from bot.providers.anthropic import CHECK_PROMPT_VERSION
from bot.providers.base import ProviderError
from bot.services.limits import LlmBudget
from bot.storage.repositories import CorrectionsRepository, MetricsRepository

logger = logging.getLogger(__name__)


class CheckUnavailable(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason  # "disabled" | "budget" | "provider" | "parse"


class CorrectionService:
    def __init__(
        self,
        llm,
        budget: LlmBudget | None,
        corrections: CorrectionsRepository,
        metrics: MetricsRepository,
        store_raw_text: bool = False,
    ):
        self.llm = llm
        self.budget = budget
        self.corrections = corrections
        self.metrics = metrics
        self.store_raw_text = store_raw_text

    @property
    def enabled(self) -> bool:
        return self.llm is not None

    async def check(self, user_id: int, text: str) -> CheckResult:
        if self.llm is None or self.budget is None:
            raise CheckUnavailable("disabled")
        if not await self.budget.try_consume():
            raise CheckUnavailable("budget")
        started = time.monotonic()
        try:
            raw = await self.llm.check(text)
        except ProviderError:
            self.metrics.incr("provider_error.llm")
            raise CheckUnavailable("provider") from None
        self.metrics.incr("source.llm", int((time.monotonic() - started) * 1000))
        try:
            result = parse_check_response(raw, text)
        except ParseError as exc:
            logger.warning("check: model output rejected (%s)", exc)
            self.metrics.incr("provider_error.llm_parse")
            raise CheckUnavailable("parse") from None
        for correction in result.corrections:
            self.corrections.add(
                user_id,
                correction,
                model=self.llm.model,
                prompt_version=CHECK_PROMPT_VERSION,
                raw_text=text if self.store_raw_text else None,
            )
        return result
