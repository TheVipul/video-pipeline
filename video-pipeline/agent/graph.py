"""
LangGraph graph wiring.

Wires the nodes into a serial per-video pipeline:

    load_urls -> extract_metadata -> ai_analyze -> download -> transform -> publish -> advance
                                                                                  |
                                                                                  v
                                                                              decide_next
                                                                              /        \
                                                                          END     extract_metadata
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from agent import nodes
from agent.state import PipelineState
from config import Settings
from logging_setup import get_logger
from safety.audit_log import AuditLog

log = get_logger(__name__)
from safety.cost_guard import CostGuard
from safety.proxy_health import ProxyPool


def build_graph(
    settings: Settings,
    urls_file: Path,
    audit: AuditLog,
    cost_guard: CostGuard,
    proxy_pool: ProxyPool,
    primary_publisher,
    checkpointer: Optional[object] = None,
):
    """Build and compile the LangGraph pipeline."""

    graph = StateGraph(PipelineState)

    # Partial-applied node functions
    def _load_urls(s): return nodes.load_urls_node(
        s, settings=settings, urls_file=urls_file, audit=audit
    )
    def _extract(s): return nodes.extract_metadata_node(
        s, settings=settings, proxy_pool=proxy_pool, audit=audit
    )
    def _ai(s): return nodes.ai_analyze_node(
        s, settings=settings, cost_guard=cost_guard, audit=audit
    )
    def _download(s): return nodes.download_node(
        s, settings=settings, proxy_pool=proxy_pool, audit=audit
    )
    def _transform(s): return nodes.transform_node(s, settings=settings, audit=audit)
    def _publish(s): return nodes.publish_node(
        s, settings=settings, primary_publisher=primary_publisher, audit=audit
    )
    def _advance(s): return nodes.advance_node(s, settings=settings, audit=audit)
    def _decide(s): return nodes.decide_next(s, settings=settings)

    graph.add_node("load_urls", _load_urls)
    graph.add_node("extract_metadata", _extract)
    graph.add_node("ai_analyze", _ai)
    graph.add_node("download", _download)
    graph.add_node("transform", _transform)
    graph.add_node("publish", _publish)
    graph.add_node("advance", _advance)

    graph.set_entry_point("load_urls")
    graph.add_edge("load_urls", "extract_metadata")
    graph.add_edge("extract_metadata", "ai_analyze")
    graph.add_edge("ai_analyze", "download")
    graph.add_edge("download", "transform")
    graph.add_edge("transform", "publish")
    graph.add_edge("publish", "advance")

    graph.add_conditional_edges(
        "advance",
        _decide,
        {END: END, "extract_metadata": "extract_metadata"},
    )

    if checkpointer is None:
        checkpointer = MemorySaver()

    compiled = graph.compile(checkpointer=checkpointer)
    return compiled


def run_pipeline(
    settings: Settings,
    urls_file: Path,
    audit: AuditLog,
    cost_guard: CostGuard,
    proxy_pool: ProxyPool,
    primary_publisher,
    resume_state_file: Optional[Path] = None,
) -> PipelineState:
    """Run the full pipeline and return the final state."""

    graph = build_graph(
        settings=settings, urls_file=urls_file, audit=audit,
        cost_guard=cost_guard, proxy_pool=proxy_pool,
        primary_publisher=primary_publisher,
    )

    initial: PipelineState = {
        "brand": settings.pipeline.brand,
        "max_videos": settings.pipeline.max_videos,
        "dry_run": settings.pipeline.dry_run,
    }
    if resume_state_file:
        if not resume_state_file.exists():
            raise FileNotFoundError(
                f"--checkpoint {resume_state_file} does not exist"
            )
        try:
            saved = json.loads(resume_state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # Silently starting from scratch on a corrupt checkpoint would
            # re-download and re-pay for everything the operator was trying
            # to resume past.
            raise ValueError(
                f"--checkpoint {resume_state_file} is not readable JSON: {exc}"
            ) from exc
        initial.update(saved)
        log.info(
            "resumed_from_checkpoint",
            file=str(resume_state_file),
            records=len(saved.get("records") or {}),
            resuming_at_index=saved.get("current_index", 0),
        )

    config = {"configurable": {"thread_id": "csc-video-pipeline-001"}}
    final_state = graph.invoke(initial, config=config)
    return final_state
