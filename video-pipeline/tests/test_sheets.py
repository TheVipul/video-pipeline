"""Google Sheets operator interface.

The Sheets API itself is not exercised here - these cover the logic that
decides what to read, what to skip, and what to write back, which is where
the behaviour that matters to an operator actually lives.
"""
from __future__ import annotations

import pytest

from pipeline.sheets import (
    COL_URL,
    HEADERS,
    MAX_CELL_CHARS,
    SheetRow,
    _cell,
    extract_sheet_id,
)


class TestSheetIdExtraction:
    @pytest.mark.parametrize("value,expected", [
        ("https://docs.google.com/spreadsheets/d/1AbC-dEf_123/edit#gid=0", "1AbC-dEf_123"),
        ("https://docs.google.com/spreadsheets/d/1AbC-dEf_123/edit", "1AbC-dEf_123"),
        ("https://docs.google.com/spreadsheets/d/1AbC-dEf_123", "1AbC-dEf_123"),
        ("1AbC-dEf_123", "1AbC-dEf_123"),
        ("  1AbC-dEf_123  ", "1AbC-dEf_123"),
    ])
    def test_accepts_url_or_bare_id(self, value, expected):
        """Operators paste whatever is in their address bar."""
        assert extract_sheet_id(value) == expected

    def test_handles_empty(self):
        assert extract_sheet_id("") == ""
        assert extract_sheet_id(None) == ""


class TestRowSkipping:
    """Idempotency is per row: a row with a status has already been done."""

    def test_blank_status_is_pending(self):
        assert SheetRow(2, "https://youtu.be/x").is_pending

    def test_whitespace_only_status_is_pending(self):
        assert SheetRow(2, "https://youtu.be/x", status="   ").is_pending

    @pytest.mark.parametrize("status", ["Published", "Held - needs review", "Failed - download"])
    def test_any_status_marks_a_row_done(self, status):
        """Including failures - a re-run must not silently retry a video that
        already failed, or a bad URL loops forever at cost."""
        assert not SheetRow(2, "https://youtu.be/x", status=status).is_pending


class TestCellCoercion:
    def test_none_becomes_empty(self):
        assert _cell(None) == ""

    def test_numbers_become_strings(self):
        assert _cell(0.25) == "0.25"
        assert _cell(3) == "3"

    def test_long_values_are_truncated(self):
        """Sheets rejects oversized cells; an LLM summary can run long."""
        assert len(_cell("x" * 99_999)) == MAX_CELL_CHARS


class TestLayout:
    def test_url_is_column_a(self):
        """Column positions are the contract - operators rename headers."""
        assert COL_URL == 0

    def test_header_count_matches_written_range(self):
        # Writes target B..I (8 columns) plus A for the URL.
        assert len(HEADERS) == 9


class TestResultMapping:
    """run.py maps pipeline records back onto the rows they came from."""

    def _rows(self):
        return [
            SheetRow(2, "https://www.youtube.com/watch?v=aaa"),
            SheetRow(3, "https://www.youtube.com/watch?v=bbb"),
        ]

    def test_maps_records_to_row_numbers_by_url(self):
        from run import _sheet_results

        state = {"records": {
            "aaa": {
                "source_url": "https://www.youtube.com/watch?v=aaa",
                "status": "published",
                "enrichment": {"ai_title": "A", "summary": "S", "relevance": 1.0,
                               "cost_usd": 0.0031, "safety": {"concerns": []}},
                "publish": {"remote_path": "https://drive.google.com/file/d/x"},
            },
        }}
        results = _sheet_results(state, self._rows())

        assert results[2]["status"] == "Published"
        assert results[2]["title"] == "A"
        assert results[2]["link"].startswith("https://drive.google.com")
        # A row the run never reached must say so rather than look successful.
        assert results[3]["status"] == "Not processed"

    def test_internal_statuses_are_translated_for_humans(self):
        from run import _sheet_results

        state = {"records": {"aaa": {
            "source_url": "https://www.youtube.com/watch?v=aaa",
            "status": "held_for_review",
            "enrichment": {"safety": {"concerns": ["low brand relevance (0.10)"]}},
        }}}
        result = _sheet_results(state, self._rows())[2]

        assert result["status"] == "Held - needs review"
        # The reason is the most useful thing an operator can see.
        assert "relevance" in result["notes"]

    def test_download_failure_names_the_stage(self):
        from run import _sheet_results

        state = {"records": {"aaa": {
            "source_url": "https://www.youtube.com/watch?v=aaa",
            "status": "download_failed",
            "error": "blocked after 6 attempts",
        }}}
        result = _sheet_results(state, self._rows())[2]

        assert result["status"] == "Failed - download"
        assert "blocked" in result["notes"]


class TestWatcher:
    """The watcher turns the sheet into an event-driven trigger.

    It polls rather than receiving push notifications: Google delivers those
    to a public HTTPS endpoint, which a workstation is not. The observable
    behaviour is the same and the mechanism is far less fragile.
    """

    def test_poll_interval_is_responsive_but_quota_safe(self):
        from watch import DEFAULT_INTERVAL

        # Fast enough to feel instant to someone pasting a URL...
        assert DEFAULT_INTERVAL <= 15
        # ...and far inside Sheets' per-minute read quota.
        assert (60 / DEFAULT_INTERVAL) < 60

    def test_backoff_is_bounded(self):
        from watch import MAX_BACKOFF

        assert 60 <= MAX_BACKOFF <= 900

    def test_batches_run_as_subprocesses(self):
        """Isolation: a hung download or crash in one batch must not take the
        watcher down with it."""
        from pathlib import Path

        source = (Path(__file__).resolve().parent.parent / "watch.py").read_text()
        assert "subprocess.run" in source

    def test_watcher_only_picks_up_pending_rows(self):
        """It must reuse the same skip logic as a manual run, or a restart
        would reprocess and re-publish everything already done."""
        from pathlib import Path

        source = (Path(__file__).resolve().parent.parent / "watch.py").read_text()
        assert "read_rows()" in source
        assert "include_done" not in source.split("read_rows()")[1][:40]
