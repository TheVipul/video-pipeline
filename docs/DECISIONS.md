# Key Decisions

A summary of the choices I made and what I considered for each. The goal is
to show how I think, not to defend every choice as correct.

## 1. LangGraph agent vs. plain Python script

**Chose**: LangGraph.

**Considered**:
- Plain Python: simpler, less indirection
- LangChain LCEL: lighter than LangGraph but no state persistence
- Custom state machine: full control, more code
- Prefect / Temporal: heavyweight for a single-run pipeline

**Why LangGraph wins**:
- The JD specifically calls out "stateful, dynamic AI systems with memory"
  and LangGraph by name
- Built-in checkpointing (MemorySaver) means resume is free
- The graph visualization is great for the video walkthrough
- The state schema (TypedDict) is JSON-serializable, easy to inspect

**What I gave up**: ~150 lines of agent boilerplate that a plain script
wouldn't need. Worth it.

## 2. Local publisher as default, S3 + YouTube as documented extensions

**Chose**: Local by default.

**Considered**:
- S3 by default: more "production-like" but requires credentials
- YouTube by default: most impressive but requires OAuth + TOS compliance

**Why Local wins**:
- Always works — no setup, no credentials, no external dependencies
- The S3/YouTube paths are documented in `pipeline/publishers/s3.py` and
  `pipeline/publishers/youtube.py`
- For a hiring manager demo, reliability > impressive-but-flaky

**What I gave up**: A live YouTube re-upload would be a more impressive
demo, but it would also be a TOS risk if mishandled. The stub makes it
clear that the integration is intentional, not just missing.

## 3. Rules-based content safety + LLM (vs. LLM only)

**Chose**: Rules layer + LLM layer, merged.

**Considered**:
- LLM only: simpler code, but every call costs money and adds latency
- Rules only: free, but high false-positive rate and misses nuance
- Two-pass (LLM twice for redundancy): expensive and slow

**Why both win**:
- Rules catch the obvious cases (NSFW, known IP brands) in <1ms, $0
- LLM adds judgment for the gray area (tone, brand fit, context)
- LLM cost is ~$0.001/vid for the metadata + $0.001/vid for the safety
  check, so total is ~$0.01 for 5 videos
- Merge logic: rules reject wins, then LLM, then both safe

**What I gave up**: Two function calls per video (slower) and more code.

## 4. yt-dlp + multi-client fallback (vs. youtube-dl-exec / pytube / API)

**Chose**: yt-dlp with player_client rotation.

**Considered**:
- `pytube`: simpler API but YouTube broke it in 2023
- `youtube-dl`: deprecated, unmaintained
- YouTube Data API: requires API key, doesn't give video file
- `pytubefix`: pytube fork, similar issues
- `yt-dlp` standalone: actively maintained, 6 clients to rotate

**Why yt-dlp wins**:
- Battle-tested against YouTube's anti-bot for years
- `player_client=android,web,ios,tv_embedded` rotates the fingerprints
  that YouTube uses to detect bots
- The PO token plugin (`bgutil-ytdlp-pot-provider`) handles the latest
  2024+ protection
- Active community, fast updates when YouTube changes

**What I gave up**: yt-dlp is a subprocess (not a Python library), so
error handling needs to parse stderr. Acceptable for a 2-3 day project.

## 5. FFmpeg via ffmpeg-python (vs. direct ffmpeg CLI / MoviePy)

**Chose**: ffmpeg-python with graceful fallback to direct CLI.

**Considered**:
- MoviePy: nicer API but slower, doesn't handle H.264 well
- Direct ffmpeg CLI: more verbose, more control
- `python-ffmpeg`: thin wrapper, less maintained

**Why ffmpeg-python wins**:
- Pythonic filter graph construction
- Direct CLI escape hatch when the Python wrapper hits edge cases
- The `drawtext` watermark code is much cleaner as a filter than as
  an `-vf` string

**What I gave up**: ffmpeg-python occasionally has serialization bugs
(filter ordering, escaping). The `_try_drawtext` helper runs a 0.1s
test encode before applying the filter to fail fast.

## 6. JSON audit log + HTML report (vs. just printing to console)

**Chose**: Both.

