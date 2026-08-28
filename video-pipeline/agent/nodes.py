"""
LangGraph agent nodes.

Each node takes the current state, performs one stage, and returns a partial
state update. The graph wires them up in agent/graph.py.

Nodes:
    load_urls       - read & validate URLs file
    extract_metadata- stage 1
    ai_analyze      - stage 2 (with safety guard)
    check_safety    - branch: proceed or reject
    download        - stage 3 (with retry)
    transform       - stage 4
    publish         - stage 5
    advance         - move to next URL
    decide_next     - conditional: more URLs or finish
    maybe_open_breaker - open circuit breaker after N failures
"""
from __future__ import annotations

import json
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from langgraph.graph import END

from agent.state import PipelineState, VideoRecord
from config import Settings
from logging_setup import get_logger
from pipeline.ai_analyzer import AIEnrichment, enrich as ai_enrich
from pipeline.downloader import DownloadResult, download_video
from pipeline.metadata import VideoMetadata, fetch_metadata, is_creative_commons
from pipeline.transcript import fetch_transcript
from pipeline.transformer import TransformResult, transform_video
from safety.audit_log import AuditLog
from safety.cost_guard import CostGuard
from safety.input_validator import (
    InputValidationError, validate_duration, validate_url_file,
)

log = get_logger(__name__)


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _make_record(video_id: str, source_url: str) -> VideoRecord:
    return VideoRecord(
        video_id=video_id,
        source_url=source_url,
        status="pending",
        error="",
        attempts=0,
        proxies_tried=[],
        started_at=_now_iso(),
        finished_at="",
    )


# ----------------------------------------------------------------------------
# Node: load_urls
# ----------------------------------------------------------------------------
def load_urls_node(state: PipelineState, *, settings: Settings, urls_file: Path, audit: AuditLog) -> dict:
    log.info("node_load_urls_start", urls_file=str(urls_file))
    records: dict[str, VideoRecord] = {}
    valid_urls: list[dict] = []

    if state.get("urls"):
        # Already loaded (e.g. from a checkpoint) - skip
        log.info("node_load_urls_skip_already_loaded", count=len(state["urls"]))
        return {}

    try:
        validated = validate_url_file(urls_file)
    except InputValidationError as exc:
        log.error("node_load_urls_failed", error=str(exc))
        return {
            "log": [f"load_urls failed: {exc}"],
            "finished_at": _now_iso(),
        }

    max_videos = state.get("max_videos") or settings.pipeline.max_videos
    for v in validated[:max_videos]:
        valid_urls.append({"url": v.url, "video_id": v.video_id, "video_type": v.video_type})
        records[v.video_id] = _make_record(v.video_id, v.url)
        audit.record("url_loaded", video_id=v.video_id, source_url=v.url, video_type=v.video_type)

    log.info("node_load_urls_ok", count=len(valid_urls))
    return {
        "urls": valid_urls,
        "records": records,
        "current_index": 0,
        "consecutive_failures": 0,
        "circuit_breaker_open": False,
        "total_cost_usd": 0.0,
        "total_prompt_tokens": 0,
        "total_completion_tokens": 0,
        "started_at": _now_iso(),
        "successful_ids": [],
        "failed_ids": [],
        "rejected_ids": [],
        "skipped_ids": [],
        "log": [f"loaded {len(valid_urls)} URLs"],
    }


