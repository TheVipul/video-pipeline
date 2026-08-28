"""
Entry point for the Video Pipeline.

Usage:
    python run.py [--urls PATH] [--brand BRAND] [--max N] [--dry-run] [--publisher {local,s3,youtube}]

Environment:
    Reads .env (override via shell). See .env.example for full list.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agent.graph import run_pipeline
from config import get_settings
from logging_setup import get_logger, setup_logging
from pipeline.publishers import get_publisher
from report import generate_report
from safety.audit_log import AuditLog
from safety.cost_guard import CostGuard
from safety.preflight import run_preflight
from safety.proxy_health import ProxyPool

log = get_logger("main")
console = Console()


# Human-readable status per outcome. The sheet is read by people, so the
# status column says what happened in words rather than exposing the
# pipeline's internal state names.
_SHEET_STATUS = {
    "published": "Published",
    "held_for_review": "Held - needs review",
    "rejected": "Rejected",
    "skipped": "Skipped",
    "metadata_failed": "Failed - could not read video",
    "ai_failed": "Failed - AI analysis",
    "download_failed": "Failed - download",
    "transform_failed": "Failed - processing",
    "publish_failed": "Failed - upload",
}


def _sheet_results(final_state: dict, sheet_rows: list) -> dict[int, dict]:
    """Map pipeline records back onto the spreadsheet rows they came from."""
    records = final_state.get("records") or {}
    by_url = {}
    for record in records.values():
        url = record.get("source_url") or ""
        if url:
            by_url[url] = record

    results: dict[int, dict] = {}
    for row in sheet_rows:
        record = by_url.get(row.url)
        if record is None:
            results[row.row_number] = {
                "status": "Not processed",
                "notes": "Pipeline did not reach this row (limit or circuit breaker)",
            }
            continue

        enrichment = record.get("enrichment") or {}
        publish = record.get("publish") or {}
        status = record.get("status", "")
        results[row.row_number] = {
            "status": _SHEET_STATUS.get(status, status or "Unknown"),
            "title": enrichment.get("ai_title", ""),
            "summary": enrichment.get("summary", ""),
            "link": publish.get("remote_path", ""),
            "relevance": enrichment.get("relevance", ""),
            "cost": f"{enrichment.get('cost_usd', 0):.4f}",
            # The reason a video was held is the single most useful thing an
            # operator can see, so it goes in Notes ahead of any error text.
            "notes": (
                "; ".join((enrichment.get("safety") or {}).get("concerns") or [])
                or record.get("error", "")
                or enrichment.get("reasoning", "")
            )[:500],
        }
    return results


def _print_settings(settings) -> None:
    summary = settings.summary()
    table = Table(title="Pipeline Settings", show_header=True, header_style="bold")
    table.add_column("Setting")
    table.add_column("Value")
    for k, v in summary.items():
        table.add_row(k, str(v))
    console.print(table)


def _print_preflight(result) -> None:
    if result.ok:
        console.print(Panel("[green]Preflight OK[/green]", border_style="green"))
    else:
        console.print(Panel(
            "[red]Preflight FAILED[/red]\n\n" + "\n".join(f"  - {e}" for e in result.errors),
            border_style="red",
        ))
    if result.warnings:
        console.print("[yellow]Warnings:[/yellow]")
        for w in result.warnings:
            console.print(f"  - {w}")


def _sigint_handler(audit: AuditLog):
    def handler(signum, frame):
        log.warning("interrupt_received", signum=signum)
        audit.record("interrupted", signum=signum)
        console.print("\n[yellow]Interrupt received - finishing current video then exiting.[/yellow]")
        # LangGraph will pick up the interrupt on the next iteration
    return handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Video Pipeline - YouTube ingest with AI review gate")
    parser.add_argument("--urls", type=Path, default=Path("inputs/urls.txt"))
    parser.add_argument("--brand", type=str, default=None, help="Brand config name (e.g. generic, kitchenware)")
    parser.add_argument("--max", type=int, default=None, help="Max videos to process")
    parser.add_argument("--publisher", choices=["local", "gdrive", "s3", "youtube"], default="local")
    parser.add_argument("--dry-run", action="store_true", help="Skip network calls (metadata + downloads are mocked)")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--report", type=Path, default=None, help="Output HTML report path")
    parser.add_argument("--checkpoint", type=Path, default=None, help="Resume from a saved state JSON")
    # --- Google Sheets as the operator interface
    parser.add_argument("--sheet", type=str, default=None,
                        help="Google Sheet URL or id: read URLs from column A, write results back")
    parser.add_argument("--sheet-tab", type=str, default="Sheet1",
                        help="Worksheet tab name (default: Sheet1)")
    parser.add_argument("--create-sheet", action="store_true",
                        help="Create a new, correctly formatted sheet and exit")
    parser.add_argument("--force", action="store_true",
                        help="Reprocess sheet rows that already have a status")
    parser.add_argument("--mode", choices=["general", "brand"], default=None,
                        help="Override the configured mode for this run")
    args = parser.parse_args(argv)

    # Creating a sheet is a setup action, not a pipeline run.
    if args.create_sheet:
        from pipeline.sheets import SheetsClient

        client = SheetsClient.create()
        console.print(Panel.fit(
            f"[bold green]Sheet created[/bold green]\n{client.web_url}\n\n"
            "[dim]Paste video URLs into column A, then run:[/dim]\n"
            f"  python run.py --sheet {client.spreadsheet_id} --publisher gdrive",
            border_style="green",
        ))
        return 0

    settings = get_settings()
    if args.brand:
        settings.pipeline.brand = args.brand
        # Asking for a real brand implies you want that brand's rules.
        #
        # Without this, `--brand kitchenware` published everything whenever
        # .env happened to say mode=general - the user explicitly named a
        # brand and silently got none of its behaviour. An explicit --mode
        # still wins, so the override remains available.
        if args.mode is None and args.brand != "generic":
            settings.pipeline.mode = "brand"
            settings.pipeline.enable_watermark = True
            settings.pipeline.enable_relevance_gate = True

    if args.mode:
        settings.pipeline.mode = args.mode
        is_brand = args.mode == "brand"
        settings.pipeline.enable_watermark = is_brand
        settings.pipeline.enable_relevance_gate = is_brand
    if args.max:
        settings.pipeline.max_videos = args.max
    if args.dry_run:
        settings.pipeline.dry_run = True

    output_dir = settings.pipeline.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(log_dir=output_dir / "logs", level=args.log_level, json_logs=False)
    console.print(Panel.fit(
        "[bold cyan]Video Pipeline[/bold cyan]\n"
        "[dim]YouTube ingest, AI review gate, and publishing[/dim]",
        border_style="cyan",
    ))
    _print_settings(settings)

    # When driven from a sheet, column A replaces inputs/urls.txt as the
    # source of work. Rows already carrying a status are skipped so a re-run
    # is cheap and cannot double-publish.
    sheet_client = None
    sheet_rows: list = []
    if args.sheet:
        from pipeline.sheets import SheetsClient

        sheet_client = SheetsClient(args.sheet, tab=args.sheet_tab)
        try:
            sheet_client.ensure_headers()
            sheet_rows = sheet_client.read_rows(include_done=args.force)
        except Exception as exc:  # noqa: BLE001 - a bad sheet must not traceback
            console.print(f"[red]Could not read the sheet:[/red] {exc}")
            return 4

        if not sheet_rows:
            console.print(
                "[yellow]No pending rows.[/yellow] Add URLs to column A, "
                "or pass --force to reprocess rows that already ran."
            )
            return 0

        limit = settings.pipeline.max_videos
        sheet_rows = sheet_rows[:limit] if limit else sheet_rows
        args.urls = output_dir / "_sheet_urls.txt"
        args.urls.write_text("\n".join(r.url for r in sheet_rows) + "\n")
        console.print(
            f"[cyan]Sheet:[/cyan] {len(sheet_rows)} pending row(s) "
            f"from {sheet_client.web_url}"
        )

    audit = AuditLog(output_dir / "audit.jsonl")
    signal.signal(signal.SIGINT, _sigint_handler(audit))

    # Preflight
    preflight = run_preflight(settings, args.urls)
    _print_preflight(preflight)
    if not preflight.ok:
        console.print("[red]Aborting due to preflight failures.[/red]")
        return 1

    # Cost guard
    cost_guard = CostGuard(settings.safety, model=settings.llm.model)

    # Proxy pool
    proxy_pool = ProxyPool(settings.proxy.proxy_file, settings.safety)

    # Publisher
    if args.publisher == "s3":
        # S3 requires boto3 + credentials. Caller must provide via env.
        from pipeline.publishers.s3 import S3Publisher
        s3_bucket = settings.pipeline.output_dir.name + "-bucket"  # placeholder
        try:
            publisher = S3Publisher(bucket=s3_bucket)
        except Exception as exc:
            console.print(f"[yellow]S3 publisher init failed ({exc}); falling back to local.[/yellow]")
            publisher = get_publisher("local", output_dir=output_dir / "published")
    else:
        # The factory filters kwargs per publisher, so callers can pass the
        # superset without knowing which backend was selected.
        publisher = get_publisher(
            args.publisher,
            output_dir=output_dir / "published",
            # Drive: file outputs under a per-brand subfolder so one Drive can
            # serve several brands without their outputs mixing.
            subfolder=settings.pipeline.brand,
        )

    console.print(f"[green]Using publisher:[/green] {publisher.name}")

    # Run
    try:
        final_state = run_pipeline(
            settings=settings,
            urls_file=args.urls,
            audit=audit,
            cost_guard=cost_guard,
            proxy_pool=proxy_pool,
            primary_publisher=publisher,
            resume_state_file=args.checkpoint,
        )
    except Exception as exc:
        log.exception("pipeline_crashed", error=str(exc))
        console.print(f"[red]Pipeline crashed: {exc}[/red]")
        return 2

    # Persist final state
    state_path = output_dir / "final_state.json"
    state_path.write_text(json.dumps(final_state, indent=2, default=str), encoding="utf-8")
    console.print(f"[green]Final state saved:[/green] {state_path}")

    # Write results back beside the URLs the operator pasted.
    if sheet_client and sheet_rows:
        try:
            written = sheet_client.write_results(
                _sheet_results(final_state, sheet_rows)
            )
            console.print(
                f"[green]Sheet updated:[/green] {written} row(s) -> "
                f"{sheet_client.web_url}"
            )
        except Exception as exc:  # noqa: BLE001 - the run itself already succeeded
            console.print(f"[yellow]Could not write results to the sheet:[/yellow] {exc}")

    # Report
    report_path = args.report or (output_dir / "report.html")
    try:
        report_path = generate_report(
            final_state=final_state,
            audit=audit,
            proxy_pool=proxy_pool,
            brand=settings.pipeline.brand,
            output_path=report_path,
        )
        console.print(f"[green]Report:[/green] {report_path}")
    except Exception as exc:
        log.error("report_failed", error=str(exc))
        console.print(f"[yellow]Report generation failed: {exc}[/yellow]")

    # Summary
    succ = len(final_state.get("successful_ids") or [])
    fail = len(final_state.get("failed_ids") or [])
    skip = len(final_state.get("skipped_ids") or [])
    cost = final_state.get("total_cost_usd") or 0.0

    # "rejected" and "held for review" both stop a video, but they mean very
    # different things to an operator: one is a decision, the other is a queue.
    # Collapsing them into a single number hides work that needs a human.
    records = final_state.get("records") or {}
    blocked = [r for r in records.values() if r.get("status") in ("rejected", "held_for_review")]
    held = sum(1 for r in blocked if r.get("status") == "held_for_review")
    rej = sum(1 for r in blocked if r.get("status") == "rejected")

    # Degraded stages are logged per-video; surface them so a "success" that
    # quietly skipped the AI layer cannot be mistaken for a clean run.
    degraded = sum(
        1 for r in records.values()
        if (r.get("enrichment") or {}).get("skipped_reason")
    )

    lines = [
        "[bold green]Done[/bold green]",
        f"Published: {succ} | Failed: {fail} | Rejected: {rej} | "
        f"Held for review: {held} | Skipped: {skip}",
        f"LLM cost: ${cost:.4f}",
    ]
    if degraded:
        lines.append(f"[yellow]AI degraded on {degraded} video(s) - see audit log[/yellow]")
    if held:
        lines.append(f"[yellow]{held} video(s) awaiting human review - not published[/yellow]")

    console.print(Panel.fit(
        "\n".join(lines),
        border_style="green" if fail == 0 and not degraded else "yellow",
    ))

    return 0 if fail == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
