"""
Watch a Google Sheet and run the pipeline whenever a URL is added.

Paste a URL into column A; within a few seconds the pipeline starts. No
scheduler, no cron, no terminal for the operator.

Why polling rather than push: Google's Sheets push notifications deliver to a
publicly reachable HTTPS endpoint, which a laptop is not. Getting real push
would mean a deployed webhook receiver or a tunnel - worth it in production,
not worth the fragility for a workstation tool. Polling column A:B is a cheap
read (well inside Sheets' per-minute quota at any sane interval) and the
observable behaviour is identical.

Each detected batch runs as a subprocess rather than in-process. That is
deliberate: a crash, a hung download or an OOM in one batch cannot take the
watcher down with it, and the watcher keeps serving the next paste.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from pipeline.sheets import SheetsClient

console = Console()
ROOT = Path(__file__).resolve().parent

# 10s feels instant to someone pasting a URL, and is ~360 reads/hour - two
# orders of magnitude inside the Sheets read quota.
DEFAULT_INTERVAL = 10

# On repeated API errors, back off rather than hammering a failing endpoint.
MAX_BACKOFF = 300


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def run_batch(sheet_id: str, publisher: str, brand: str | None,
              extra: list[str]) -> int:
    """Invoke the pipeline for whatever is currently pending."""
    cmd = [
        sys.executable, str(ROOT / "run.py"),
        "--sheet", sheet_id,
        "--publisher", publisher,
        "--log-level", "ERROR",
    ]
    if brand:
        cmd += ["--brand", brand]
    cmd += extra

    console.print(f"[dim]{_now()}[/dim] [cyan]running pipeline...[/cyan]")
    result = subprocess.run(cmd, cwd=str(ROOT))
    return result.returncode


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Watch a Google Sheet and process URLs as they are added."
    )
    parser.add_argument("--sheet", required=True, help="Sheet URL or id")
    parser.add_argument("--publisher", default="gdrive",
                        choices=["local", "gdrive", "s3", "youtube"])
    parser.add_argument("--brand", default=None)
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL,
                        help=f"Seconds between checks (default {DEFAULT_INTERVAL})")
    parser.add_argument("--sheet-tab", default="Sheet1")
    parser.add_argument("--once", action="store_true",
                        help="Process what is pending, then exit")
    args = parser.parse_args(argv)

    client = SheetsClient(args.sheet, tab=args.sheet_tab)
    try:
        client.ensure_headers()
    except Exception as exc:  # noqa: BLE001 - a bad sheet should not traceback
        console.print(f"[red]Cannot open the sheet:[/red] {exc}")
        return 4

    console.print(Panel.fit(
        f"[bold cyan]Watching for new URLs[/bold cyan]\n"
        f"{client.web_url}\n\n"
        f"[dim]Publisher: {args.publisher}"
        + (f"  |  Brand: {args.brand}" if args.brand else "")
        + f"  |  Checking every {args.interval}s[/dim]\n\n"
        "[dim]Paste a YouTube URL into column A. Ctrl+C to stop.[/dim]",
        border_style="cyan",
    ))

    extra = ["--sheet-tab", args.sheet_tab]
    backoff = args.interval
    processed_total = 0

    try:
        while True:
            try:
                pending = client.read_rows()
                backoff = args.interval          # healthy again
            except Exception as exc:  # noqa: BLE001
                console.print(
                    f"[dim]{_now()}[/dim] [yellow]sheet read failed:[/yellow] "
                    f"{str(exc)[:90]} - retrying in {backoff}s"
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)
                continue

            if pending:
                rows = ", ".join(f"row {r.row_number}" for r in pending[:5])
                more = f" (+{len(pending) - 5} more)" if len(pending) > 5 else ""
                console.print(
                    f"\n[dim]{_now()}[/dim] [green]detected "
                    f"{len(pending)} new URL(s)[/green] - {rows}{more}"
                )
                run_batch(args.sheet, args.publisher, args.brand, extra)
                processed_total += len(pending)
                console.print(
                    f"[dim]{_now()}[/dim] [dim]waiting for the next URL "
                    f"({processed_total} processed this session)[/dim]"
                )

            if args.once:
                return 0
            time.sleep(args.interval)

    except KeyboardInterrupt:
        console.print(
            f"\n[cyan]Stopped.[/cyan] {processed_total} video(s) processed "
            f"this session."
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