# ----------------------------------------------------------------------------
# Node: extract_metadata
# ----------------------------------------------------------------------------
def extract_metadata_node(
    state: PipelineState, *, settings: Settings, proxy_pool, audit: AuditLog,
) -> dict:
    urls = state.get("urls", [])
    idx = state.get("current_index", 0)
    if idx >= len(urls):
        return {}

    target = urls[idx]
    video_id = target["video_id"]
    log.info("node_extract_metadata", video_id=video_id, index=idx)

    record = (state.get("records") or {}).get(video_id, _make_record(video_id, target["url"]))
    record["status"] = "metadata_in_progress"
    record["attempts"] += 1
    audit.record("metadata_start", video_id=video_id, attempt=record["attempts"])

    try:
        md = fetch_metadata(
            url=target["url"],
            video_id=video_id,
            yt_settings=settings.youtube,
            proxy_pool=proxy_pool,
            # Reuse the download stage's throttle window so both stages pace
            # themselves identically against YouTube.
            min_interval_sec=settings.safety.download_min_interval_sec,
            max_interval_sec=settings.safety.download_max_interval_sec,
        )
        # Duration check
        try:
            validate_duration(md.duration_sec, settings.pipeline)
        except InputValidationError as exc:
            record["status"] = "skipped"
            record["error"] = f"duration_check_failed: {exc}"
            audit.record("duration_check_failed", video_id=video_id, error=str(exc))
            log.warning("node_extract_metadata_duration_failed", video_id=video_id, error=str(exc))
            return _advance_skip(state, video_id, record, "duration_too_long", audit)

        # Persist the route history, including blocked attempts, so the audit
        # trail can answer "how often are we being blocked, and on which
        # routes?" - not just "which route eventually worked".
        record["metadata_attempts"] = md.attempt_log
        blocked = [a for a in md.attempt_log if a.get("kind") == "blocked"]
        if blocked:
            audit.record(
                "metadata_blocked_attempts",
                video_id=video_id,
                blocked=len(blocked),
                routes=[a["proxy"] for a in blocked],
            )

        record["status"] = "metadata_ok"
        record["metadata"] = {
            "license": md.license,
            "is_creative_commons": is_creative_commons(md.license),
            "categories": md.categories,
            "comment_count": md.comment_count,
            "resolution": f"{md.width}x{md.height}" if md.width else "",
            "fps": md.fps,
            "has_captions": md.has_captions,
            "caption_languages": md.caption_languages,
            "age_limit": md.age_limit,
            "chapters": len(md.chapters),
            "title": md.title,
            "description": md.description[:1000],
            "duration_sec": md.duration_sec,
            "channel": md.channel,
            "uploader": md.uploader,
            "tags": md.tags,
            "thumbnail_url": md.thumbnail_url,
            "view_count": md.view_count,
        }
        audit.record(
            "metadata_ok", video_id=video_id,
            duration_sec=md.duration_sec, channel=md.channel,
            proxy=md.fetched_with,
        )
        return {"records": {video_id: record}}

    except Exception as exc:
        record["status"] = "metadata_failed"
        record["error"] = f"metadata_error: {exc}"
        audit.record("metadata_failed", video_id=video_id, error=str(exc))
        log.error("node_extract_metadata_failed", video_id=video_id, error=str(exc))
        return _advance_fail(state, video_id, record, str(exc), audit)


