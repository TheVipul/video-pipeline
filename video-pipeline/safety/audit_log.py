"""
Structured audit log.

Every significant pipeline action is appended to a JSONL file for compliance,
post-hoc analysis, and the final HTML report. This is *parallel* to the
standard logger - audit_log entries are guaranteed machine-parseable.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from logging_setup import get_logger

log = get_logger(__name__)


class AuditLog:
    """Thread-safe append-only JSONL audit log."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # Create/truncate the file
        self.path.write_text("", encoding="utf-8")
        log.info("audit_log_initialized", path=str(self.path))

    def record(
        self,
        event: str,
        video_id: Optional[str] = None,
        stage: Optional[str] = None,
        **fields: Any,
    ) -> None:
        """Record one event."""
        entry = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "event": event,
            "video_id": video_id,
            "stage": stage,
            **fields,
        }
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def read_all(self) -> list[dict]:
        """Read all entries (for the HTML report)."""
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
