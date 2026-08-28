# Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            run.py (Entry Point)                          │
│   - Argparse, preflight checks, signal handler, summary panel           │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     LangGraph Orchestration Agent                       │
│                                                                          │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐                │
│  │  load_urls   │──▶│ extract_meta │──▶│  ai_analyze  │                │
│  │  (validates) │   │  (yt-dlp)    │   │  (LLM)       │                │
│  └──────────────┘   └──────────────┘   └──────┬───────┘                │
│                                                 │                        │
│                              ┌──────────────────▼───────────┐           │
│                              │      download                 │          │
│                              │  (yt-dlp + anti-bot)          │          │
│                              └──────────────────┬───────────┘           │
│                                                 │                        │
│                              ┌──────────────────▼───────────┐           │
│                              │      transform                │          │
│                              │  (FFmpeg)                     │          │
│                              └──────────────────┬───────────┘           │
│                                                 │                        │
│                              ┌──────────────────▼───────────┐           │
│                              │      publish                  │          │
│                              │  (Local / S3 / YouTube)       │          │
│                              └──────────────────┬───────────┘           │
│                                                 │                        │
│                              ┌──────────────────▼───────────┐           │
│                              │      advance                  │          │
│                              │  (next video or END)          │          │
│                              └──────────────────────────────┘           │
│                                                                          │
│  State: JSON-serializable TypedDict; LangGraph MemorySaver for resume.  │
└─────────────────────────────────────────────────────────────────────────┘
                                   │
        ┌──────────────────────────┼──────────────────────────┐
        ▼                          ▼                          ▼
┌───────────────┐         ┌────────────────┐         ┌────────────────┐
│   Safety      │         │   Publishers   │         │    Reports     │
│  (preflight,  │         │ (Local, S3,    │         │  (HTML +       │
│  cost guard,  │         │  YouTube)      │         │  audit.jsonl)  │
│  content,     │         │                │         │                │
│  proxy health,│         │                │         │                │
│  audit log)   │         │                │         │                │
└───────────────┘         └────────────────┘         └────────────────┘
```

## Key Design Decisions

### 1. LangGraph over plain Python
- **Why**: The JD calls out "stateful, dynamic AI systems with memory" and
  LangGraph specifically. LangGraph gives us:
  - Typed state schema (PipelineState)
  - Conditional branching (decide_next after each video)
  - Built-in checkpointing (MemorySaver) for resume
  - Visualization tools for the video walkthrough
- **Trade-off**: A 200-line script would be simpler for the linear flow. But
  the agent abstraction lets the pipeline add a planner node later (e.g.,
  re-rank URLs by priority) without restructuring.

### 2. Defense-in-depth anti-bot
The single biggest technical risk in this project. We layer:
1. **Multi-client player** (`--extractor-args "youtube:player_client=android,web,ios,tv_embedded"`)
   — different clients have different anti-bot thresholds
2. **PO Token plugin** (`bgutil-ytdlp-pot-provider`) — the current best
   workaround for YouTube's 2024+ bot detection
3. **Cookie-based auth** — optional but dramatically increases headroom
4. **Proxy rotation** — Webshare free tier (10 datacenter) for demo,
   residential (Bright Data / Smartproxy) for production
5. **Exponential backoff + jitter** — random 2-8s between attempts
6. **Format restriction** — only request ≤1080p, mp4/m4a (avoid signature
   deciphering triggers)

### 3. Rules + LLM for content safety
- **Why both**: Rules are deterministic, fast, and free. They catch the
  obvious cases (NSFW keywords, known copyright brands) without spending
  LLM budget. LLM adds nuanced judgment (tone, context, brand fit).
- **Merge logic**: Rules reject wins always. LLM reject beats rules
  silent. LLM review escalates rules review. Both safe → safe.
- **Cost**: ~$0.001-0.005 per video, capped at $1.50/run.

### 4. Publisher abstraction
- **Why**: a multi-brand operator has several destinations (CDN, social, internal
  archive). The Publisher interface decouples "what to publish" from
  "where to publish." Local is always-works; S3/MinIO is for production;
  YouTube is a documented extension.
- **YouTube stub**: Re-uploading another creator's content without
  transformation rights violates YouTube TOS. The stub documents the
  OAuth + compliance path without shipping code that would be misused.

### 5. Graceful degradation
- No LLM key → pipeline runs with original metadata + rules-only safety
- No proxies → falls back to direct connection + multi-client rotation
- No cookies → higher bot-detection risk, but works
- 1 of 5 videos fails → pipeline continues, audit log captures the failure

## Scaling Across Many Brands

The pipeline is designed to be re-run per brand (or per content batch) with
different configs. The config system (`configs/brands/*.yaml`) controls:

- **Tone** for AI metadata generation (cozy, authoritative, playful...)
- **Brand watermark** text and position
- **Intro/outro** video paths
- **Hard-reject categories** (e.g., alcohol for kids' brand, gambling for
  premium retailer)
- **System prompts** customized per brand voice

A real deployment would:
1. Run the pipeline nightly across all many brands' source channels
2. Bucket output by brand in S3 (`s3://republished/{brand}/{video_id}.mp4`)
3. Route the JSON manifests to each brand's social media team for review
4. Auto-publish videos that pass `safety.verdict == "safe"` with confidence
   ≥ 0.8 (configurable threshold)
5. Queue `review` verdicts for human approval in a Slack/Linear workflow

## Observability

Every run produces:
- `outputs/audit.jsonl` — structured event log (every action, every retry,
  every safety verdict)
- `outputs/final_state.json` — complete agent state
- `outputs/report.html` — self-contained HTML report (dark theme, no
  external dependencies, opens from `file://`)
- `outputs/logs/pipeline.log` — full text log
- Per-video manifests in `outputs/published/manifests/{video_id}.json`

## Failure Modes & Mitigations

| Failure | Mitigation |
|---|---|
| YouTube rate-limits our IP | Proxy rotation, exponential backoff, circuit breaker after 3 consecutive failures |
| LLM API down | Graceful bypass, run completes with original metadata |
| LLM budget exhausted | Cost guard raises, pipeline continues without LLM |
| YouTube blocks all our clients | Documented path to PO token server, residential proxies, cookie auth |
| Disk fills up | Preflight disk check (2 GB min); refuse to start |
| Pipeline crashes mid-run | LangGraph checkpoint to `outputs/checkpoint.json`; resume with `--checkpoint` |
| Bad URL (typo, malicious) | Input validator rejects non-YouTube, non-allowlist paths |
| Copyrighted content | Rules layer flags known IP brands; LLM reviews for context |
| Watermark text breaks ffmpeg | Test drawtext first; skip gracefully if it fails |
