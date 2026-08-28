"""
Stage 1: Metadata extraction.

Calls `yt-dlp --dump-json <url>` (no download) to fetch title, description,
duration, thumbnail URL, tags, channel, view count, etc.

Metadata extraction is the pipeline's first contact with YouTube, which makes
it the stage most likely to meet a block. It therefore runs the same
resilience ladder as the download stage: rotate proxies, fall back to a
direct connection, rotate player clients, and back off between attempts.

Earlier this stage was deliberately fail-fast ("canary signal"). That was the
wrong call: because metadata runs before download, a single dead proxy killed
the whole run before the downloader's own fallback logic ever executed. The
ladder now lives in both stages.
"""
from __future__ import annotations

import json
import random
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config import YouTubeSettings
from logging_setup import get_logger
from pipeline._runtime import yt_dlp_command
from safety.proxy_health import ProxyPool, ProxyState

log = get_logger(__name__)


@dataclass
class VideoMetadata:
    video_id: str
    url: str
    title: str = ""
    description: str = ""
    duration_sec: int = 0
    channel: str = ""
    uploader: str = ""
    upload_date: str = ""
    view_count: int = 0
    like_count: int = 0
    tags: list[str] = field(default_factory=list)
    thumbnail_url: str = ""
    webpage_url: str = ""
    # --- richer signals, previously fetched from YouTube and then discarded.
    # `license` is the important one: it reports whether the uploader marked
    # the video Creative Commons, which is the single most useful field for
    # deciding whether reuse is even defensible.
    license: str = ""
    categories: list[str] = field(default_factory=list)
    comment_count: int = 0
    age_limit: int = 0
    availability: str = ""
    live_status: str = ""
    language: str = ""
    chapters: list[dict] = field(default_factory=list)
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_captions: bool = False
    caption_languages: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)
    fetched_with: str = ""  # "direct" | proxy URL used
    fetch_duration_sec: float = 0.0
    # One entry per attempt, including the ones that failed. The audit trail
    # previously recorded only the route that finally worked, which made
    # "how often are we being blocked?" unanswerable - the single most useful
    # operational question for this pipeline.
    attempt_log: list[dict] = field(default_factory=list)


def is_creative_commons(license_text: str) -> bool:
    """True when the uploader marked the video Creative Commons.

    yt-dlp surfaces YouTube's human-readable label, e.g.
    "Creative Commons Attribution license (reuse allowed)". A licence marking
    is not legal advice - and notably it does not override YouTube's own Terms
    of Service on downloading - but it is a strong signal worth recording and
    surfacing to whoever approves the reuse.
    """
    return "creative commons" in (license_text or "").lower()


class MetadataError(RuntimeError):
    pass


# Ordered by how often each survives a block, best first. Mirrors
# downloader.PLAYER_CLIENTS so both stages degrade the same way.
PLAYER_CLIENTS = [
    "android,web,ios,tv_embedded",
    "web_safari,ios",
    "android",
    "web",
    "ios",
]

# Substrings that mean "YouTube refused us" rather than "the video is gone".
# A block is worth retrying on another proxy/client; a dead video is not.
BLOCK_SIGNATURES = (
    "sign in to confirm",
    "not a bot",
    "http error 403",
    "http error 429",
    "too many requests",
    "unable to download api page",
    "unable to connect to proxy",
    "failed to establish a new connection",
    "connection refused",
    "temporarily blocked",
    "captcha",
    # Geo-restriction - retriable via a proxy in another region.
    "not made this video available in your country",
    "not available in your country",
    "blocked it in your country",
    "available in your country",
)

# Substrings that mean retrying is pointless - fail fast and save the budget.
#
# These are matched against yt-dlp's stderr, which phrases the same condition
# several ways depending on which player client answered: a dead video comes
# back as "Video unavailable" on one client and "This video is unavailable" on
# another. Matching only the first form meant a permanently dead video still
# burned the entire fallback ladder before the fast-fail triggered on the last
# attempt. Both phrasings are listed rather than regex-matched, because an
# explicit list is easier to extend when YouTube changes its wording again.
PERMANENT_SIGNATURES = (
    "video unavailable",
    "video is unavailable",
    "video is no longer available",
    "video has been removed",
    "private video",
    "this video is private",
    "removed by the uploader",
    "account associated with this video has been terminated",
    "does not exist",
    "members-only",
    "age-restricted",
    "sign in to confirm your age",
)

