# CSC Generation - AI Solutions Engineer Assessment

This repository contains my submission for the **AI Solutions Engineer** take-home
assessment at CSC Generation. The project is **Option 2: Video Pipeline Flow** —
a working automation that downloads short YouTube videos serially and re-publishes
them, with a hardened anti-bot downloader and a LangGraph orchestration agent on
top.

## What's in the box

| Path | What it is |
|---|---|
| `video-pipeline/` | The working pipeline (Python, LangGraph, yt-dlp, FFmpeg) |
| `video-pipeline/run.py` | Entry point: `python run.py --help` |
| `video-pipeline/outputs/` | Latest run artifacts (videos, manifests, audit log, HTML report) |
| `docs/` | Architecture, decisions, anti-bot strategy, scaling notes |
| `video-walkthrough/script.md` | Narration script for the 10-15 min video walkthrough |
| `ai-questionnaire/` | (Will be completed separately by the candidate) |

> **New here?** [`docs/INSTALL.md`](docs/INSTALL.md) has full setup for both
> technical users and non-technical stakeholders, plus how to switch on the
> brand gate. To host the sheet watcher permanently, see
> [`docs/WINDOWS_DEPLOYMENT.md`](docs/WINDOWS_DEPLOYMENT.md).

## Three ways to run it

| Interface | Who it is for |
|---|---|
| **Google Sheet** | The marketing team - paste URLs, results appear beside them. No terminal. |
| **CLI** | Engineers, cron jobs, CI |
| Setup wizard | First-time configuration |

```bash
# First run - asks brand vs general use, and where to publish
.venv/bin/python setup_wizard.py

# Create a ready-formatted sheet
.venv/bin/python run.py --create-sheet

# Watch it: paste a URL into column A and the pipeline starts on its own
.venv/bin/python watch.py --sheet <SHEET_ID> --publisher gdrive

# ...or run a batch manually
.venv/bin/python run.py --sheet <SHEET_ID> --publisher gdrive
```

With the watcher running, adding a URL to the sheet triggers the pipeline
within a few seconds - no scheduler, no terminal for the operator.

## Quickstart

Requires Python 3.11+ and `ffmpeg` on PATH.

**macOS / Linux**
```bash
cd video-pipeline
./setup.sh
.venv/bin/python run.py --max 5 --publisher local --brand generic
```

**Windows (PowerShell)**
```powershell
cd video-pipeline
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe run.py --max 5 --publisher local --brand generic
```

Run the tests with `.venv/bin/python -m pytest` (156 tests, no network needed).

Open `outputs/report.html` in a browser for a self-contained run summary.

### Two runs worth seeing

```bash
# 1. General archive channel - all five publish
.venv/bin/python run.py --max 5 --publisher local --brand generic

# 2. Same videos, a topical brand - all five are held for a human,
#    because none of them are actually Sur La Table content
.venv/bin/python run.py --max 5 --publisher local --brand surlatable
```

The second run is the interesting one: the pipeline refuses to publish
off-brand content rather than inventing plausible on-brand metadata for it.

## How the pipeline works

1. **Load URLs** — read `inputs/urls.txt`, validate each against an allowlist
2. **Extract metadata** — `yt-dlp --dump-json` (no download, fast canary)
3. **AI analyze** — LLM (Claude via MiniMax OpenAI-compatible gateway) generates
   brand-aligned metadata + brand-safety verdict; rules-based pre-filter catches
   the obvious cases without an LLM call
4. **Download** — `yt-dlp` with multi-client fallback, proxy rotation, and
   exponential backoff
5. **Transform** — FFmpeg re-encode, strip metadata, scale, optional watermark
6. **Publish** — write to local filesystem (default), S3/MinIO, or YouTube stub

Every action is recorded to a JSONL audit log. The final HTML report shows
per-video status, cost, proxy health, and the full audit trail.

## Five videos, end-to-end

The most recent run processed these five URLs serially and published all five:

| # | Video | Duration | Status |
|---|---|---|---|
| 1 | "Me at the zoo" (first YouTube video) | 19s | Published |
| 2 | Demo Background Sample Video | 18s | Published |
| 3 | YouTube sample | 2:05 | Published |
| 4 | Sea waves & beach drone (no copyright) | 3:22 | Published |
| 5 | Spring - Blender Open Movie (CC BY 3.0) | 7:44 | Published |

Total wall time: ~2-4 minutes (serial, with throttling between YouTube
requests). LLM cost with MiniMax M2.7: ~$0.014 per five-video run,
metered per video against a hard spend guard.

## What I'd demo in the video walkthrough

1. **Live demo** — show the full pipeline run, from URLs to published videos
2. **Setup walkthrough** — show the LangGraph graph, the safety modules, the
   publisher abstraction
3. **Decision rationale** — why this architecture, what I considered,
   what tradeoffs I made
4. **What I'd add with more time** — PO token server, residential proxies,
   YouTube Data API integration, evaluation harness, CI/CD

The script is in `video-walkthrough/script.md`.

## Why this is an "AI Solutions Engineer" submission, not just an automation

- **LangGraph orchestration agent** — the agent has state, memory, and makes
  decisions (e.g., should we abort the run after N consecutive failures?)
- **LLM-driven metadata + brand safety** — real AI judgment, not a hardcoded
  regex; rules layer is the safety net, LLM is the value-add
- **Defense-in-depth anti-bot** — 6 layers (player clients, cookies, proxies,
  PO tokens, jitter, format restriction)
- **Production-grade safety** — cost circuit breaker, disk check, per-IP
  rate limit, prompt-injection sanitization, audit log, idempotent re-runs
- **Cross-system integration** — yt-dlp, FFmpeg, LLM gateway, file system,
  S3-compatible storage, YouTube stub
- **Scales to 13 brands** — per-brand config (tone, watermark, prompts)
  means the same pipeline produces different output for Sur La Table vs.
  Backcountry

## What I would NOT show a hiring manager

- The single-emoji `print("OK")` debugging. I removed all of those.
- The first three drafts of the URL regex (see `git log` if you want the
  archaeology).
- The fact that during development I downloaded the same 5 videos 6 times
  because I forgot idempotency was already in place.

## License

This is a take-home submission; not licensed for redistribution.
