# Video Pipeline

Ingests YouTube videos, has an LLM summarise and score each one against a
brand profile, transforms them with ffmpeg, and publishes the result to
Google Drive. Anything that does not fit the brand is held for human review
rather than published.

Two interfaces: a Google Sheet for non-technical operators, and a CLI for
engineers and scheduled runs.

```bash
cd video-pipeline
./setup.sh                  # setup.ps1 on Windows
.venv/bin/python -m pytest  # 184 tests, no network, no credentials
.venv/bin/python run.py --max 5 --publisher local --brand generic
```

That last command works with no API keys at all. Without an LLM key the AI
stages are skipped and metadata falls back to YouTube's own — the run still
produces real video files, and reports that it degraded.

Full setup, including Windows and the Google APIs, is in
[`docs/INSTALL.md`](docs/INSTALL.md).

---

## How it works

```
   Google Sheet  ──┐                          ┌──►  Google Drive
   (paste a URL)   │                          │
                   ▼                          │
              ┌─────────────┐                 │
   watch.py ─►│  run.py     │                 │
              └──────┬──────┘                 │
                     ▼                        │
        ┌────────────────────────────┐        │
        │   LangGraph state machine  │        │
        │   load_urls                │        │
        │       ▼                    │        │
        │   extract_metadata ────────┼────────┼──►  YouTube
        │       ▼                    │        │     (metadata +
        │   ai_analyze ──────────────┼────────┼──►   captions,
        │       ▼                    │        │      no download yet)
        │   download ────────────────┼────────┘
        │       ▼                    │
        │   transform  (ffmpeg)      │
        │       ▼                    │
        │   publish ─────────────────┼──►  local │ gdrive │ s3 │ youtube
        │       ▼                    │
        │   advance ──┐              │
        │      ▲      │ next video   │
        │      └──────┘              │
        └────────────────────────────┘
                     │
                     ▼
        results written back to the sheet
```

Metadata and captions are fetched before anything is downloaded, so a video
can be judged — and rejected — for the cost of a few kilobytes rather than a
few hundred megabytes.

## The review gate

Two questions are scored separately: whether content is **harmful**, and
whether it is **relevant** to the brand. Collapsing them produces confident
nonsense — an early version decided a zoo video was safe, which it was, and
published it under a cookware profile with the AI-generated title *"Cooking
Basics: Getting Started in the Kitchen"*.

Relevance now has its own score and a per-brand threshold. Anything below it
is held for a person to look at.

```bash
run.py --max 5 --brand generic      # publishes broadly
run.py --max 5 --brand kitchenware  # holds anything off-topic
```

Naming a brand switches the gate on. Brand profiles live in
`configs/brands/*.yaml` — tone, audience, watermark, relevance threshold and
prompts. Adding one is a single file with no code change.

## Resilience

Both network stages share a fallback ladder: rotate proxies, fall back to a
direct connection, rotate player clients, with jittered backoff. Failures are
classified rather than retried blindly — a *blocked* request is worth another
route, a *removed* video never will be, so it fails immediately instead of
consuming the whole ladder.

LangGraph keeps an in-process checkpoint while a run is active, and the final
state is written to JSON for inspection or an explicit later resume. Uploads
are idempotent: re-publishing a video updates the existing file instead of
creating a second copy, even if its title changed.

## The sheet interface

```bash
run.py --create-sheet                              # creates a formatted sheet
watch.py --sheet <SHEET_ID> --publisher gdrive     # process URLs as they arrive
```

With the watcher running, pasting a URL into column A starts the pipeline
within about ten seconds. Results are written back beside it: status, title,
summary, Drive link, relevance, cost, and the reason anything was held.

It polls rather than receiving push notifications — Google delivers those to a
public HTTPS endpoint, which a workstation is not.

## What it reads

Title, description, duration, channel, upload date, view and like counts,
tags, categories, thumbnail, resolution, frame rate, chapter count, age
restriction, and the **licence** field reporting whether the uploader marked
the video Creative Commons.

Then the captions, so the LLM can summarise what is actually said. Videos
without captions get a summary from metadata only, and the record says which —
a thin summary is never presented as a confident one.

## Publishing

Google Drive is the default target. Uploading to a Drive folder is an internal
file operation; re-publishing third-party video to a public channel is a
rights question rather than an engineering one. YouTube's Terms of Service
prohibit automated downloading even for Creative Commons material — the
content licence and the platform terms are separate things.

The YouTube publisher is implemented in full (resumable upload, retry on 429
and 5xx, private by default) and available for content a brand owns outright.

Output is organised by brand and run date:

```
VideoPipeline/<brand>/2026-08-27/
    Me at the Zoo - YouTube's First Video [jNQXAC9IVRw].mp4
    Me at the Zoo - YouTube's First Video [jNQXAC9IVRw].json
```

The bracketed video id keeps deduplication exact while the title keeps the
folder readable.

## Stack

yt-dlp, ffmpeg, LangGraph, an OpenAI-compatible LLM client (MiniMax, Anthropic
or OpenAI), Google Drive API v3 with the `drive.file` scope, Google Sheets API
v4, boto3, YouTube Data API v3, structlog for JSONL audit output.

## Documentation

| | |
|---|---|
| [`docs/INSTALL.md`](docs/INSTALL.md) | Setup for both technical and non-technical users |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | How the pieces fit together |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Design choices and the reasoning behind them |
| [`docs/ANTI_BOT.md`](docs/ANTI_BOT.md) | Handling YouTube blocking |
| [`docs/SCALING.md`](docs/SCALING.md) | Running across many brands |
| [`docs/WINDOWS_DEPLOYMENT.md`](docs/WINDOWS_DEPLOYMENT.md) | Hosting the watcher on an always-on machine |
| [`docs/FIXES.md`](docs/FIXES.md) | Defect log with before/after evidence |

## Security

`.env`, `inputs/client_secret.json`, `inputs/*token*.json` and
`inputs/cookies.txt` are gitignored. A cached OAuth token is a live credential
— never commit one. Revoke access at
[myaccount.google.com/permissions](https://myaccount.google.com/permissions).
