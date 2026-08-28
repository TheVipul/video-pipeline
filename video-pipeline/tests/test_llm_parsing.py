"""LLM output parsing.

Every case here is a real failure observed against MiniMax during
development, not a hypothetical. Structured output from an LLM is the least
trustworthy input in the pipeline, so it gets the most tests.
"""
from __future__ import annotations

import pytest

from pipeline.ai_analyzer import _balanced_json, _clean_title, _extract_json, _strip_reasoning


class TestStripReasoning:
    def test_removes_think_block(self):
        assert _strip_reasoning("<think>musing</think>{\"a\":1}") == '{"a":1}'

    def test_removes_unclosed_think_block(self):
        # A truncated response leaves <think> open; everything after is scratch.
        assert _strip_reasoning("<think>never closes {\"a\":1}") == ""

    def test_leaves_normal_text_alone(self):
        assert _strip_reasoning('{"a": 1}') == '{"a": 1}'


class TestBalancedJson:
    def test_stops_at_first_complete_object(self):
        assert _balanced_json('{"a":1}{"b":2}') == '{"a":1}'

    def test_ignores_braces_inside_strings(self):
        raw = '{"title": "a { weird } title"}'
        assert _balanced_json(raw) == raw

    def test_ignores_escaped_quotes(self):
        raw = '{"title": "he said \\"hi\\""}'
        assert _balanced_json(raw) == raw

    def test_returns_none_when_no_object(self):
        assert _balanced_json("no json here") is None


class TestExtractJson:
    def test_think_block_decoy_does_not_win(self):
        """Regression: naive first-brace/last-brace picked up the model's
        scratch work instead of its actual answer."""
        raw = '<think>maybe {"title": "WRONG"}</think>\n{"title": "RIGHT"}'
        assert _extract_json(raw)["title"] == "RIGHT"

    def test_fenced_json(self):
        assert _extract_json('```json\n{"title": "Fenced"}\n```')["title"] == "Fenced"

    def test_trailing_prose_after_object(self):
        assert _extract_json('{"title": "Bare"} Hope this helps!')["title"] == "Bare"

    def test_repairs_unescaped_inner_quote(self):
        """json.loads rejects this; json_repair recovers it."""
        raw = '{"title": "He said "hi" loudly", "tags": []}'
        assert "title" in _extract_json(raw)

    def test_raises_with_context_when_truly_absent(self):
        with pytest.raises(ValueError, match="No JSON object"):
            _extract_json("I could not complete that request.")

    def test_recovers_truncated_fenced_block(self):
        """Regression: the model hit max_tokens mid-object, so there was no
        closing brace and no closing fence. The whole enrichment was discarded
        even though every field needed had already arrived."""
        raw = '```json\n{\n  "title": "Nanoplastics and Human Health",\n  "relevance": 0.9'
        parsed = _extract_json(raw)
        assert parsed["title"] == "Nanoplastics and Human Health"
        assert parsed["relevance"] == 0.9

    def test_recovers_truncated_bare_object(self):
        parsed = _extract_json('{"title": "Half a title", "description": "cut off mid')
        assert parsed["title"] == "Half a title"


class TestCleanTitle:
    @pytest.mark.parametrize("token", ["reject", "review", "safe", "REJECT", " Reject "])
    def test_verdict_tokens_fall_back_to_original(self, token):
        """Regression: a video was published literally titled 'reject' because
        the model wrote its verdict into the title field."""
        assert _clean_title(token, "Real Original Title") == "Real Original Title"

    def test_empty_falls_back(self):
        assert _clean_title("", "Original") == "Original"
        assert _clean_title(None, "Original") == "Original"

    def test_real_title_passes_through(self):
        assert _clean_title("Aerial Beach Footage", "Original") == "Aerial Beach Footage"

    def test_truncates_to_200(self):
        assert len(_clean_title("x" * 500, "Original")) == 200
