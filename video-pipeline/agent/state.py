"""
LangGraph state schema.

The agent's state is the single source of truth for a pipeline run. It carries:
    - The list of validated URLs
    - The current processing index
    - Per-video results (metadata, AI enrichment, download, transform, publish)
    - Aggregate stats (cost, retries, success count)
    - Persistent history that the agent "remembers" across retry decisions

The state is JSON-serializable so it can be checkpointed to disk between runs
or after a crash.
"""
from __future__ import annotations

from typing import Annotated, Any, Optional, TypedDict

from langgraph.graph.message import add_messages


def merge_records(
    existing: Optional[dict[str, "VideoRecord"]],
    incoming: Optional[dict[str, "VideoRecord"]],
) -> dict[str, "VideoRecord"]:
    """Merge per-video records instead of replacing the whole dict.

    Every node returns `{"records": {video_id: record}}` for just the video it
    touched. Without an explicit reducer LangGraph applies last-write-wins to
    the *whole* key, so each node was silently discarding every previously
    processed video: a five-video run ended with a single record, and both
    final_state.json and the HTML report showed one row instead of five.

    Merging per key preserves the full run history. Records for the same
    video_id are merged field-wise so a later stage (publish) does not drop
    what an earlier stage (metadata) recorded.
    """
    merged: dict[str, VideoRecord] = dict(existing or {})
    for video_id, record in (incoming or {}).items():
        if video_id in merged:
            combined = dict(merged[video_id])
            combined.update(record)
            merged[video_id] = combined  # type: ignore[assignment]
        else:
            merged[video_id] = record
    return merged


class VideoRecord(TypedDict, total=False):
    video_id: str
    source_url: str
    # Failure statuses are stage-specific on purpose: "which stage did this
    # die in?" is the first question an operator asks, and a single generic
    # "failed" cannot answer it. Every terminal failure ends in "_failed".
    #   in-flight : pending | metadata_in_progress | metadata_ok | enriched
    #               | downloaded | transformed
    #   success   : published
    #   blocked   : rejected (policy) | held_for_review (needs a human)
    #   skipped   : skipped (e.g. duration cap)
    #   failure   : metadata_failed | ai_failed | download_failed
    #               | transform_failed | publish_failed
    status: str
    error: str
    metadata: dict[str, Any]         # VideoMetadata fields
    enrichment: dict[str, Any]       # AIEnrichment fields (incl. safety verdict)
    download: dict[str, Any]         # DownloadResult fields
    transform: dict[str, Any]        # TransformResult fields
    publish: dict[str, Any]          # PublishResult fields
    attempts: int
    proxies_tried: list[str]
    started_at: str
    finished_at: str


class PipelineState(TypedDict, total=False):
    # Inputs
    urls: list[dict]                 # ValidatedURL as dicts
    brand: str
    max_videos: int
    dry_run: bool

    # Per-video records, keyed by video_id. The reducer merges rather than
    # replaces - see merge_records above.
    records: Annotated[dict[str, VideoRecord], merge_records]

    # Execution cursor
    current_index: int
    consecutive_failures: int
    circuit_breaker_open: bool
    circuit_breaker_opened_at: str

    # Aggregate stats
    total_cost_usd: float
    total_prompt_tokens: int
    total_completion_tokens: int
    started_at: str
    finished_at: str

    # Final output - success list
    successful_ids: list[str]
    failed_ids: list[str]
    rejected_ids: list[str]
    skipped_ids: list[str]

    # For human-readable progress
    log: Annotated[list[str], "append"]
