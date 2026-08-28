"""
LLM cost circuit breaker.

Tracks estimated spend across an LLM client. Once we hit SAFETY_MAX_LLM_SPEND_USD,
the guard refuses to make further calls until the run restarts.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Optional

from config import SafetySettings
from logging_setup import get_logger

log = get_logger(__name__)

# USD per 1M tokens, as (input, output). Input and output are priced
# separately because the gap is large - MiniMax M2.7 charges 4x more for
# output than input - and a single blended rate is therefore wrong in both
# directions depending on the prompt/completion ratio. Metadata enrichment is
# input-heavy, so blending materially over-charged it.
#
# Source: provider pricing pages, verified 2026-08.
PRICE_PER_1M_TOKENS: dict[str, tuple[float, float]] = {
    # MiniMax
    "MiniMax-M2.7": (0.30, 1.20),
    "MiniMax-M2.7-highspeed": (0.60, 2.40),
    "MiniMax-M2.5": (0.30, 1.20),
    "MiniMax-M2": (0.30, 1.20),
    "MiniMax-M3": (0.30, 1.20),
    "MiniMax-Text-01": (1.00, 1.00),
    "abab6.5s-chat": (1.00, 1.00),
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    # Anthropic
    "claude-3-5-sonnet-20241022": (3.00, 15.00),
    "claude-3-5-haiku-20241022": (0.80, 4.00),
}

# Deliberately pessimistic: an unknown model must over-estimate, so the guard
# trips early rather than late. Failing closed is the right bias for a
# component whose job is to stop runaway spend.
DEFAULT_PRICE_PER_1M: tuple[float, float] = (2.00, 8.00)


@dataclass
class CostGuard:
    settings: SafetySettings
    model: str
    _spent: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _warned_unpriced: bool = False

    def _price_per_1m(self) -> tuple[float, float]:
        """(input, output) USD per 1M tokens for the configured model."""
        if self.model in PRICE_PER_1M_TOKENS:
            return PRICE_PER_1M_TOKENS[self.model]
        # Warn once: an unpriced model means every cost figure this run
        # reports is a guess, which matters if anyone quotes cost-per-video.
        if not self._warned_unpriced:
            self._warned_unpriced = True
            log.warning(
                "cost_model_not_in_price_table",
                model=self.model,
                using_default_input=DEFAULT_PRICE_PER_1M[0],
                using_default_output=DEFAULT_PRICE_PER_1M[1],
                note="costs are an over-estimate; add the model to PRICE_PER_1M_TOKENS",
            )
        return DEFAULT_PRICE_PER_1M

    def estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        price_in, price_out = self._price_per_1m()
        return (
            prompt_tokens * price_in + completion_tokens * price_out
        ) / 1_000_000

    def can_spend(self) -> bool:
        with self._lock:
            return self._spent < self.settings.max_llm_spend_usd

    def record(self, prompt_tokens: int, completion_tokens: int) -> float:
        cost = self.estimate_cost(prompt_tokens, completion_tokens)
        with self._lock:
            self._spent += cost
        log.info(
            "llm_cost_recorded",
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=round(cost, 6),
            total_spent_usd=round(self._spent, 6),
            budget_usd=self.settings.max_llm_spend_usd,
        )
        return cost

    @property
    def spent(self) -> float:
        with self._lock:
            return self._spent

    @property
    def budget_remaining(self) -> float:
        return max(0.0, self.settings.max_llm_spend_usd - self.spent)

    def assert_can_spend(self) -> None:
        if not self.can_spend():
            raise CostGuardExceeded(
                f"LLM cost budget exhausted: spent ${self.spent:.4f} / "
                f"${self.settings.max_llm_spend_usd:.2f}"
            )


class CostGuardExceeded(RuntimeError):
    pass
