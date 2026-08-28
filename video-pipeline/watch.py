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

from pipeline.google_auth import GoogleAuthError
from pipeline.sheets import SheetsClient

console = Console()
ROOT = Path(__file__).resolve().parent

# 10s feels instant to someone pasting a URL, and is ~360 reads/hour - two
# orders of magnitude inside the Sheets read quota.
DEFAULT_INTERVAL = 10

# On repeated API errors, back off rather than hammering a failing endpoint.
MAX_BACKOFF = 300


# Substrings that mean "the credentials are wrong", as opposed to a network
# blip. Matched against the whole exception text because the Google libraries
# surface these through several different exception types.
_AUTH_MARKERS = (
    "invalid_scope",
    "invalid_grant",
    "invalid_client",
    "unauthorized",
    "token has been expired or revoked",
    "insufficient authentication scopes",
    "insufficientpermissions",
)


def _is_auth_error(exc: Exception) -> bool:
    if isinstance(exc, GoogleAuthError):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _AUTH_MARKERS)


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
                # Authorisation problems never fix themselves. Retrying one
                # every few minutes just hides it: the watcher looks alive
                # while the sheet silently stops updating. Stop, and say what
                # to do about it.
                if _is_auth_error(exc):
                    console.print(Panel.fit(
                        "[bold red]Google authorisation has failed[/bold red]\n\n"
                        f"{str(exc)[:200]}\n\n"
                        "[bold]This will not recover on its own.[/bold] Most likely the\n"
                        "token expired (Google expires them after 7 days for apps\n"
                        "in testing mode), or it was issued for narrower access\n"
                        "than the watcher needs.\n\n"
                        "Fix it on this machine, then start the watcher again:\n"
                        "    python run.py --max 1 --publisher gdrive",
                        border_style="red",
                    ))
                    return 3

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
