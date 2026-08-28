"""
HTML report generator.

After the pipeline finishes, this module reads the audit log + final state
and produces a self-contained HTML report with:
    - Per-video timeline
    - Cost summary
    - Proxy health
    - Safety verdicts
    - All errors
    - Direct links to published files

The report is one HTML file with no external dependencies - reviewers can
open it from a file:// URL with no network.
"""
from __future__ import annotations

import json
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from jinja2 import Template

from logging_setup import get_logger

log = get_logger(__name__)

REPORT_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Video Pipeline - Run Report</title>
<style>
:root {
  --bg: #0f1115; --panel: #1a1d24; --border: #2a2f3a;
  --text: #e5e7eb; --muted: #94a3b8;
  --green: #34d399; --red: #f87171; --yellow: #fbbf24;
  --blue: #60a5fa; --purple: #a78bfa;
}
* { box-sizing: border-box; }
body { margin: 0; padding: 0; background: var(--bg); color: var(--text);
       font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       line-height: 1.5; }
.wrap { max-width: 1200px; margin: 0 auto; padding: 32px 24px; }
h1 { font-size: 28px; margin: 0 0 8px; }
h2 { font-size: 20px; margin: 32px 0 12px; padding-bottom: 8px;
     border-bottom: 1px solid var(--border); }
.subtitle { color: var(--muted); font-size: 14px; margin-bottom: 24px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
         gap: 12px; margin-bottom: 24px; }
.card { background: var(--panel); border: 1px solid var(--border);
        border-radius: 8px; padding: 16px; }
.card .label { font-size: 12px; color: var(--muted);
               text-transform: uppercase; letter-spacing: 0.5px; }
