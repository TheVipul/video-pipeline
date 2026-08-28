# Video Walkthrough Script (12 min)

Target 10-15 min. Loom is fine - they say explicitly they are not grading
production quality.

**Every command and number here was verified against the current build.**
Re-verify if you change the code.

> **The strategic note:** most submissions will show a working script. Three
> things here are unusual, and they deserve your airtime:
> **a URL pasted into a sheet triggering the pipeline by itself**, **the
> pipeline refusing to publish**, and **why a spreadsheet is the interface.**
> The first is memorable, the second shows judgment, the third shows you
> thought about who actually has to use this.
>
> Structure the opening so the pipeline runs *while you talk*. Pasting the URL
> then narrating the "why a spreadsheet" argument fills the ~60-75 seconds of
> processing, and you cut back to a completed row. Do not sit and watch a
> progress bar.

---

## Pre-flight

```bash
cd youtube-pipeline/video-pipeline
.venv/bin/python -m pytest                    # expect: 156 passed
.venv/bin/python run.py --max 3 --publisher local --brand generic
```

Timings measured on this build, so you can pace the narration:

| Step | Takes |
|---|---|
| Watcher notices a pasted URL | ~6-10s |
| One video, end to end (download → Drive → sheet) | ~60-75s |
| Full 5-video batch | ~2.5-3 min |

- [ ] **Re-consent if it has been >7 days** - Google expires test-mode tokens.
      Run any `--publisher gdrive` command; it reopens the browser.
- [ ] Warm the cache with one full run so the demo is not waiting on downloads
- [ ] Open in tabs: **the Google Sheet**, **your Drive folder**,
      `agent/graph.py`, `pipeline/metadata.py`, `docs/FIXES.md`
- [ ] Terminal font 16pt+, window wide enough that log lines do not wrap
- [ ] Second terminal ready for the dead-proxy demo

---

## 0:00 – 0:45 | Cold open

> "Hi, I'm Vipul - this is my submission for the AI Solutions Engineer role.
>
> I built Option 2: a pipeline that ingests YouTube videos, has an LLM
> understand and judge each one, transforms them, and publishes them - with a
> human review gate in the middle.
>
> I want to show you three things: someone pasting a URL into a spreadsheet
> and the pipeline starting on its own, the pipeline *refusing* to publish
> when it should, and why I chose that interface at all.
>
> Let's start with the part a marketing team would actually touch."

---

## 0:45 – 2:00 | The sheet, and paste a URL live — REQUIRED SECTION 1

**Open the sheet first. Not the terminal.**

> "This is what the person running this actually sees. A Google Sheet. They
> paste video URLs into column A. That is their entire job."

*Show the completed rows.*

> "Everything from column B rightward was written by the pipeline. Status, an
> AI-generated title, a summary of what the video actually contains, the link
> to where it was published, a relevance score, and what it cost - about a
> third of a cent.
>
> Look at the relevance column: 1.0 for the zoo video, 0.3 for one that is
> just a logo animation. It discriminates, it does not rubber-stamp."

**Now start the watcher** (second terminal, keep it visible):

```bash
.venv/bin/python watch.py --sheet <SHEET_ID> --publisher gdrive
```

> "That is now watching the sheet. Nothing is scheduled - it reacts."

**Paste a URL into the next empty row, on camera.**

> "I have just pasted a YouTube URL. Watch the terminal."

*Within ~10 seconds: `detected 1 new URL(s) - row N` → `running pipeline...`*

> "There it goes. No button, no cron job, no terminal for the operator.
>
> It takes about a minute end to end, so let me use that time to explain why
> I built the interface this way - and we will come back to the filled-in row."

---

## 2:00 – 3:15 | Why a spreadsheet — REQUIRED SECTION 3 (decisions)

*Let it run in the background while you talk.*

> "This was the choice I thought hardest about.
>
> The CLI is for me. But your job description says twice that this role is
> about non-technical teams - understanding their manual processes, and
> getting sustained adoption. A marketing coordinator is not going to open a
> terminal. If I build something they cannot open, adoption is zero and the
> automation was pointless.
>
> So there are three interfaces for three audiences: the sheet for the
> marketing team, the CLI for engineers and cron, and a setup wizard for
> first-time configuration. Same pipeline underneath."

```bash
.venv/bin/python setup_wizard.py
```

> "Two questions - are you running this for a specific brand or general use,
> and where should the output go. That is the whole setup."

> "One honest note on the watcher: it polls the sheet every ten seconds rather
> than receiving a push notification. Google delivers those to a public HTTPS
> endpoint, and a laptop is not one - real push would need a deployed webhook
> receiver or a tunnel. Worth it in production, needlessly fragile here. Ten-
> second reads are two orders of magnitude inside the quota and the behaviour
> is identical."

