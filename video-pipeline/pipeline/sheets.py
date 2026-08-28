"""
Google Sheets as the operator interface.

The CLI is for engineers. This is for the person who actually has to run the
thing every week - they paste URLs into a spreadsheet, come back later, and
the results are filled in beside them. No terminal, no install, no training.

Design notes:

Idempotency is by row. A row that already has a status is skipped, so re-running
against the same sheet costs nothing and cannot double-publish. `--force`
overrides that for deliberate reprocessing.

Writes are batched into a single API call per run rather than one call per
cell. Sheets quotas are per-minute and per-user; a naive cell-at-a-time
implementation hits them on a sheet of any real size.

Column meanings are fixed by position (A = URL, B = status, ...) rather than
by reading the header row. Operators rename headers - it is a spreadsheet -
and position is the stable contract.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from logging_setup import get_logger
from pipeline.google_auth import SHEETS_SCOPE, build_service

log = get_logger(__name__)

# Fixed column layout. Index 0 = column A.
COL_URL = 0
COL_STATUS = 1
COL_TITLE = 2
COL_SUMMARY = 3
COL_LINK = 4
COL_RELEVANCE = 5
COL_COST = 6
COL_NOTES = 7
COL_PROCESSED_AT = 8

HEADERS = [
    "Video URL",
    "Status",
    "Title",
    "Summary",
    "Published Link",
    "Relevance",
    "Cost (USD)",
    "Notes",
    "Processed At",
]

# Sheets caps a single cell at 50k characters; stay well clear.
MAX_CELL_CHARS = 4000

SHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")


def extract_sheet_id(value: str) -> str:
    """Accept a full Sheets URL or a bare id - operators paste either."""
    match = SHEET_ID_RE.search(value or "")
    return match.group(1) if match else (value or "").strip()


@dataclass
class SheetRow:
    row_number: int          # 1-based, matching what the operator sees
    url: str
    status: str = ""

    @property
    def is_pending(self) -> bool:
        return not self.status.strip()


class SheetsClient:
    def __init__(
        self,
        spreadsheet_id: str,
        tab: str = "Sheet1",
        credentials_file=None,
        token_file=None,
    ) -> None:
        self.spreadsheet_id = extract_sheet_id(spreadsheet_id)
        self.tab = tab
        self._credentials_file = credentials_file
        self._token_file = token_file
        self._service = None

    def _svc(self):
        if self._service is None:
            self._service = build_service(
                "sheets", "v4", [SHEETS_SCOPE],
                credentials_file=self._credentials_file,
                token_file=self._token_file,
            )
        return self._service

    # --- setup ----------------------------------------------------------------

    @classmethod
    def create(
        cls, title: str = "Video Pipeline", credentials_file=None, token_file=None
    ) -> "SheetsClient":
        """Create a ready-to-use sheet, headers and all.

        Saves the operator from getting the column order right by hand, which
        is exactly the kind of setup step that quietly blocks adoption.
        """
        service = build_service(
            "sheets", "v4", [SHEETS_SCOPE],
            credentials_file=credentials_file, token_file=token_file,
        )
        created = service.spreadsheets().create(
            body={"properties": {"title": title}}, fields="spreadsheetId,spreadsheetUrl"
        ).execute()
        client = cls(
            created["spreadsheetId"],
            credentials_file=credentials_file, token_file=token_file,
        )
        client._service = service
        client.ensure_headers()
        log.info("sheet_created", url=created["spreadsheetUrl"])
        client.url = created["spreadsheetUrl"]
        return client

    def ensure_headers(self) -> None:
        """Write the header row if the sheet is empty. Never overwrites data."""
        existing = self._svc().spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id, range=f"{self.tab}!A1:I1"
        ).execute().get("values", [])
        if existing and any(c.strip() for c in existing[0]):
            return
        self._svc().spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"{self.tab}!A1:I1",
            valueInputOption="RAW",
            body={"values": [HEADERS]},
        ).execute()
        log.info("sheet_headers_written", sheet=self.spreadsheet_id)

    # --- read -----------------------------------------------------------------

    def read_rows(self, include_done: bool = False) -> list[SheetRow]:
        """Read URL rows. By default only those not yet processed."""
        values = self._svc().spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id, range=f"{self.tab}!A2:B",
        ).execute().get("values", [])

        rows: list[SheetRow] = []
        for offset, raw in enumerate(values):
            url = (raw[0] if len(raw) > 0 else "").strip()
            status = (raw[1] if len(raw) > 1 else "").strip()
            if not url:
                continue
            row = SheetRow(row_number=offset + 2, url=url, status=status)
            if include_done or row.is_pending:
                rows.append(row)

        log.info(
            "sheet_rows_read",
            total=len(values), returned=len(rows), include_done=include_done,
        )
        return rows

    # --- write ----------------------------------------------------------------

    def write_results(self, results: dict[int, dict]) -> int:
        """Write results back, one batched call for the whole run.

        `results` maps row number -> field dict. Only columns B..I are touched,
        so whatever the operator keeps in column A stays exactly as typed.
        """
        if not results:
            return 0

        data = []
        for row_number, fields in sorted(results.items()):
            values = [
                _cell(fields.get("status")),
                _cell(fields.get("title")),
                _cell(fields.get("summary")),
                _cell(fields.get("link")),
                _cell(fields.get("relevance")),
                _cell(fields.get("cost")),
                _cell(fields.get("notes")),
                fields.get("processed_at")
                or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            ]
            data.append({"range": f"{self.tab}!B{row_number}:I{row_number}",
                         "values": [values]})

        self._svc().spreadsheets().values().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"valueInputOption": "RAW", "data": data},
        ).execute()
        log.info("sheet_results_written", rows=len(data))
        return len(data)

    @property
    def web_url(self) -> str:
        return f"https://docs.google.com/spreadsheets/d/{self.spreadsheet_id}/edit"


def _cell(value) -> str:
    """Coerce anything to a safe cell string."""
    if value is None:
        return ""
    text = str(value)
    return text[:MAX_CELL_CHARS] if len(text) > MAX_CELL_CHARS else text