# ----------------------------------------------------------------------------
# Node: ai_analyze
# ----------------------------------------------------------------------------
def ai_analyze_node(
    state: PipelineState, *, settings: Settings, cost_guard: CostGuard, audit: AuditLog,
) -> dict:
    urls = state.get("urls", [])
    idx = state.get("current_index", 0)
    if idx >= len(urls):
        return {}
    target = urls[idx]
    video_id = target["video_id"]
    log.info("node_ai_analyze", video_id=video_id)

    record = (state.get("records") or {}).get(video_id)
    if not record or record.get("status") != "metadata_ok":
        # Nothing to analyze - skip
        return {}

    md_data = record["metadata"]
    md = VideoMetadata(
        video_id=video_id,
        url=target["url"],
        title=md_data.get("title", ""),
        description=md_data.get("description", ""),
        duration_sec=md_data.get("duration_sec", 0),
        channel=md_data.get("channel", ""),
        tags=md_data.get("tags", []),
    )

    # Fetch captions before the LLM call so the model can summarise what the
    # video actually says rather than guessing from its title. This costs a
    # few KB and no video download - deliberately done before the expensive
    # stages, so a video can be judged on its content and rejected cheaply.
    transcript_text = ""
    if md_data.get("has_captions"):
        transcript = fetch_transcript(target["url"], video_id, settings.youtube)
        if transcript:
            transcript_text = transcript.text
            record["transcript"] = {
                "source": transcript.source,
                "language": transcript.language,
                "char_count": transcript.char_count,
                "truncated": transcript.truncated,
            }
            audit.record(
                "transcript_fetched", video_id=video_id,
                source=transcript.source, chars=transcript.char_count,
            )
    else:
        log.info("transcript_skipped_none_listed", video_id=video_id)

    try:
        enrichment = ai_enrich(
            md,
            brand=state.get("brand") or settings.pipeline.brand,
            llm_settings=settings.llm,
            safety_settings=settings.safety,
            cost_guard=cost_guard,
            project_root=settings.project_root,
            transcript_text=transcript_text,
            relevance_gate_enabled=settings.pipeline.enable_relevance_gate,
        )
        record["enrichment"] = enrichment.to_dict()
        record["status"] = "enriched"

        # Gate on the safety verdict.
        #
        # "reject" blocks outright. "review" also blocks by default, which is
        # the important change: previously only "reject" stopped a video, so
        # anything the model was merely unsure about - including content it had
        # scored as off-brand - was published silently with AI-invented
        # metadata. Re-publishing a third party's video under someone else's
        # brand is not a decision to make on a low-confidence guess, so an
        # uncertain verdict holds for a human instead.
        #
        # A brand that genuinely wants unattended publishing can opt in with
        # policy.publish_on_review: true.
        verdict = enrichment.safety.verdict
        publish_on_review = bool(
            (_brand_policy(settings) or {}).get("publish_on_review", False)
        )
        blocking = verdict == "reject" or (verdict == "review" and not publish_on_review)

        if blocking:
            held = verdict == "review"
            record["status"] = "held_for_review" if held else "rejected"
            record["error"] = (
                f"brand_safety_{verdict}: {enrichment.reasoning or 'n/a'}"
            )
            audit.record(
                "ai_safety_hold" if held else "ai_safety_reject",
                video_id=video_id,
                verdict=verdict,
                confidence=enrichment.safety.confidence,
                relevance=enrichment.relevance,
                concerns=enrichment.safety.concerns,
                source=enrichment.safety.source,
            )
            log.warning(
                "node_ai_safety_blocked",
                video_id=video_id,
                verdict=verdict,
                relevance=round(enrichment.relevance, 2),
                concerns=enrichment.safety.concerns,
            )
            return _advance_reject(state, video_id, record, audit, enrichment)

        audit.record(
            "ai_enrichment_ok", video_id=video_id,
            verdict=enrichment.safety.verdict,
            cost_usd=enrichment.cost_usd,
            skipped_reason=enrichment.skipped_reason,
        )
        return {
            "records": {video_id: record},
            "total_cost_usd": (state.get("total_cost_usd") or 0.0) + enrichment.cost_usd,
            "total_prompt_tokens": (state.get("total_prompt_tokens") or 0) + enrichment.prompt_tokens,
            "total_completion_tokens": (state.get("total_completion_tokens") or 0) + enrichment.completion_tokens,
        }
    except Exception as exc:
        log.error("node_ai_analyze_failed", video_id=video_id, error=str(exc))
        record["status"] = "ai_failed"
        record["error"] = f"ai_analyze_error: {exc}"
        audit.record("ai_analyze_failed", video_id=video_id, error=str(exc))
        return _advance_fail(state, video_id, record, str(exc), audit)


# ----------------------------------------------------------------------------
# Node: download
# ----------------------------------------------------------------------------
def download_node(
    state: PipelineState, *, settings: Settings, proxy_pool, audit: AuditLog,
) -> dict:
    urls = state.get("urls", [])
    idx = state.get("current_index", 0)
    if idx >= len(urls):
        return {}
    target = urls[idx]
    video_id = target["video_id"]
    log.info("node_download", video_id=video_id)

    record = (state.get("records") or {}).get(video_id)
    if not record or record.get("status") != "enriched":
        return {}

    downloads_dir = settings.pipeline.output_dir / "raw"
    try:
        result: DownloadResult = download_video(
            url=target["url"],
            video_id=video_id,
            output_dir=downloads_dir,
            yt_settings=settings.youtube,
            safety_settings=settings.safety,
            proxy_pool=proxy_pool,
        )
        blocked_dl = [a for a in result.attempt_log if a.get("kind") == "blocked"]
        if blocked_dl:
            audit.record(
                "download_blocked_attempts",
                video_id=video_id,
                blocked=len(blocked_dl),
                routes=[a["proxy"] for a in blocked_dl],
            )

        record["download"] = {
            "attempt_log": result.attempt_log,
            "success": result.success,
            "file_path": str(result.file_path) if result.file_path else None,
            "attempts": result.attempts,
            "last_error": result.last_error,
            "file_size_bytes": result.file_size_bytes,
            "proxy_used": result.proxy_used,
            "client_used": result.client_used,
            "elapsed_sec": result.elapsed_sec,
        }
        if result.success:
            record["status"] = "downloaded"
            audit.record(
                "download_ok", video_id=video_id,
                attempts=result.attempts, size=result.file_size_bytes,
                proxy=result.proxy_used, client=result.client_used,
            )
            return {"records": {video_id: record}, "consecutive_failures": 0}
        else:
            record["status"] = "download_failed"
            record["error"] = f"download_failed: {result.last_error}"
            audit.record("download_failed", video_id=video_id, error=result.last_error, attempts=result.attempts)
            return _advance_fail(state, video_id, record, result.last_error, audit)
    except Exception as exc:
        log.error("node_download_unexpected", video_id=video_id, error=str(exc))
        record["status"] = "download_failed"
        record["error"] = f"download_exception: {exc}"
        audit.record("download_exception", video_id=video_id, error=str(exc))
        return _advance_fail(state, video_id, record, str(exc), audit)


