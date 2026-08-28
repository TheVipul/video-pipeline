# Video Pipeline — Project Submission

**Option 2: Video Pipeline Flow**
Vipul Sharma · AI Solutions Engineer assessment

---

## What it does

You give it YouTube URLs. For each one, in order, it reads the video's
details, pulls the captions so an LLM can summarise what the video actually
contains, scores how well it fits your brand, downloads it, re-encodes it,
and uploads the result to Google Drive. Anything that doesn't fit the brand is
held for a person to look at instead of being published.

There are two ways to use it. Marketers use a Google Sheet and never open a
terminal. Engineers use the command line.

**A live example sheet is linked in the covering email.** It holds six videos
from a real run, each with a summary, a Drive link, a relevance score and what
it cost. You can paste a URL into column A and try it yourself.

---

## For non-technical users: the spreadsheet

Nothing to install.

1. Open the sheet
2. Paste a YouTube URL into the next blank row of column A
3. Wait about a minute

A watcher notices the new row within ten seconds and starts work. When it
finishes, the row fills in:

| Column | Meaning |
|---|---|
| Status | Published, Held for review, Skipped, or which stage failed |
| Title | An AI-written title |
| Summary | What the video contains, from its captions where available |
| Published Link | The finished file in Google Drive |
| Relevance | 0.0–1.0, how well it fits the brand |
| Cost | What that video cost to process |
| Notes | Why it was held, or what went wrong |

Rows that already have a status are skipped, so nothing gets processed twice.

---

## For technical users: install

**You need:** Python 3.11, ffmpeg, about 2 GB of free disk.

```bash
git clone <repo-url>
cd youtube-pipeline/video-pipeline
./setup.sh                       # setup.ps1 on Windows
.venv/bin/python -m pytest       # 156 tests, no network, no credentials
```

Then run it:

```bash
.venv/bin/python run.py --max 5 --publisher local --brand generic
```

That works with no credentials at all. Without an API key the AI stages are
skipped, metadata falls back to YouTube's own, and the run still produces
real video files. It tells you in the output that it degraded.

Full instructions, including the Windows path, are in `docs/INSTALL.md`.

---

## What you need to wire up

Everything below is optional and independent.

**An LLM key** turns on summaries, AI titles and relevance scoring. Put a
MiniMax key in `.env` as `LLM_API_KEY`. Costs about $0.003 per video. Anthropic
and OpenAI also work — change the base URL and model name.

**Google credentials** enable Drive upload and the spreadsheet. Create a
project at console.cloud.google.com, enable the Drive and Sheets APIs, add
yourself as a test user on the OAuth consent screen, create a Desktop OAuth
client, and save the JSON to `inputs/client_secret.json`. The first Drive
command opens a browser once. The app only requests `drive.file`, which gives
it access to files it creates and nothing else in your Drive.

Two things to know: you must add yourself as a test user or consent fails with
a 403, and tokens for unverified apps expire after seven days.

**Cookies and proxies** are only needed if YouTube starts rate-limiting you.

---

## What it reads from each video

Fetched from YouTube before anything is downloaded:

Title, description, duration, channel, uploader, upload date, view count,
like count, comment count, tags, categories, thumbnail URL, resolution, frame
rate, language, chapter count, age restriction, availability, and whether
captions exist and in which languages.

It also reads the **licence** field, which reports whether the uploader marked
the video Creative Commons. That matters for deciding whether reuse is
defensible, and it's recorded on every video.

Then it fetches the captions — a few kilobytes, no video download — and an LLM
produces:

A summary of what the video actually contains, a rewritten title, a
description, tags, a category, a relevance score against the brand, a
brand-safety verdict, and the reasoning behind both. Every record also stores
which model ran, the token counts and the exact cost.

Each summary records where it came from. Videos with captions get a summary of
what is actually said. Silent footage — drone shots, animation — has no
captions, so the summary is based on the description and says so. A thin
summary is never presented as a confident one.

---

## Turning on the brand gate

Without it, the pipeline publishes anything that isn't harmful. With it, the
pipeline also asks whether the content belongs on that brand's channel, and
holds anything that doesn't.

This matters more than it sounds. An earlier version of this published a video
of a man at a zoo under a cookware brand, with the AI-generated title
"Cooking Basics: Getting Started in the Kitchen". The safety classifier had
been asked whether the content was harmful — it wasn't — and nobody was asking
whether it was relevant. Those are different questions, and they're scored
separately now.

To enable it:

```bash
cp configs/brands/surlatable.yaml configs/brands/yourbrand.yaml
```

Edit the file to set the brand name, audience, tone, watermark text and the
`min_relevance` threshold. Then run with `--brand yourbrand`. Naming a brand
switches the gate on automatically; no other flag is needed. Lower the
threshold to publish more, raise it to be stricter.

