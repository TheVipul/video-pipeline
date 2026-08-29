# Installation & Usage

Two audiences, two paths. Pick the one that matches you.

| You are | Go to |
|---|---|
| A **non-technical stakeholder** who wants results | [Path A — the spreadsheet](#path-a--the-spreadsheet-no-install) |
| A **technical user** who wants to run or modify it | [Path B — full install](#path-b--full-install) |

---

# Path A — the spreadsheet (no install)

**Nothing to install. No account to create. No terminal.**

The pipeline is driven from a Google Sheet. You paste YouTube URLs into
column A; everything else fills itself in.

### How to use it

1. Open the shared sheet link you were sent
2. Paste a YouTube URL into the next empty row of **column A**
3. Wait

Within about ten seconds a watcher notices the new row and starts processing.
A single video takes roughly a minute end to end.

### What appears

| Column | What it tells you |
|---|---|
| **Status** | `Published`, `Held - needs review`, `Skipped`, or a failure naming the stage |
| **Title** | An AI-written title for the content |
| **Summary** | What the video actually contains - from its captions where available |
| **Published Link** | The finished video in Google Drive. Click it. |
| **Relevance** | 0.0-1.0. How well the content fits the configured brand |
| **Cost** | What that video cost to process, in dollars |
| **Notes** | Why something was held, or what failed |

### Things worth knowing

**It only processes rows that are blank.** A row that already has a status is
skipped, so re-opening the sheet or adding new rows never reprocesses old work.

**Not every video will publish.** If a brand profile is configured, videos
that do not fit it are **held for review** rather than published. That is
deliberate - the tool refuses to attach a brand's name to content that does
not belong to it. The Notes column says why.

**Some videos are rejected outright:** anything over 10 minutes is skipped,
and only YouTube links are accepted.

**Summaries vary in confidence.** Videos with captions get a summary of what
is actually said. Silent footage - drone shots, animation - has no captions,
so its summary is based on the description only, and says so.

**If nothing happens for several minutes**, the watcher is probably not
running. It runs on a specific machine; ask whoever set it up.

---

# Path B — full install

### Prerequisites

| Requirement | Why | Install |
|---|---|---|
| **Python 3.11+** | Runs the pipeline | [python.org](https://python.org) - on Windows tick *"Add Python to PATH"* |
| **ffmpeg** | Video transformation | macOS `brew install ffmpeg` · Windows `winget install Gyan.FFmpeg` · Ubuntu `sudo apt install ffmpeg` |
| ~2 GB free disk | Downloaded video | - |

Everything else is optional. **The pipeline runs with no credentials at all.**

### Install

**macOS / Linux**
```bash
git clone https://github.com/TheVipul/video-pipeline.git
cd video-pipeline/video-pipeline
./setup.sh
```

**Windows** (PowerShell)
```powershell
git clone https://github.com/TheVipul/video-pipeline.git
cd video-pipeline\video-pipeline
powershell -ExecutionPolicy Bypass -File setup.ps1
```

### Verify it works

```bash
.venv/bin/python -m pytest          # 185 tests, no network, no credentials
```

```powershell
.\.venv\Scripts\python.exe -m pytest
```

### First run

```bash
.venv/bin/python run.py --max 5 --publisher local --brand generic
```

Output lands in `outputs/published/`. Open `outputs/report.html` for a summary.

This works **before you configure anything**. Without an LLM key the AI stages
are skipped and metadata falls back to YouTube's own - the run still succeeds,
and says clearly that it degraded.

---

# What to wire, and when

Everything here is optional and independent. Add what you need.

### 1. LLM key — AI titles, summaries, brand scoring

Without this, no summaries and no relevance scoring.

Get a key from [platform.minimax.io](https://platform.minimax.io), then in `.env`:

```
LLM_API_KEY=your_key_here
LLM_BASE_URL=https://api.minimax.io/v1
LLM_MODEL=MiniMax-M2.7-highspeed
```

Costs roughly **$0.003 per video**. `SAFETY_MAX_LLM_SPEND_USD` caps spend per run.

> Anthropic or OpenAI work too - change `LLM_BASE_URL` and `LLM_MODEL`. The
> client is OpenAI-compatible.

### 2. Google credentials — Drive upload and Sheets

Needed for `--publisher gdrive` and for any `--sheet` usage. The shared OAuth
setup covers Drive and Sheets, and adds the narrow `youtube.upload` scope only
when the YouTube publisher is selected.

1. [console.cloud.google.com](https://console.cloud.google.com) → create a project
2. **APIs & Services → Library** → enable **Google Drive API** and **Google Sheets API**
3. **APIs & Services → OAuth consent screen** → *External* → under **Test users**, add your own Google account
4. **Credentials → Create Credentials → OAuth client ID → Desktop app** → download the JSON
5. Save it as `inputs/client_secret.json`
6. Run any Drive command - a browser opens for consent, once

```bash
.venv/bin/python run.py --max 1 --publisher gdrive
```

You can also complete or verify consent without processing a video:

```bash
.venv/bin/python run.py --check-auth --publisher gdrive
```

> **Two gotchas.** You must add yourself as a *test user* or consent fails with
> `403: access_denied`. And tokens for unverified apps **expire after 7 days** -
> re-run any Drive command to refresh. Do not publish the app to get around
> this: the Sheets scope is classed as sensitive and would trigger a review.

The app requests `drive.file`, which grants access **only to files it
creates**. It cannot see anything else in your Drive.

### 3. YouTube upload — optional, and think first

`--publisher youtube` is implemented (resumable upload, `privacyStatus:
private` by default) but not wired to a channel.

Before enabling it, read `docs/DECISIONS.md`. Re-uploading third-party video
to a public channel is a rights question, not a technical one - which is why
Drive is the default target.

Enable the YouTube Data API v3 in the same Google Cloud project, then run:

```bash
.venv/bin/python run.py --check-auth --publisher youtube
```

This reuses `inputs/google_token.json` and adds only the `youtube.upload`
scope. Uploads default to private.

### 4. S3-compatible storage — optional

For AWS S3, Cloudflare R2, Backblaze B2, or MinIO, set:

```env
S3_BUCKET=your-bucket
S3_PREFIX=republished/
S3_REGION=us-east-1
S3_ENDPOINT_URL=https://your-provider-endpoint  # omit for AWS S3
S3_ACCESS_KEY=your-access-key
S3_SECRET_KEY=your-secret-key
```

Then select `--publisher s3`. The pipeline fails with an actionable message
when `S3_BUCKET` is missing; it never invents a placeholder bucket.

### 5. Cookies and proxies — optional, for scale

Only needed if YouTube starts blocking you:

```
YT_COOKIES_FILE=inputs/cookies.txt
PROXY_FILE=inputs/proxies.txt
```

---

# Turning on the brand gate

**What it does.** Without it, the pipeline publishes anything that is not
harmful. With it, the pipeline also asks *"does this belong on this brand's
channel?"* and holds anything that does not.

That is the difference between publishing a zoo video under a cookware brand
with an invented cooking title, and refusing to.

Enabling it also switches on **watermarking**, since both only make sense
when a brand is behind the output.

### Step 1 — create a brand profile

Copy an existing one:

```bash
cp configs/brands/kitchenware.yaml configs/brands/yourbrand.yaml
```

### Step 2 — edit it

```yaml
brand:
  name: Your Brand
  audience: who your content is for
  tone: how it should sound

policy:
  min_relevance: 0.4        # below this, hold for review

transform:
  watermark_text: yourbrand.com

ai_prompts:
  metadata_system: |
    ... tell the model what your brand is about, and to score
    relevance honestly rather than inventing an on-brand angle ...
  brand_safety_system: |
    ... harm and appropriateness only - relevance is scored separately ...
```

The `min_relevance` threshold is the dial. `0.4` holds anything clearly
off-topic. Raise it to be stricter; lower it to publish more.

### Step 3 — run with it

```bash
.venv/bin/python run.py --max 5 --brand yourbrand --publisher gdrive
```

**Naming a brand switches brand mode on automatically.** No other flag needed.

To make it permanent, run `setup_wizard.py` and choose *"For a specific
brand"*, or set in `.env`:

```
PIPELINE_MODE=brand
PIPELINE_BRAND=yourbrand
PIPELINE_ENABLE_WATERMARK=true
PIPELINE_ENABLE_RELEVANCE_GATE=true
```

### Step 4 — check what it holds

```bash
.venv/bin/python run.py --max 5 --brand yourbrand --publisher local
```

Expect held videos. Read the Notes and tune `min_relevance` until the
boundary sits where you want it.

> To go back to publishing everything: `--brand generic`, or `--mode general`.

---

# Running the sheet interface

### Create a sheet

```bash
.venv/bin/python run.py --create-sheet
```

Prints a URL. Paste video URLs into column A.

### Process what is pending, once

```bash
.venv/bin/python run.py --sheet <SHEET_ID> --publisher gdrive
```

### Or watch it continuously

```bash
.venv/bin/python watch.py --sheet <SHEET_ID> --publisher gdrive
```

Now anyone pasting a URL into the sheet triggers the pipeline within ~10
seconds. Leave this running on a machine that stays on.

> **Hosting this permanently?** See
> [`WINDOWS_DEPLOYMENT.md`](WINDOWS_DEPLOYMENT.md) for running it on an
> always-on Windows machine, including surviving reboots and the weekly token
> refresh.

> It **polls** every 10 seconds rather than receiving push notifications -
> Google delivers those to a public HTTPS endpoint, which a workstation is
> not. Behaviour is identical; the mechanism is simply more robust for a
> machine behind a router.

### Sharing the sheet with non-technical users

Share it as you would any Google Sheet. **Editor** access lets them add URLs;
**Viewer** lets them read results only.

If you share it, also make the Drive output link-viewable, or the
*Published Link* column will show "You need access":

```python
GoogleDrivePublisher(make_shareable=True)
```

---

# Command reference

```bash
run.py --max N                    # limit videos this run
       --brand NAME               # brand profile (implies brand mode)
       --mode general|brand       # override mode explicitly
       --publisher local|gdrive|s3|youtube
       --check-auth              # verify/complete Google OAuth, then exit
       --sheet ID                 # read URLs from a sheet, write results back
       --create-sheet             # create a formatted sheet and exit
       --force                    # reprocess rows that already have a status
       --dry-run                  # no network calls
       --checkpoint FILE          # resume a previous run
       --urls FILE                # use a text file instead of a sheet

watch.py --sheet ID               # process URLs as they are added
         --interval N             # seconds between checks (default 10)
         --once                   # process what is pending, then exit

setup_wizard.py                   # interactive first-time configuration
```

---

# Troubleshooting

| Symptom | Cause |
|---|---|
| `ffmpeg not found` | Not installed, or PATH not refreshed - open a new terminal |
| `403: access_denied` on Google consent | Add your account under **Test users** on the OAuth consent screen |
| Google auth suddenly fails | Test-mode tokens expire after 7 days. Re-run any Drive command. |
| `LLM cost: $0.0000` and no summaries | No `LLM_API_KEY` - the run still succeeds, degraded |
| Everything `Held - needs review` | Brand mode is on and content does not fit. Lower `min_relevance` or use `--brand generic`. |
| Sheet rows never process | The watcher is not running |
| `No pending rows` | Every row already has a status. Use `--force` to reprocess. |
| Videos `Skipped` | Over the 10-minute cap (`PIPELINE_MAX_DURATION_SEC`) |

---

# Security

`.env`, `inputs/client_secret.json`, `inputs/*token*.json` and
`inputs/cookies.txt` are gitignored. **Never commit them** - a cached OAuth
token is a live credential that grants API access on your behalf until
revoked.

To revoke access at any time:
[myaccount.google.com/permissions](https://myaccount.google.com/permissions)