# ----------------------------------------------------------------------------
# Node: transform
# ----------------------------------------------------------------------------
def transform_node(state: PipelineState, *, settings: Settings, audit: AuditLog) -> dict:
    urls = state.get("urls", [])
    idx = state.get("current_index", 0)
    if idx >= len(urls):
        return {}
    target = urls[idx]
    video_id = target["video_id"]
    log.info("node_transform", video_id=video_id)

    record = (state.get("records") or {}).get(video_id)
    if not record or record.get("status") != "downloaded":
        return {}

    raw = settings.pipeline.output_dir / "raw" / f"{video_id}.mp4"
    transformed_dir = settings.pipeline.output_dir / "transformed"
    out_path = transformed_dir / f"{video_id}.mp4"

    # Brand config
    from pipeline.ai_analyzer import _load_brand_config
    brand_cfg = _load_brand_config(state.get("brand") or settings.pipeline.brand, settings.project_root)
    branding = brand_cfg.get("branding", {})

    intro_p = Path(branding["intro_path"]) if branding.get("intro_path") and Path(branding["intro_path"]).exists() else None
    outro_p = Path(branding["outro_path"]) if branding.get("outro_path") and Path(branding["outro_path"]).exists() else None

    try:
        result: TransformResult = transform_video(
            input_path=raw,
            output_path=out_path,
            intro_path=intro_p,
            outro_path=outro_p,
            # A watermark identifies the brand publishing the video. In
            # general mode there is no such brand, so stamping one on would be
            # meaningless at best and misleading at worst.
            watermark_text=(
                branding.get("watermark_text", "")
                if settings.pipeline.enable_watermark else ""
            ),
            watermark_position=branding.get("watermark_position", "bottom-right"),
        )
        record["transform"] = {
            "success": result.success,
            "file_path": str(result.output_path) if result.output_path else None,
            "file_size_bytes": result.file_size_bytes,
            "duration_sec": result.duration_sec,
            "elapsed_sec": result.elapsed_sec,
            "error": result.error,
        }
        if result.success:
            record["status"] = "transformed"
            audit.record(
                "transform_ok", video_id=video_id,
                size=result.file_size_bytes, duration=result.duration_sec,
            )
            return {"records": {video_id: record}}
        else:
            record["status"] = "transform_failed"
            record["error"] = f"transform_failed: {result.error}"
            audit.record("transform_failed", video_id=video_id, error=result.error)
            return _advance_fail(state, video_id, record, result.error, audit)
    except Exception as exc:
        log.error("node_transform_unexpected", video_id=video_id, error=str(exc))
        record["status"] = "transform_failed"
        record["error"] = f"transform_exception: {exc}"
        audit.record("transform_exception", video_id=video_id, error=str(exc))
        return _advance_fail(state, video_id, record, str(exc), audit)