---

## 3:15 – 4:15 | Back to the row

*Refresh the sheet. The row is filled in.*

> "There it is. Title, summary, Drive link, relevance, cost - written back
> beside the URL I pasted a minute ago."

*Open the Drive link. Show the file.*

> "And the video is in Drive. That is the whole loop: paste, wait, done."

*Now paste the same URL again, or just point at the watcher.*

> "And if I add nothing new, it sits idle. Re-running costs nothing - it is
> idempotent per row, so a row that already has a status is skipped. That is
> what makes it safe to leave running."

---

## 4:15 – 6:00 | The refusal — the decision I most want to explain

**This is the most important two minutes of the video.**

```bash
.venv/bin/python run.py --max 5 --publisher local --brand surlatable
```

> "Same five videos that just published fine. Now: zero published, five held
> for review.
>
> That's correct. A zoo video, a Blender animation, drone footage of a beach -
> none of that is Sur La Table content. The pipeline is refusing to publish
> them rather than inventing a cooking-themed title to force them through.
>
> I know it would do that, because it did. Before I fixed it, it published a
> video of a man at a zoo titled **'Cooking Basics: Getting Started in the
> Kitchen.'**"

*Open `docs/FIXES.md`, item 6.*

> "Here's the root cause, and it's a design bug, not a typo. The safety
> classifier was asking *'is this harmful?'* Beach footage isn't harmful. So
> it came back 'safe' and the pipeline published it.
>
> Nothing was asking *'does this belong on this brand's channel?'* Those are
> two completely different questions and I'd collapsed them into one.
>
> Worse - the model's own reasoning field said the content was 'unsuitable for
> repurposing.' It knew. Nobody was reading it. That field was parsed into a
> variable and then thrown away.
>
> The fix separates harm from relevance. They're scored independently now,
> with a per-brand threshold, and anything uncertain is held for a human
> rather than published."

> **If asked "how do you know the relevance score is right?"** - I don't,
> fully. It's a useful gate, not ground truth. That's exactly why it routes to
> a human queue instead of being trusted to publish on its own.

## 6:00 – 7:30 | Understanding the video — AI usage

*Show a manifest or the Summary column.*

> "The pipeline doesn't just read titles. It pulls the video's captions -
> a few kilobytes, no video download - and has the LLM summarise what's
> actually said, *before* deciding whether to spend bandwidth downloading it.
>
> For the zoo video it correctly summarises a person describing elephants'
> trunks. That's from the transcript, not the title.
>
> But not every video has captions. Silent footage usually has none. So rather
> than guess, every summary records its source - transcript, description, or
> none - and the model says 'based on metadata only' when that's what it had.
> A thin summary is never dressed up as a confident one."

> "It also captures the video's licence field, which tells you whether the
> uploader marked it Creative Commons. I'll come back to why that matters."

## 7:30 – 9:00 | Setup and structure — REQUIRED SECTION 2

*Open `agent/graph.py`.*

> "Orchestration is a LangGraph state machine - a node per stage, and a
> conditional edge that advances, stops, or trips a circuit breaker after
> repeated failures.
>
> It's deliberately serial. The brief asks for serial, and it's also correct
> against YouTube: parallel downloads from one IP is the fastest way to get
> blocked."

*Open `agent/state.py`, point at the `records` reducer.*

> "This reducer is worth ten seconds. Each node returns only the record for
> the video it touched. Without an explicit merge, LangGraph's default is
> last-write-wins on the whole key - so every node was silently wiping the
> previous videos. A five-video run ended with one record. The report and the
> saved state were both wrong, and nothing errored.
>
> That was the pattern across this codebase when I started: things failed
> quietly and still reported success."

*Show the module layout briefly.*

> "Safety modules are decoupled and independently testable - URL allowlist,
> spend guard, content safety, proxy health, audit log. Publishers are an
> interface with four implementations: local, Google Drive, S3, and YouTube."

## 9:00 – 10:15 | Error handling — REQUIRED, their Priority #2

*Open `pipeline/metadata.py`.*

> "The brief specifically requires handling YouTube proxy and blocking
> behaviour. This is where the most important fix was.
>
> The downloader had a good fallback ladder. The *metadata* stage had none -
> and metadata runs first. So one dead proxy killed the run before any of that
> logic ever executed."

**Second terminal:**

```bash
printf 'http://127.0.0.1:9999\nhttp://127.0.0.1:9998\n' > /tmp/dead.txt
PROXY_FILE=/tmp/dead.txt .venv/bin/python run.py --max 1 --publisher local --brand generic
```

