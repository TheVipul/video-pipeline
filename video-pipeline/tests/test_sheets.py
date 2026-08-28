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
            SheetRow(2, "https://www.youtube.com/watch?v=aaaaaaaaaaa"),
            SheetRow(3, "https://www.youtube.com/watch?v=bbbbbbbbbbb"),
        ]

    def test_maps_records_to_row_numbers_by_url(self):
        from run import _sheet_results

        state = {"records": {
            "aaa": {
                "source_url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
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
            "source_url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
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
            "source_url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
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


class TestInvalidRowFeedback:
    """A row must never be left blank.

    Regression: pasting a page title instead of a URL produced no pipeline
    record, so nothing was written back. The row stayed blank, which reads as
    "still pending" - and a watcher re-reads pending rows every few seconds,
    so one bad paste became an endless retry loop with no visible cause.
    """

    def _state(self):
        return {"records": {}}

    def test_non_url_gets_a_clear_status(self):
        from pipeline.sheets import SheetRow
        from run import _sheet_results

        row = SheetRow(2, "Artificial intelligence explained - YouTube")
        result = _sheet_results(self._state(), [row])[2]

        assert result["status"] == "Invalid link"
        # The note must tell the operator how to fix it, not just that it broke.
        assert "address bar" in result["notes"]

    def test_valid_but_unreached_url_is_distinguished(self):
        """A row the run simply did not get to is a different situation from a
        row that can never work, and must not be reported the same way."""
        from pipeline.sheets import SheetRow
        from run import _sheet_results

        row = SheetRow(2, "https://www.youtube.com/watch?v=jNQXAC9IVRw")
        result = _sheet_results(self._state(), [row])[2]

        assert result["status"] == "Not processed"
        assert "Invalid" not in result["status"]

    def test_every_row_receives_a_status(self):
        """The invariant that stops the retry loop."""
        from pipeline.sheets import SheetRow
        from run import _sheet_results

        rows = [
            SheetRow(2, "not a url at all"),
            SheetRow(3, "https://www.youtube.com/watch?v=jNQXAC9IVRw"),
            SheetRow(4, "ftp://example.com/video.mp4"),
        ]
        results = _sheet_results(self._state(), rows)

        assert set(results) == {2, 3, 4}
        for r in results.values():
            assert r["status"].strip(), "a blank status would loop forever"


class TestAuthFailureHandling:
    """Authorisation problems must stop the watcher, not be retried.

    Regression: a token issued for Drive only was reused by the watcher, which
    also needs Sheets. Every refresh failed with 'invalid_scope', and the
    generic retry loop backed off to five minutes and kept going - so the
    watcher looked alive in the terminal while the sheet silently stopped
    updating for hours.
    """

    @pytest.mark.parametrize("message", [
        "('invalid_scope: Bad Request', {'error': 'invalid_scope'})",
        "invalid_grant: Token has been expired or revoked.",
        "invalid_client: Unauthorized",
        "Request had insufficient authentication scopes",
    ])
    def test_auth_errors_are_recognised(self, message):
        from watch import _is_auth_error

        assert _is_auth_error(Exception(message))

    @pytest.mark.parametrize("message", [
        "Connection reset by peer",
        "503 Service Unavailable",
        "timed out",
    ])
    def test_transient_errors_are_not_treated_as_auth(self, message):
        """These should still back off and retry - stopping on a network blip
        would be worse than retrying."""
        from watch import _is_auth_error

        assert not _is_auth_error(Exception(message))

    def test_google_auth_error_always_counts(self):
        from pipeline.google_auth import GoogleAuthError
        from watch import _is_auth_error

        assert _is_auth_error(GoogleAuthError("consent required"))


class TestScopeValidation:
    """A cached token granted narrower access than requested must be detected
    before use, not at refresh time where it surfaces as 'invalid_scope'."""

    def test_missing_scope_is_detected(self, tmp_path):
        import json

        from pipeline.google_auth import DRIVE_SCOPE, SHEETS_SCOPE, load_credentials

        # A token granted Drive only, as `run.py --publisher gdrive` creates.
        token = tmp_path / "token.json"
        token.write_text(json.dumps({
            "token": "x", "refresh_token": "y",
            "client_id": "c", "client_secret": "s",
            "scopes": [DRIVE_SCOPE],
        }))

        # Asking for Sheets as well must not silently reuse it.
        with pytest.raises(Exception) as excinfo:
            load_credentials(
                [DRIVE_SCOPE, SHEETS_SCOPE],
                credentials_file=tmp_path / "absent.json",
                token_file=token,
                allow_interactive=False,
            )
        assert "authoris" in str(excinfo.value).lower()

    def test_non_interactive_refuses_to_open_a_browser(self, tmp_path):
        """In a background process, consent would block forever on a browser
        nobody sees. Fail with an instruction instead."""
        from pipeline.google_auth import DRIVE_SCOPE, GoogleAuthError, load_credentials

        with pytest.raises(GoogleAuthError) as excinfo:
            load_credentials(
                [DRIVE_SCOPE],
                credentials_file=tmp_path / "absent.json",
                token_file=tmp_path / "absent_token.json",
                allow_interactive=False,
            )
        assert "publisher gdrive" in str(excinfo.value)


class TestSmartChips:
    """Sheets converts a pasted link into a smart chip by default.

    Regression: a chip displays the page *title*, and the plain values API
    returns that title rather than the link. So a correctly pasted YouTube URL
    arrived as "Some Video - YouTube" and was rejected as invalid. The real
    URL lives in the cell's chip metadata.
    """

    def test_chip_url_is_preferred_over_visible_title(self):
        from pipeline.sheets import _cell_url

        cell = {
            "formattedValue": "Artificial intelligence explained - YouTube",
            "chipRuns": [{"chip": {"richLinkProperties": {
                "uri": "https://www.youtube.com/watch?v=UdE-W30oOXo"}}}],
        }
        assert _cell_url(cell) == "https://www.youtube.com/watch?v=UdE-W30oOXo"

    def test_plain_hyperlink_is_used_when_no_chip(self):
        from pipeline.sheets import _cell_url

        cell = {"formattedValue": "click here",
                "hyperlink": "https://youtu.be/jNQXAC9IVRw"}
        assert _cell_url(cell) == "https://youtu.be/jNQXAC9IVRw"

    def test_plain_text_still_works(self):
        from pipeline.sheets import _cell_url

        url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
        assert _cell_url({"formattedValue": url}) == url

    def test_empty_cell_is_safe(self):
        from pipeline.sheets import _cell_url

        assert _cell_url({}) == ""
        assert _cell_url(None) == ""

    def test_chip_url_passes_validation(self):
        """The whole point: a chipped paste must survive URL validation."""
        from safety.input_validator import validate_url
        from pipeline.sheets import _cell_url

        cell = {
            "formattedValue": "Some Video - YouTube",
            "chipRuns": [{"chip": {"richLinkProperties": {
                "uri": "https://www.youtube.com/watch?v=UdE-W30oOXo"}}}],
        }
        assert validate_url(_cell_url(cell)).video_id == "UdE-W30oOXo"


class TestScopeUnion:
    """One token must cover the whole application.

    Regression: each service requested only its own scope. Because a token is
    granted exactly what was asked for, publishing to Drive issued a
    Drive-only token and the watcher then issued a Sheets-only token - so the
    two kept overwriting each other's access and alternated between
    'invalid_scope' on read and 'insufficient authentication scopes' on
    publish.
    """

    def _requested(self, asked_for, monkeypatch, tmp_path):
        """Capture the scope list load_credentials actually asks Google for."""
        captured = {}

        class _Flow:
            @classmethod
            def from_client_secrets_file(cls, path, scopes):
                captured["scopes"] = list(scopes)
                raise RuntimeError("stop before opening a browser")

        import google_auth_oauthlib.flow as flow_mod
        monkeypatch.setattr(flow_mod, "InstalledAppFlow", _Flow)

        from pipeline.google_auth import load_credentials

        secrets = tmp_path / "client_secret.json"
        secrets.write_text("{}")
        try:
            load_credentials(asked_for, credentials_file=secrets,
                             token_file=tmp_path / "absent.json")
        except Exception:
            pass
        return set(captured.get("scopes", []))

    def test_asking_for_sheets_also_requests_drive(self, monkeypatch, tmp_path):
        from pipeline.google_auth import DRIVE_SCOPE, SHEETS_SCOPE

        requested = self._requested([SHEETS_SCOPE], monkeypatch, tmp_path)
        assert {DRIVE_SCOPE, SHEETS_SCOPE} <= requested

    def test_asking_for_drive_also_requests_sheets(self, monkeypatch, tmp_path):
        from pipeline.google_auth import DRIVE_SCOPE, SHEETS_SCOPE

        requested = self._requested([DRIVE_SCOPE], monkeypatch, tmp_path)
        assert {DRIVE_SCOPE, SHEETS_SCOPE} <= requested

    def test_extra_scopes_are_added_not_replaced(self, monkeypatch, tmp_path):
        """Using the YouTube publisher must widen the token, not narrow it."""
        from pipeline.google_auth import (
            DRIVE_SCOPE, SHEETS_SCOPE, YOUTUBE_UPLOAD_SCOPE,
        )

        requested = self._requested([YOUTUBE_UPLOAD_SCOPE], monkeypatch, tmp_path)
        assert {DRIVE_SCOPE, SHEETS_SCOPE, YOUTUBE_UPLOAD_SCOPE} <= requested

    def test_base_scopes_cover_both_services(self):
        from pipeline.google_auth import BASE_SCOPES, DRIVE_SCOPE, SHEETS_SCOPE

        assert set(BASE_SCOPES) == {DRIVE_SCOPE, SHEETS_SCOPE}
