"""Shared fixtures.

Everything here is offline. The suite must run on a laptop with no network,
no API key and no YouTube access, because a test suite that needs credentials
is a test suite that stops being run.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.ai_analyzer import AIEnrichment  # noqa: E402
from safety.content_safety import SafetyVerdict  # noqa: E402


@pytest.fixture
def safe_verdict() -> SafetyVerdict:
    return SafetyVerdict(verdict="safe", confidence=0.9, concerns=[], source="rules")


@pytest.fixture
def enrichment(safe_verdict) -> AIEnrichment:
    return AIEnrichment(
        video_id="testvid123",
        ai_title="A Test Video",
        ai_description="Description of a test video.",
        ai_tags=["one", "two"],
        ai_category="general",
        safety=safe_verdict,
    )
