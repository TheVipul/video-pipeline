# Anti-Bot Strategy: YouTube in 2024+

This document is the deep-dive on the hardest part of Option 2: actually
downloading from YouTube without getting blocked.

## The Problem

YouTube runs aggressive bot detection at three layers:

1. **Transport**: IP rate-limiting, datacenter IP blacklists, TLS fingerprinting
2. **Application**: Player client validation, request signature (PO tokens),
   cookie-based session tracking
3. **Behavioral**: Burst patterns, sequential access, missing browser headers

A naive `yt-dlp` invocation with default settings fails within 5-20 downloads
on a fresh IP. A "production" pipeline needs to handle this defensively.

## Our 6-Layer Defense

### Layer 1: Player client rotation
`--extractor-args "youtube:player_client=android,web,ios,tv_embedded"`

YouTube serves different "player clients" to different surfaces. Each client
has its own anti-bot thresholds:
- `android` (mobile app): most permissive, oldest code path
- `web` (browser): standard, gets new protections first
- `ios` (iOS app): similar to android
- `tv_embedded` (TV/embed player): least monitored, often bypasses

Rotating the client on each attempt means a block on one client doesn't
kill the pipeline.

### Layer 2: PO Token plugin
`bgutil-ytdlp-pot-provider` (Python package, installed via pip)

In 2024 YouTube started requiring **Proof of Origin (PO) tokens** — signed
attestations that the request is coming from a legitimate browser session.
Without these tokens, even valid cookies get blocked.

The plugin generates PO tokens by running a small JavaScript snippet that
mimics the browser's signing logic. The latest yt-dlp versions integrate
this automatically; we just need the package installed.

**Production path**: Run a local PO token server (`bgutil-ytdlp-pot-server`)
that uses headless Chromium to generate tokens. This is more reliable than
the inline plugin but requires a Chromium dependency.

### Layer 3: Cookie-based auth (optional)
`YT_COOKIES_FILE=inputs/cookies.txt` (Netscape format)

Logged-in YouTube sessions are flagged less aggressively. To use:
1. Install the "Get cookies.txt LOCALLY" browser extension
2. Log into YouTube
3. Export cookies to `inputs/cookies.txt`
4. The pipeline picks it up automatically

**Note**: Cookies are personal credentials. They go in `.env`, not in git.

### Layer 4: Proxy rotation
`inputs/proxies.txt` — one proxy per line, `protocol://user:pass@host:port`

The ProxyPool class tracks per-proxy state:
- `requests_in_window` (last 60s) — for rate limit
- `consecutive_failures` — for circuit breaker
- `cooldown_until` — for backoff after a failure

Proxies are acquired round-robin, skipping unhealthy ones. The first proxy
that fails 3 times in a row is marked unhealthy and the pool avoids it.

**For the demo**: 0 proxies (direct connection works because the videos
are short and well-distributed).

**For production**: Residential proxies (Bright Data, Smartproxy, Oxylabs).
Datacenter proxies (Webshare free tier) are flagged by YouTube more
aggressively. Budget: $50-500/month for 13 brands running daily.

### Layer 5: Exponential backoff + jitter
`SAFETY_DOWNLOAD_MIN_INTERVAL_SEC=2` and `SAFETY_DOWNLOAD_MAX_INTERVAL_SEC=8`

Between download attempts (not just within one), we sleep a random duration
in [min, max]. The jitter prevents synchronized bursts that look bot-like.

Between pipeline runs, the LLM cost guard prevents a stuck retry loop from
running up the API bill.

### Layer 6: Format restriction
`-f "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/b[ext=mp4]/b"`

- `height<=1080`: skip 1440p/2160p (less common, more bot-flagged)
- `ext=mp4`: prefer MP4 containers (skip WebM, 3GP)
- `bv*+ba`: separate video + audio streams (best quality)
- Fallback chain: progressively less restrictive if the preferred format
  is unavailable

The fewer format negotiations, the smaller the fingerprint surface.

## What we DON'T do (and why)

### Headless browser scraping
Selenium / Playwright could extract the manifest directly from the
YouTube page, bypassing yt-dlp entirely. But:
- Heavy dependency (a full Chromium runtime)
- Slower (multi-second per page load vs sub-second for yt-dlp)
- Higher detection risk (Chromium fingerprints are well-known)
- yt-dlp's player client approach is essentially "browser without the
  browser" — same result, less overhead

### YouTube Data API
The official API gives you metadata + the ability to upload, but **not
to download video files**. So it doesn't solve the download problem.

### Direct manifest fetching
YouTube's manifest URLs are signed and short-lived. You can extract them
from the watch page HTML, but that's exactly what yt-dlp does internally
with much more sophistication. No reason to reimplement.

### Residential proxy from a free list
Free residential proxy lists are almost always honeypots. Don't.

## Failure Modes

What happens when all 6 layers fail:

```
download_attempt (1) -> fail
  cooldown 30s on this proxy
download_attempt (2) -> fail (different client)
  cooldown 30s on this proxy
download_attempt (3) -> fail
  all proxies marked unhealthy
  agent marks video as failed
  pipeline continues with next URL
  audit log records: video_failed, error="all 4 attempts failed"
```

This is what the circuit breaker (`SAFETY_MAX_CONSECUTIVE_FAILURES=3`)
prevents — if too many videos fail in a row, the pipeline pauses
(`SAFETY_CIRCUIT_BREAKER_COOLDOWN_SEC=60`) and the user can investigate
before continuing.

## Production Deployment

For CSC Generation's 13 brands, the production setup would be:

1. **Per-brand worker pools** — each brand gets its own IP range so a
   block on one brand doesn't affect the others
2. **Residential proxy subscription** — Bright Data or similar, $200-500/mo
   for enough headroom
3. **PO token server** — one shared Chromium instance generates tokens
4. **Cookie rotation** — each brand has 2-3 logged-in sessions, rotated
   to avoid session-fingerprint linking
5. **Daily schedule** — 4am UTC start time (low traffic window for
   the regions YouTube's anti-bot uses for training)
6. **Per-brand circuit breakers** — one brand's failure doesn't stop
   the others
7. **Audit log to SIEM** — every retry, every proxy, every success
   is shipped to the security team's log aggregator

## What to do if YouTube changes its bot detection again

1. Update yt-dlp: `pip install -U yt-dlp` (they ship fixes within hours)
2. Update the PO token plugin: `pip install -U bgutil-ytdlp-pot-provider`
3. Rotate player clients: try adding `mweb,web_creator,web_safari` to
   the player_client list
4. Add a new layer: e.g., a Selenium-based fallback that uses the
   actual browser
5. If all else fails: pivot to YouTube Data API for metadata, accept
   the manual download step, or use a third-party service
   (e.g., RapidAPI's YouTube downloader)