> "Two proxies that don't exist. Attempt one fails, classified as blocked,
> backs off with jitter. Attempt two, second proxy, same. Attempt three falls
> back to direct - and it downloads and publishes.
>
> Before the fix this was zero published, one failed.
>
> The classification matters too. A *blocked* request is worth retrying on
> another route. A *removed* video never will be - so that fails fast instead
> of burning the whole ladder. I got that wrong initially: yt-dlp says
> 'Video unavailable' on one client and 'This video **is** unavailable' on
> another, and I'd only matched the first. It wasted four attempts on videos
> that could never succeed."

> "Everything else degrades the same way - no LLM key runs rules-only, no
> captions falls back to metadata. The difference now is that every
> degradation is *reported*. A quiet success is the dangerous outcome."

## 10:15 – 11:30 | What I'd do differently — REQUIRED SECTION 4

> "Honest list.
>
> **One - the copyright question, which is the real one.** I read YouTube's
> Terms of Service properly. Downloading via yt-dlp isn't permitted *even for
> Creative Commons videos* - because the content licence and the platform
> terms are separate things. YouTube's own page says 'YouTube can't grant you
> rights.'
>
> That's why the default publish target is Google Drive, not a YouTube
> channel. Uploading to a Drive folder is an internal file operation.
> Re-uploading someone else's video to a public channel is a rights question I
> can't engineer away. The YouTube publisher is written and works - it just
> shouldn't be pointed at third-party content without a licensing answer.
>
> **Two - make the source pluggable.** The valuable engine here is 'ingest
> video, judge it, transform it, publish it.' YouTube is just one way video
> gets in. Point it at a brand's own asset library or licensed stock and the
> legal problem disappears entirely. That's a day's work and it's what I'd do
> first.
>
> **Three - a working PO token provider.** The plugin shim is registered but
> its token server isn't running, so it contributes nothing today. I'd rather
> tell you it's missing than let you assume it's covered.
>
> **Four - an evaluation harness.** 156 unit tests, but nothing catching
> quality regressions across runs: did relevance scoring drift, did cost
> spike."

## 11:30 – 12:00 | Close

> "So: a working pipeline with a real review gate, three interfaces for three
> audiences, per-brand configuration, 156 tests, and a defect log in
> `docs/FIXES.md` documenting 34 issues I found and fixed with before-and-after
> evidence.
>
> The thing I'd leave you with: the version I started from reported
> 'Failed: 0' on every run while the AI had never actually executed, the
> watermark was silently skipped, and four of five videos were being dropped
> from state. Making failures loud was worth more than any feature I added.
>
> Happy to dig into any of it."

---

## Likely questions

**"Why not n8n?"** Option 2 doesn't require a workflow platform - that's
Option 1. And your JD asks for people who go beyond linear n8n flows. The
parts that matter here - the review gate, the retry ladder, the state
handling - are exactly what n8n renders as opaque boxes.

**"Why Drive instead of YouTube?"** Copyright, not capability. The YouTube
publisher works; pointing it at third-party video is the problem.

**"What did you build vs. inherit?"** The metadata resilience ladder, the
relevance gate, JSON extraction, the publisher factory fix, Drive and Sheets
integration, captions/summary, the state reducer, cost accounting, watermark
fallback, and the test suite. `docs/FIXES.md` lists all 34 with evidence.

**"Why polling instead of a webhook?"** Google pushes to a public HTTPS
endpoint; a workstation is not one. Real push needs a deployed receiver or a
tunnel - worth it in production, fragile for a demo. Ten-second polling is
inside the quota and behaves identically.

**"What breaks first at scale?"** YouTube bot detection, without residential
proxies and a working PO token server. Everything else scales linearly.
Sheets has per-minute quotas but writes are batched into one call per run.

**"How much does it cost?"** About a third of a cent per video, metered
per-video against a hard cap. Bandwidth dominates at scale, not tokens.

---

## Recording checklist

- [ ] `.venv/bin/python -m pytest` → 156 passed
- [ ] Google token fresh (re-consent if >7 days old)
- [ ] Sheet open with completed rows, plus **one blank row to paste into live**
- [ ] Watcher command ready to paste in a second terminal
- [ ] A URL on your clipboard, ready to paste (do not type it on camera)
- [ ] Drive folder open in a tab
- [ ] `/tmp/dead.txt` prepared
- [ ] `docs/FIXES.md` open at item 6
- [ ] Don't read verbatim - the bug stories should sound like recollection
- [ ] Aim 11-12 min