# Geo-restriction sits deliberately in BLOCK_SIGNATURES rather than here.
# "Not available in your country" is permanent for *this* exit IP but not for
# the video: rotating to a proxy in another region is exactly the fix, so it
# must stay retriable. Classifying it as permanent would make the pipeline
# give up on precisely the case proxies exist to solve.


def classify_error(stderr: str) -> str:
    """Return 'blocked', 'permanent' or 'unknown' for a yt-dlp stderr blob."""
    low = stderr.lower()
    if any(sig in low for sig in PERMANENT_SIGNATURES):
        return "permanent"
    if any(sig in low for sig in BLOCK_SIGNATURES):
        return "blocked"
    return "unknown"


def _build_yt_dlp_args(
    url: str,
    yt_settings: YouTubeSettings,
    proxy: Optional[ProxyState] = None,
    player_client: str = PLAYER_CLIENTS[0],
) -> list[str]:
    """Build yt-dlp command line for metadata extraction (no download)."""
    args = yt_dlp_command() + [
        "--dump-json",
        "--no-download",
        "--no-warnings",
        "--no-playlist",
        "--skip-download",
        # Player client is a parameter, not a constant: rotating it is one of
        # the cheapest ways past a block.
        "--extractor-args", f"youtube:player_client={player_client}",
        # Add a UA
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ]
    if yt_settings.cookies_file and yt_settings.cookies_file.exists():
        args += ["--cookies", str(yt_settings.cookies_file)]
    elif yt_settings.cookies_from_browser:
        args += ["--cookies-from-browser", yt_settings.cookies_from_browser]
    if proxy:
        args += ["--proxy", proxy.url]
    args.append(url)
    return args