.card .value { font-size: 24px; font-weight: 600; margin-top: 4px; }
.card .value.green { color: var(--green); }
.card .value.red { color: var(--red); }
.card .value.yellow { color: var(--yellow); }
.card .value.blue { color: var(--blue); }
.card .value.purple { color: var(--purple); }
table { width: 100%; border-collapse: collapse; background: var(--panel);
        border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
th, td { padding: 10px 14px; text-align: left; font-size: 14px;
         border-bottom: 1px solid var(--border); }
th { background: #131720; color: var(--muted); font-weight: 500;
     text-transform: uppercase; font-size: 11px; letter-spacing: 0.5px; }
tr:last-child td { border-bottom: none; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 4px;
         font-size: 11px; font-weight: 600; text-transform: uppercase; }
.badge.green { background: rgba(52, 211, 153, 0.15); color: var(--green); }
.badge.red { background: rgba(248, 113, 113, 0.15); color: var(--red); }
.badge.yellow { background: rgba(251, 191, 36, 0.15); color: var(--yellow); }
.badge.blue { background: rgba(96, 165, 250, 0.15); color: var(--blue); }
.badge.purple { background: rgba(167, 139, 250, 0.15); color: var(--purple); }
.badge.gray { background: rgba(148, 163, 184, 0.15); color: var(--muted); }
.code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
        font-size: 12px; background: #0a0c10; padding: 2px 6px; border-radius: 4px; }
.muted { color: var(--muted); font-size: 12px; }
.error { color: var(--red); font-size: 12px; }
pre { background: #0a0c10; padding: 12px; border-radius: 6px;
      overflow-x: auto; font-size: 12px; }
details { margin-top: 8px; }
summary { cursor: pointer; color: var(--muted); }
</style>
</head>
<body>
<div class="wrap">
  <h1>Video Pipeline - Run Report</h1>
  <div class="subtitle">Generated {{ generated_at }} &middot; Brand: <span class="code">{{ brand }}</span></div>

  <div class="cards">
    <div class="card">
      <div class="label">Successful</div>
      <div class="value green">{{ successful_count }} / {{ total }}</div>
    </div>
    <div class="card">
      <div class="label">Failed</div>
      <div class="value red">{{ failed_count }}</div>
    </div>
    <div class="card">
      <div class="label">Rejected</div>
      <div class="value yellow">{{ rejected_count }}</div>
    </div>
    <div class="card">
      <div class="label">Skipped</div>
      <div class="value blue">{{ skipped_count }}</div>
    </div>
    <div class="card">
      <div class="label">LLM Cost</div>
      <div class="value purple">${{ total_cost_usd }}</div>
    </div>
    <div class="card">
      <div class="label">Total Bytes</div>
      <div class="value">{{ total_bytes_human }}</div>
    </div>
    <div class="card">
      <div class="label">Started</div>
      <div class="value" style="font-size:14px;">{{ started_at }}</div>
    </div>
    <div class="card">
      <div class="label">Finished</div>
      <div class="value" style="font-size:14px;">{{ finished_at }}</div>
    </div>
  </div>

  <h2>Per-Video Results</h2>
  <table>
    <thead>
      <tr>
        <th>Status</th>
        <th>Video</th>
        <th>Title</th>
        <th>Duration</th>
        <th>Safety</th>
        <th>Size</th>
        <th>Attempts</th>
        <th>Proxy</th>
      </tr>
    </thead>
    <tbody>
    {% for v in videos %}
      <tr>
        <td><span class="badge {{ v.status_class }}">{{ v.status }}</span></td>
        <td><span class="code">{{ v.video_id }}</span></td>
        <td>{{ v.title }}</td>
        <td>{{ v.duration }}</td>
        <td><span class="badge {{ v.safety_class }}">{{ v.safety_verdict }}</span></td>
        <td>{{ v.size }}</td>
        <td>{{ v.attempts }}</td>
        <td><span class="muted">{{ v.proxy }}</span></td>
      </tr>
      {% if v.error %}
      <tr>
        <td colspan="8"><div class="error">Error: {{ v.error }}</div></td>
      </tr>
      {% endif %}
    {% endfor %}
    </tbody>
  </table>

  <h2>Proxy Health</h2>
  <table>
    <thead>
      <tr><th>URL</th><th>Requests</th><th>Failures</th><th>Consec. Failures</th><th>Healthy</th></tr>
    </thead>
    <tbody>
    {% for p in proxies %}
      <tr>
        <td><span class="code">{{ p.url }}</span></td>
        <td>{{ p.total_requests }}</td>
        <td>{{ p.total_failures }}</td>
        <td>{{ p.consecutive_failures }}</td>
        <td>{% if p.is_healthy %}<span class="badge green">yes</span>{% else %}<span class="badge red">no</span>{% endif %}</td>
      </tr>
    {% endfor %}
    {% if not proxies %}
      <tr><td colspan="5" class="muted">No proxies were used (direct connection).</td></tr>
    {% endif %}
    </tbody>
  </table>

  <h2>Audit Log (latest 200 events)</h2>
  <details>
    <summary>Show raw events</summary>
    <pre>{{ audit_log_text }}</pre>
  </details>
</div>
</body>
</html>
"""


def _status_class(status: str) -> str:
    """Colour a status for the report.

    Failure statuses are stage-specific (metadata_failed, download_failed,
    transform_failed, publish_failed, ai_failed) so the report says *where* a
    video died. Matching on the suffix rather than enumerating every value
    means a new stage cannot silently render as neutral grey.
    """
    exact = {
        "published": "green",
        "downloaded": "blue",
        "transformed": "blue",
        "enriched": "purple",
        "metadata_ok": "gray",
        "metadata_in_progress": "gray",
        "rejected": "red",
        "held_for_review": "yellow",
        "skipped": "yellow",
        "pending": "gray",
    }
    if status in exact:
        return exact[status]
    if status.endswith("_failed") or status == "failed":
        return "red"
    return "gray"


def _safety_class(verdict: str) -> str:
    return {"safe": "green", "review": "yellow", "reject": "red"}.get(verdict, "gray")


def _human_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024 ** 3:.2f} GB"


def generate_report(
    final_state: dict,
    audit,
    proxy_pool,
    brand: str,
    output_path: Path,
) -> Path:
    records = final_state.get("records") or {}
    videos = []
    total_bytes = 0
    for vid, rec in records.items():
        size = 0
        if rec.get("transform", {}).get("file_size_bytes"):
            size = rec["transform"]["file_size_bytes"]
        elif rec.get("download", {}).get("file_size_bytes"):
            size = rec["download"]["file_size_bytes"]
        total_bytes += size
        safety = rec.get("enrichment", {}).get("safety", {})
        videos.append({
            "video_id": vid,
            "status": rec.get("status", "unknown"),
            "status_class": _status_class(rec.get("status", "unknown")),
            "title": (rec.get("metadata", {}).get("title") or "(no title)")[:60],
            "duration": f"{rec.get('metadata', {}).get('duration_sec', 0)}s",
            "safety_verdict": safety.get("verdict", "n/a"),
            "safety_class": _safety_class(safety.get("verdict", "n/a")),
            "size": _human_bytes(size),
            "attempts": rec.get("attempts", 0),
            "proxy": (rec.get("download", {}) or {}).get("proxy_used", "n/a"),
            "error": rec.get("error", ""),
        })

    template = Template(REPORT_TEMPLATE)
    html = template.render(
        generated_at=datetime.utcnow().isoformat() + "Z",
        brand=brand,
        total=len(records),
        successful_count=len(final_state.get("successful_ids") or []),
        failed_count=len(final_state.get("failed_ids") or []),
        rejected_count=len(final_state.get("rejected_ids") or []),
        skipped_count=len(final_state.get("skipped_ids") or []),
        total_cost_usd=f"{final_state.get('total_cost_usd') or 0.0:.4f}",
        total_bytes_human=_human_bytes(total_bytes),
        started_at=final_state.get("started_at", "n/a"),
        finished_at=final_state.get("finished_at", "n/a"),
        videos=videos,
        proxies=proxy_pool.stats() if proxy_pool else [],
        audit_log_text=json.dumps(audit.read_all()[-200:], indent=2, default=str),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    log.info("report_written", path=str(output_path))
    return output_path