`--brand generic` returns to publishing everything.

---

## Where the videos go

```
VideoPipeline/
  <brand>/
    2026-08-27/
      Me at the Zoo - YouTube's First Video [jNQXAC9IVRw].mp4
      Me at the Zoo - YouTube's First Video [jNQXAC9IVRw].json
```

Grouped by brand and run date. The video ID in brackets keeps re-publishing
exact — a reworded title updates the existing file rather than creating a
second copy. The JSON alongside each video records why it was published and
what the AI concluded.

---

## Configured limits

Three guards are set by default and are all configurable: videos over ten
minutes are skipped, only YouTube links are accepted, and a run processes five
videos unless told otherwise. They exist to bound cost and runtime on an
unattended job.

**Why Drive is the default target.** This was a deliberate decision, made
after reading YouTube's Terms of Service rather than assuming. Those terms
prohibit downloading through automated tools, and that holds even for
Creative Commons videos — the content licence and the platform terms are
separate things, and YouTube's own documentation states it cannot grant those
rights.

Uploading to a Drive folder is an internal file operation with none of those
implications. Re-publishing third-party video to a public channel is a rights
question rather than an engineering one, so the pipeline defaults to the path
that is defensible and keeps the YouTube publisher available for content a
brand owns outright. It is implemented in full — resumable upload, retry on
429 and 5xx, private by default — and enabling it is a configuration change.

---

## Architecture

```
   Google Sheet  ──┐                          ┌──►  Google Drive
   (paste a URL)   │                          │     (finished video
                   ▼                          │      + manifest)
              ┌─────────────┐                 │
   watch.py ─►│  run.py     │                 │
   (polls,    │  CLI entry  │                 │
    fires on  └──────┬──────┘                 │
    new rows)        │                        │
                     ▼                        │
        ┌────────────────────────────┐        │
        │   LangGraph state machine  │        │
        │                            │        │
        │   load_urls                │        │
        │       ▼                    │        │
        │   extract_metadata ────────┼────────┼──►  YouTube
        │       ▼                    │        │     (metadata +
        │   ai_analyze ──────────────┼────────┼──►   captions, no
        │       ▼                    │        │      download yet)
        │   download ────────────────┼────────┘
        │       ▼                    │
        │   transform  (ffmpeg)      │
        │       ▼                    │
        │   publish ─────────────────┼──►  local │ gdrive │ s3 │ youtube
        │       ▼                    │
        │   advance ──┐              │
        │      ▲      │              │
        │      └──────┘ next video   │
        └────────────────────────────┘
                     │
                     ▼
        results written back to the sheet
```

**Seven nodes, run serially per video.** `advance` loops back for the next
URL, or stops when the list is exhausted or the circuit breaker trips after
repeated failures.

The ordering is deliberate. Metadata and captions are fetched first, costing a
few kilobytes, so the LLM can judge a video before any bandwidth is spent
downloading it. A video that does not fit the brand is held at `ai_analyze`
and never reaches `download`.

**Supporting modules**, each independently testable:

| Module | Responsibility |
|---|---|
| `pipeline/metadata.py` | Video details and captions, with a proxy and player-client fallback ladder |
| `pipeline/ai_analyzer.py` | LLM summary, relevance score, safety verdict, cost accounting |
| `pipeline/downloader.py` | Acquisition, sharing the same fallback ladder |
| `pipeline/transformer.py` | ffmpeg re-encode, metadata strip, optional watermark |
| `pipeline/publishers/` | One interface, four destinations |
| `pipeline/sheets.py` | Read pending rows, write results back |
| `safety/` | URL allowlist, spend guard, content rules, proxy health, audit log |

**State** is a typed dictionary checkpointed after every node, so a run can be
resumed rather than restarted. Every action is appended to a JSONL audit log,
and each run writes a manifest recording the script, the AI verdict, which
route each network call took, and what it cost.

**Configuration** lives in two places by design. `.env` holds machine
concerns — keys, paths, limits. `configs/brands/*.yaml` holds editorial
concerns — tone, audience, watermark, relevance threshold, prompts. A marketer
edits the second without touching the first, which is what makes onboarding a
brand a single file.

---

## Against the evaluation criteria

Taking each dimension from the assessment packet in turn.

**Working end-to-end automation**

It runs, and it runs without credentials. `./setup.sh` then
`.venv/bin/python -m pytest` gives 156 passing tests with no network and no
API keys, and `run.py --max 5 --publisher local` produces real MP4 files on a
clean checkout. With credentials, the last full run published six videos to
Google Drive and wrote six rows back to a spreadsheet for $0.0145 total. The
shared sheet shared in the covering email is output from that run, not a
screenshot.

