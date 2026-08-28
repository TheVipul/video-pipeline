"""
Brand safety + copyright heuristics.

Two layers:
    1. Hard keyword filter (rules-based, fast, no LLM needed) - reject obvious cases
    2. LLM-based classification (richer judgment) - safe/review/reject

The LLM layer is advisory when cost guard is active; rules layer is always enforced.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from logging_setup import get_logger

log = get_logger(__name__)

# Known high-risk keywords - these are coarse but catch the obvious cases.
HARD_REJECT_KEYWORDS = [
    r"\bnsfw\b",
    r"\bxxx\b",
    r"\bonlyfans\b",
    r"\bporn\b",
    r"\bhate\s*speech\b",
    r"\bgraphic\s*violence\b",
    r"\bterrorist\b",
    r"\bcsam\b",
]

# Well-known copyrighted brand names we never re-publish (avoid TOS/legal risk).
COPYRIGHT_HINTS = [
    r"\bmarvel\b",
    r"\bdisney\b",
    r"\bnetflix\b",
    r"\bnfl\b",
    r"\bnba\b",
    r"\bfifa\b",
    r"\bspotify\b",
    r"\bhbo\b",
    r"\bparamount\b",
    r"\bsony\s*pictures\b",
    r"\buniversal\s*pictures\b",
    r"\bwarner\s*bros\b",
]


@dataclass
class SafetyVerdict:
    verdict: str                # "safe" | "review" | "reject"
    confidence: float           # 0.0 - 1.0
    concerns: list[str]
    source: str                 # "rules" | "llm" | "merged"

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "confidence": round(self.confidence, 3),
            "concerns": self.concerns,
            "source": self.source,
        }


def rules_check(title: str, description: str, tags: list[str]) -> SafetyVerdict:
    """
    Fast, deterministic safety check. Returns "reject" on hard keyword match,
    "review" on copyright hint match, "safe" otherwise.
    """
    text = " ".join([title or "", description or "", " ".join(tags or [])]).lower()
    concerns: list[str] = []

    for pat in HARD_REJECT_KEYWORDS:
        if re.search(pat, text, re.IGNORECASE):
            concerns.append(f"hard_reject:{pat}")
            return SafetyVerdict(
                verdict="reject",
                confidence=0.95,
                concerns=concerns,
                source="rules",
            )

    for pat in COPYRIGHT_HINTS:
        if re.search(pat, text, re.IGNORECASE):
            concerns.append(f"copyright_hint:{pat}")

    if concerns:
        return SafetyVerdict(
            verdict="review",
            confidence=0.6,
            concerns=concerns,
            source="rules",
        )

    return SafetyVerdict(
        verdict="safe",
        confidence=0.7,
        concerns=[],
        source="rules",
    )


def merge_verdicts(rules: SafetyVerdict, llm: Optional[SafetyVerdict]) -> SafetyVerdict:
    """
    Combine rules-based and LLM verdicts. Rules take precedence on reject.
    """
    if llm is None:
        return rules

    # If rules say reject, honor it.
    if rules.verdict == "reject":
        return rules

    # If rules say review, escalate if LLM also says review or reject.
    if rules.verdict == "review" and llm.verdict in ("review", "reject"):
        merged_concerns = list(set(rules.concerns + llm.concerns))
        return SafetyVerdict(
            verdict=llm.verdict,
            confidence=min(0.95, max(rules.confidence, llm.confidence)),
            concerns=merged_concerns,
            source="merged",
        )

    # If LLM rejects, honor it (even if rules are quiet).
    if llm.verdict == "reject":
        return llm

    # If LLM says review, that wins over rules "safe".
    if llm.verdict == "review":
        return llm

    # Both safe
    return SafetyVerdict(
        verdict="safe",
        confidence=min(rules.confidence, llm.confidence),
        concerns=[],
        source="merged",
    )