def _attempt_fetch(
    url: str,
    video_id: str,
    yt_settings: YouTubeSettings,
    proxy: Optional[ProxyState],
    player_client: str,
    timeout_sec: int,
) -> tuple[bool, str, dict]:
    """One metadata attempt. Returns (ok, error_message, raw_json)."""
    args = _build_yt_dlp_args(url, yt_settings, proxy, player_client)
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout_sec}s", {}

    if result.returncode != 0:
        return False, result.stderr.strip()[:500], {}

    try:
        raw = json.loads(result.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        return False, f"could not parse yt-dlp JSON: {exc}", {}

    return True, "", raw


def fetch_metadata(
    url: str,
    video_id: str,
    yt_settings: YouTubeSettings,
    proxy_pool: Optional[ProxyPool] = None,
    timeout_sec: int = 60,
    max_attempts: int = 4,
    min_interval_sec: float = 1.0,
    max_interval_sec: float = 4.0,
) -> VideoMetadata:
    """Fetch metadata, degrading through proxies and player clients.

    The candidate ladder is ordered cheapest-to-most-desperate:
      1. up to two pooled proxies on the best player client
      2. a direct connection on the best player client
      3. a direct connection on alternate player clients

    A direct attempt is *always* included even when proxies are configured, so
    a fully dead proxy pool degrades to a working run instead of failing it.
    Permanent errors (private/removed video) abort immediately rather than
    burning the whole ladder.
    """
    started = time.time()

    candidates: list[tuple[Optional[ProxyState], str]] = []
    if proxy_pool and proxy_pool.size > 0:
        # Distinct proxies only. acquire() is round-robin but skips proxies in
        # cooldown, so two consecutive calls can legitimately hand back the
        # *same* proxy when the alternates are unavailable - which would spend
        # a rung of the ladder retrying the route that just failed.
        seen: set[str] = set()
        for _ in range(min(2, proxy_pool.size)):
            p = proxy_pool.acquire()
            if p and p.url not in seen:
                seen.add(p.url)
                candidates.append((p, PLAYER_CLIENTS[0]))
    candidates.append((None, PLAYER_CLIENTS[0]))
    for client in PLAYER_CLIENTS[1:]:
        candidates.append((None, client))
    candidates = candidates[:max_attempts]

    last_error = "no attempts made"
    attempts = 0
    attempt_log: list[dict] = []

    for proxy, client in candidates:
        attempts += 1
        if attempts > 1:
            # Jittered backoff. Fixed sleeps make traffic look automated,
            # which is the thing we are trying not to look like.
            sleep_for = random.uniform(min_interval_sec, max_interval_sec)
            log.info("metadata_throttle", video_id=video_id, sleep_sec=round(sleep_for, 2))
            time.sleep(sleep_for)

        log.info(
            "metadata_fetch_attempt",
            video_id=video_id,
            attempt=attempts,
            max_attempts=len(candidates),
            proxy=proxy.url if proxy else "direct",
            client=client,
        )

        ok, err, raw = _attempt_fetch(
            url, video_id, yt_settings, proxy, client, timeout_sec
        )

        attempt_log.append({
            "attempt": attempts,
            "proxy": proxy.url if proxy else "direct",
            "client": client,
            "ok": ok,
            "kind": None if ok else classify_error(err),
            "error": None if ok else err[:200],
        })

        if ok:
            elapsed = time.time() - started
            if proxy:
                proxy_pool.report_success(proxy)
            log.info(
                "metadata_fetch_ok",
                video_id=video_id,
                title=raw.get("title", ""),
                duration_sec=raw.get("duration"),
                channel=raw.get("channel") or raw.get("uploader"),
                attempts=attempts,
                proxy=proxy.url if proxy else "direct",
                client=client,
                elapsed=round(elapsed, 2),
            )
            return _build_metadata(
                raw, url, video_id, proxy.url if proxy else "direct", elapsed,
                attempt_log,
            )

        last_error = err
        if proxy:
            proxy_pool.report_failure(proxy)

        kind = classify_error(err)
        log.warning(
            "metadata_fetch_attempt_failed",
            video_id=video_id,
            attempt=attempts,
            kind=kind,
            proxy=proxy.url if proxy else "direct",
            client=client,
            error=err[:200],
        )
        if kind == "permanent":
            # Retrying a removed or private video just wastes the ladder.
            raise MetadataError(
                f"permanent failure for {video_id}: {err}",
            )

    elapsed = time.time() - started
    log.error(
        "metadata_fetch_failed",
        video_id=video_id,
        attempts=attempts,
        elapsed=round(elapsed, 2),
        error=last_error[:300],
    )
    raise MetadataError(
        f"metadata failed for {video_id} after {attempts} attempts: {last_error}"
    )


def _build_metadata(
    raw: dict,
    url: str,
    video_id: str,
    fetched_with: str,
    elapsed: float,
    attempt_log: Optional[list[dict]] = None,
) -> VideoMetadata:
    md = VideoMetadata(
        video_id=raw.get("id", video_id),
        url=raw.get("webpage_url", url),
        title=raw.get("title", ""),
        description=raw.get("description", "") or "",
        duration_sec=int(raw.get("duration") or 0),
        channel=raw.get("channel") or raw.get("uploader") or "",
        uploader=raw.get("uploader") or "",
        upload_date=raw.get("upload_date") or "",
        view_count=int(raw.get("view_count") or 0),
        like_count=int(raw.get("like_count") or 0),
        tags=list(raw.get("tags") or []),
        thumbnail_url=raw.get("thumbnail") or "",
        webpage_url=raw.get("webpage_url", url),
        license=raw.get("license") or "",
        categories=list(raw.get("categories") or []),
        comment_count=int(raw.get("comment_count") or 0),
        age_limit=int(raw.get("age_limit") or 0),
        availability=raw.get("availability") or "",
        live_status=raw.get("live_status") or "",
        language=raw.get("language") or "",
        chapters=list(raw.get("chapters") or []),
        width=int(raw.get("width") or 0),
        height=int(raw.get("height") or 0),
        fps=float(raw.get("fps") or 0.0),
        has_captions=bool(raw.get("subtitles") or raw.get("automatic_captions")),
        caption_languages=sorted(
            set(list((raw.get("subtitles") or {}).keys())
                + list((raw.get("automatic_captions") or {}).keys()))
        )[:10],
        raw=raw,
        fetched_with=fetched_with,
        fetch_duration_sec=elapsed,
        attempt_log=attempt_log or [],
    )
    return md