**Considered**:
- Console only: simplest but unreviewable after the run
- Database (SQLite): queryable but overkill for a single run
- Just a JSON file: structured but unreadable
- HTML only: pretty but harder to grep

**Why both win**:
- JSONL is grep-friendly (`grep '"event": "download_failed"' audit.jsonl`)
- HTML is what a hiring manager opens in a browser
- The audit log is the source of truth for "what really happened" during
  the video walkthrough

## 7. Five videos per run (vs. configurable, no default)

**Chose**: Default 5, configurable up to 20.

**Why 5**: The assessment says "5 or more short YouTube videos." Five is
the floor; more is opt-in via `--max`. The agent would happily process
100, but the assessment timebox is 2-3 days and the demo is more
impressive at 5-10 than at 50.

## 8. IDEMPOTENT downloads (vs. always re-download)

**Chose**: Skip if file exists with size > 1KB.

**Considered**:
- Always re-download: simpler logic, but slow and wasteful
- Skip if exists: fast, but might miss a partial/corrupt file
- Hash check: most correct, but adds a download round-trip

**Why size-check wins**:
- A corrupt file would be <1KB (yt-dlp returns a tiny error page)
- A successful file is at least tens of KB
- Re-runs become near-instant (just metadata fetch + transform)
- The dev loop is much faster

**What I gave up**: If YouTube updates a video and we want the new
version, we'd need to delete the local file manually. For a 2-3 day
project that's acceptable.

## 9. Watermark as a text overlay (vs. image overlay)

**Chose**: Text overlay (drawtext).

**Considered**:
- PNG watermark image: more flexible but requires shipping an image asset
- Logo image overlay: most professional but brand-specific
- Text watermark: simple, no asset needed, but font issues on Windows

**Why text wins**:
- No asset to ship — easier for the hiring manager to test
- Brand config is plain YAML
- If arial.ttf is missing, we skip gracefully and the video still ships

**What I gave up**: A real logo watermark would look more professional.
The brand config (`branding.watermark_text`) supports either approach;
swap to image overlay when brand assets are available.

## 10. No committed cookies file

**Chose**: Ship `cookies.example.txt`, not `cookies.txt`.

**Why**:
- Cookies are personal credentials
- The .gitignore (and the assessment's "no secrets" rule) keep them out
- `YT_COOKIES_FILE=inputs/cookies.txt` in .env.example makes the path clear
- Pipeline works without cookies, just with a higher bot-detection risk

## 11. The videos I chose for the demo

**Chose**: 5 publicly available, copyright-safe, varied-length videos.

**Considered**:
- All Blender Foundation: 3 of 5 are now unavailable on YouTube (taken
  down or moved); the remaining 2 are too long for "short"
- Famous music videos: copyright (Taylor Swift, Queen, etc.) — bad look
  for a hiring manager demo
- Random "no copyright" videos: not always available

**Why this set works**:
- "Me at the zoo" (19s) — first YouTube video, public knowledge
- "Demo Background Sample Video" (18s) — public domain sample
- "YouTube" (2:05) — YouTube's own sample
- "Sea waves & beach drone" (3:22) — tagged "no copyright"
- "Spring - Blender Open Movie" (7:44) — CC BY 3.0

All under 10 min, all copyright-safe, all currently available (verified
in Aug 2026).

## What I considered and didn't do (yet)

| Idea | Why deferred |
|---|---|
| MinIO for local S3 | S3Publisher already exists; spinning up MinIO adds setup overhead without changing the demo story |
| Resume from checkpoint | LangGraph MemorySaver is wired; a CLI flag for `--checkpoint file.json` is a 10-line add |
| Unit tests for every module | Tests for safety module exist; full coverage would be nice but 2-3 day timebox |
| Pre-commit hooks + CI | Project structure is clean; linters can be added |
| PO token server (`bgutil-ytdlp-pot-server`) | Configured the package; running the local server requires Chromium dependencies |
| YouTube Data API re-upload | Documented as a stub; would require OAuth flow + compliance review |
| Evaluation harness (pass rate metrics) | Manual review is fine for one-shot demo; Prometheus exporter would be the production add |
| Slack/Linear notification on `review` verdict | Hook system; would be a 1-hour add once the brand team integration is defined |