The Drive upload, the spreadsheet interface, the retry behaviour and the brand
gate have each been exercised end to end against live APIs, not mocked.

**Error handling**

Failures are classified rather than retried blindly. yt-dlp errors are sorted
into blocked, permanent, or unknown: a block is worth trying on another route,
a removed video never will be, so it fails immediately instead of consuming
the whole retry ladder. Both network stages walk the same fallback ladder —
rotate proxies, fall back to a direct connection, rotate player clients, with
jittered backoff between attempts. Pointing it at two dead proxies still
publishes, because it degrades to direct.

Authentication failures fail fast and name the environment variable rather
than retrying into a rate limit. Google upload retries on 429 and 5xx with
exponential backoff. If the LLM is unreachable the run continues on
rule-based checks; if captions are missing the summary falls back to metadata
and says so. Every degradation is recorded in the run manifest — the failure
mode I was most concerned about was a run reporting success while silently
doing less, so degradations are reported rather than swallowed.

**Scalability**

Onboarding a new brand is one YAML file with no code change and no redeploy.
Spend is capped per run. Publishing is an interface with four implementations,
so adding a destination does not touch the pipeline. Runs resume from a
checkpoint rather than restarting, and uploads are idempotent, so re-running
cannot duplicate work or double-charge.

Videos are processed serially by design. Concurrent downloads from a single IP
is the fastest way to trigger YouTube's bot detection, so throughput is scaled
by adding brands and machines rather than by parallelising within one queue —
which is also how the per-brand configuration is structured.

**AI tool usage**

Both. Inside the workflow, an LLM reads each video's captions and produces a
summary of what is actually said, then scores brand relevance and safety as
two separate judgements. That separation is the substance of the feature: an
earlier version asked only whether content was harmful, decided a zoo video
was safe, and published it under a cookware brand titled "Cooking Basics:
Getting Started in the Kitchen". Relevance and harm are different questions.

The model's output is treated as untrusted. Structured JSON is requested,
reasoning blocks are stripped before parsing, malformed JSON is repaired
rather than crashing the run, and rule-based checks run independently so the
pipeline still functions when the LLM does not. Video titles and descriptions
are sanitised for prompt injection before they reach the model.

In the build process, Claude was used throughout for implementation and
review. `docs/FIXES.md` records 37 defects found and fixed, most of them
surfaced by running the thing against live APIs rather than by reading code.

**Flow structure and maintainability**

The orchestration is a LangGraph state machine with seven named nodes:
`load_urls`, `extract_metadata`, `ai_analyze`, `download`, `transform`,
`publish`, `advance`. Safety concerns are separate modules — input validation,
cost guard, content safety, proxy health, audit log — each testable on its
own. Publishers share one interface with four implementations, so adding a
destination does not touch the pipeline.

Failure statuses name the stage that failed rather than collapsing into a
generic error, because the first question anyone asks is where it broke. There
are 156 tests, and four documents covering architecture, decisions, setup and
the defect log.

**Communication quality**

The walkthrough covers the live demo, the node structure, the decisions and
what I would change. The parts I chose to spend time on are the two that show
judgement rather than output: why the interface is a spreadsheet, and why the
pipeline refuses to publish content that does not fit a brand.

**Tool specificity**

Named and versioned throughout: yt-dlp with player-client rotation, ffmpeg for
transformation, LangGraph for orchestration, MiniMax M2.7-highspeed via an
OpenAI-compatible client, Google Drive API v3 with the `drive.file` scope,
Google Sheets API v4, boto3 for S3-compatible storage, YouTube Data API v3 for
the upload path, structlog for JSONL audit output, moto for testing S3 against
a real local server rather than a mock.

Specific choices are documented with reasons in `docs/DECISIONS.md` — why
Drive rather than YouTube, why serial rather than parallel, why polling rather
than webhooks, why `drive.file` rather than full Drive access.

---

## What I'd do next

Make the input source pluggable. The valuable part is ingesting video,
judging it, transforming it and publishing it — YouTube is just one way video
arrives. Pointing it at a brand's own asset library or licensed stock removes
the copyright problem entirely and makes the tool useful beyond this one case.

After that: a review queue so held videos can be approved without reading
JSON, and an evaluation harness to catch quality drift across runs.

---

## Repository

```
video-pipeline/        the pipeline
docs/INSTALL.md        full setup, both audiences
docs/ARCHITECTURE.md   how it fits together
docs/DECISIONS.md      what I chose and why
docs/FIXES.md          37 defects found and fixed, with evidence
video-walkthrough/     narration script for the walkthrough
```