# ----------------------------------------------------------------------------
# Node: publish
# ----------------------------------------------------------------------------
def publish_node(state: PipelineState, *, settings: Settings, primary_publisher, audit: AuditLog) -> dict:
    urls = state.get("urls", [])
    idx = state.get("current_index", 0)
    if idx >= len(urls):
        return {}
    target = urls[idx]
    video_id = target["video_id"]
    log.info("node_publish", video_id=video_id)

    record = (state.get("records") or {}).get(video_id)
    if not record or record.get("status") != "transformed":
        return {}

    # Rebuild the enrichment for the publisher. Uses the dataclass's own
    # round-trip rather than copying fields by hand - the hand-rolled version
    # silently dropped relevance, summary and skipped_reason from every
    # published manifest.
    from pipeline.ai_analyzer import AIEnrichment

    enrichment = AIEnrichment.from_dict(record.get("enrichment") or {}, video_id)

    file_path = Path(record["transform"]["file_path"])
    try:
        result = primary_publisher.publish(video_id, file_path, enrichment)
        record["publish"] = {
            "success": result.success,
            "destination": result.destination,
            "remote_path": result.remote_path,
            "bytes_written": result.bytes_written,
            "error": result.error,
        }
        if result.success:
            record["status"] = "published"
            record["finished_at"] = _now_iso()
            audit.record("publish_ok", video_id=video_id, destination=result.destination, size=result.bytes_written)
            log.info("node_publish_ok", video_id=video_id, destination=result.destination)
            return {
                "records": {video_id: record},
                "successful_ids": (state.get("successful_ids") or []) + [video_id],
                "consecutive_failures": 0,
            }
        else:
            record["status"] = "publish_failed"
            record["error"] = f"publish_failed: {result.error}"
            record["finished_at"] = _now_iso()
            audit.record("publish_failed", video_id=video_id, error=result.error)
            return _advance_fail(state, video_id, record, result.error, audit)
    except Exception as exc:
        log.error("node_publish_unexpected", video_id=video_id, error=str(exc))
        record["status"] = "publish_failed"
        record["error"] = f"publish_exception: {exc}"
        record["finished_at"] = _now_iso()
        audit.record("publish_exception", video_id=video_id, error=str(exc))
        return _advance_fail(state, video_id, record, str(exc), audit)


# ----------------------------------------------------------------------------
# Node: advance (move to next URL)
# ----------------------------------------------------------------------------
def advance_node(state: PipelineState, *, settings: Settings, audit: AuditLog) -> dict:
    idx = state.get("current_index", 0) + 1
    log.info("node_advance", next_index=idx, total=len(state.get("urls") or []))
    return {"current_index": idx, "log": [f"advance to index {idx}"]}


# ----------------------------------------------------------------------------
# Conditional: should we continue?
# ----------------------------------------------------------------------------
def decide_next(state: PipelineState, *, settings: Settings) -> str:
    urls = state.get("urls") or []
    idx = state.get("current_index", 0)

    # Circuit breaker check
    consecutive = state.get("consecutive_failures", 0)
    if consecutive >= settings.safety.max_consecutive_failures:
        log.warning("circuit_breaker_trip", consecutive=consecutive)
        return END

    if idx >= len(urls):
        return END

    return "extract_metadata"


# ----------------------------------------------------------------------------
def _brand_policy(settings) -> dict:
    """Read the active brand's `policy` block, if any.

    Cached per brand name so the YAML is not re-read for every video.
    """
    from pipeline.ai_analyzer import _load_brand_config  # noqa: PLC0415

    try:
        cfg = _load_brand_config(settings.pipeline.brand, Path(__file__).resolve().parent.parent)
    except Exception:  # noqa: BLE001 - a missing policy must not stop the run
        return {}
    return cfg.get("policy") or {}


# Helper: handle skip / fail / reject
# ----------------------------------------------------------------------------
def _advance_skip(state: PipelineState, video_id: str, record: VideoRecord, reason: str, audit: AuditLog) -> dict:
    record["finished_at"] = _now_iso()
    audit.record("video_skipped", video_id=video_id, reason=reason)
    return {
        "records": {video_id: record},
        "skipped_ids": (state.get("skipped_ids") or []) + [video_id],
        "consecutive_failures": 0,
    }


def _advance_fail(state: PipelineState, video_id: str, record: VideoRecord, error: str, audit: AuditLog) -> dict:
    record["finished_at"] = _now_iso()
    audit.record("video_failed", video_id=video_id, error=error)
    return {
        "records": {video_id: record},
        "failed_ids": (state.get("failed_ids") or []) + [video_id],
        "consecutive_failures": (state.get("consecutive_failures") or 0) + 1,
    }


def _advance_reject(
    state: PipelineState, video_id: str, record: VideoRecord,
    audit: AuditLog, enrichment: AIEnrichment,
) -> dict:
    record["finished_at"] = _now_iso()
    audit.record(
        "video_rejected", video_id=video_id,
        verdict=enrichment.safety.verdict,
        confidence=enrichment.safety.confidence,
        concerns=enrichment.safety.concerns,
    )
    return {
        "records": {video_id: record},
        "rejected_ids": (state.get("rejected_ids") or []) + [video_id],
        "consecutive_failures": 0,
        # A rejected video still cost real tokens to analyse. Dropping that
        # spend here made the run report $0.00 for a run that had just paid
        # for five LLM round trips - and would have let a pathological input
        # set burn the budget invisibly.
        "total_cost_usd": (state.get("total_cost_usd") or 0.0) + enrichment.cost_usd,
    }
