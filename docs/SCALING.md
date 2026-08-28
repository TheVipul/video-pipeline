# Scaling Across Many Brands

A multi-brand operator running a dozen or more properties is the case this
pipeline is built for. A handful of videos is the proof of concept; the
production target is many brands, each processing videos daily.

This document describes how the architecture supports that scale.

## The Multi-Brand Problem

Each brand has:
- A different voice (Kitchenware Co = warm, Outdoor Gear Co = adventurous)
- Different content policies (Kitchenware Co rejects alcohol; Outdoor Gear Co
  welcomes outdoor adventure)
- Different publishing destinations (Instagram, TikTok, YouTube, owned
  site)
- Different team workflows (Kitchenware Co has a marketing coordinator;
  Outdoor Gear Co is fully self-serve)

A single hardcoded pipeline serves none of them well.

## The Brand Config System

`configs/brands/{brand}.yaml` is the per-brand contract. Each file has:

```yaml
brand:
  name: "Kitchenware Co"
  tone: "cozy"
  audience: "home cooks"
  keywords: [...]

content_policy:
  hard_reject_categories: ["alcohol-excessive"]
  soft_flag_categories: ["meat-heavy"]
  min_confidence: 0.6

branding:
  watermark_text: "kitchenware.example"
  watermark_position: "bottom-right"
  intro_path: "assets/intros/kitchenware_intro.mp4"
  outro_path: "assets/outros/kitchenware_outro.mp4"

ai_prompts:
  metadata_system: |
    You are a content strategist for Kitchenware Co...
  brand_safety_system: |
    You are a brand safety classifier for Kitchenware Co...
```

The pipeline reads the active config based on `--brand {name}` (or
`PIPELINE_BRAND` env var). No code change required to add a new brand —
just a new YAML file.

## Per-Brand Workflow

For each brand, a daily/weekly run looks like:

```
1. Brand team (or automation) populates inputs/urls_{brand}.txt
   with the videos they want re-published.
2. CI/CD or cron triggers:
   python run.py --brand kitchenware --urls inputs/urls_kitchenware.txt
3. Pipeline:
   - Validates URLs
   - For each: extract → analyze → download → transform → publish
   - Brand-specific watermark, intro, outro
   - Brand-specific AI tone for metadata
   - Brand-specific safety rules
4. Outputs to outputs/{brand}/{date}/...
5. Manifests sent to brand's content review queue
6. Auto-publish for safety.verdict == "safe" + confidence >= threshold
7. Hold for review for safety.verdict == "review"
8. Reject (no publish) for safety.verdict == "reject"
```

## Storage Layout

Production S3 layout (illustrative):

```
s3://csc-republished/
├── kitchenware/
│   ├── 2026-08-26/
│   │   ├── videos/{video_id}.mp4
│   │   └── manifests/{video_id}.json
│   ├── 2026-08-27/
│   │   └── ...
├── outdoor_gear/
│   ├── 2026-08-26/
│   │   └── ...
└── ...
```

The `LocalPublisher` already organizes by `outputs/published/`. The
`S3Publisher` just needs the brand name in the prefix:
`prefix = "republished/{brand}/{date}/"`.

## Concurrent Execution

For many brands, sequential execution is too slow. Options:

1. **Per-brand workers** — 13 separate processes/cron jobs, each
   handling one brand's daily run. Simple, isolated, easy to debug.
2. **Multiprocessing pool** — one orchestrator process spawns one
   worker per brand, waits for all to finish. 5-10x speedup with no
   shared state.
3. **Celery / RQ / Temporal** — full job queue. Overkill for many brands
   but justified at 100+.
4. **LangGraph as the per-brand orchestrator** — same agent code,
   different config, different queue slot.

The current code is a single-process orchestrator. Adding multiprocessing
is a 30-line change (`multiprocessing.Pool` over brands).

## Failure Isolation

A failure in one brand must not affect the others. The current agent
state is per-run; per-brand runs have isolated state. If a brand run
crashes, only that brand's videos are lost; the others complete.

For added safety:
- Wrap each brand run in a try/except in the orchestrator
- Log brand-level failures to a separate alert channel
- Quarantine bad URLs in `inputs/quarantine/{brand}.txt` for human review

## Cost Estimation (production)

Per 13-brand run, assuming 20 videos per brand per day = 260 videos/day:

| Component | Cost per video | Daily cost | Monthly cost |
|---|---|---|---|
| LLM (Claude 3.5 Sonnet) | $0.005 | $1.30 | $39 |
| Residential proxies | $0.02 | $5.20 | $156 |
| S3 storage (1 GB avg) | $0.023/GB | $6.00 | $180 |
| S3 PUT requests | $0.005/1k | $0.00 | $1 |
| Compute (1 small VM) | — | $5.00 | $150 |
| **Total** | | **~$18/day** | **~$525/month** |

Order of magnitude: $500-1000/month for full portfolio coverage.

## Observability

For many brands running daily, you need:

- **Per-brand dashboard** — count of videos processed, success rate,
  cost, average safety verdict
- **Anomaly alerts** — if a brand's success rate drops >20% day-over-day,
  or LLM cost spikes, or proxy health degrades
- **Audit log aggregation** — ship all `audit.jsonl` files to a central
  log store (CloudWatch, Datadog, ELK)
- **Per-brand SLA** — each brand has a target turnaround time (e.g.,
  24h from URL ingestion to publish)

The `report.html` template is per-run today; aggregating to a portfolio
view is a 1-page Jinja template that loops over all brands.

## What's NOT in the current build (deferred)

| Feature | Why deferred |
|---|---|
| Web UI for brand teams to upload URLs | CLI input is fine for the assessment; a Streamlit/Next.js UI is a separate project |
| Auto-publish to TikTok / Instagram | Each platform requires business verification + API approval; documented in publisher stubs |
| A/B testing different AI prompts | Once you have 1 brand live, the A/B harness is a 50-line add |
| Feedback loop from brand team → LLM prompts | Needs a UI + database; not relevant to the assessment |
| Cost attribution per brand | Trivial to add: pass `brand` to `CostGuard.record()` |

## What I'd demo to a hiring manager in the 13-brand framing

1. "Here's the same pipeline running for Kitchenware Co and Outdoor Gear Co
   side by side — see how the watermark and tone change automatically."
2. "The same logic, plus a S3 publisher, runs across all many brands
   daily, with a per-brand circuit breaker."
3. "The cost is ~$500/month for full portfolio coverage — cheaper than
   one human content coordinator."
4. "Every video has a tamper-evident audit trail. If a brand team
   questions why something was published, we have the full record."

That's the production story. The 5-video demo is the floor.
