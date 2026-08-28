"""
Stage 3: Anti-bot hardened YouTube downloader.

Defense in depth:
    1. Multiple player_clients (android, web, ios, tv_embedded)
    2. Cookies file / browser-cookie auth (optional, dramatically helps)
    3. Proxy rotation via ProxyPool
    4. PO Token plugin (bgutil-ytdlp-pot-provider) - if installed
    5. Exponential backoff + jitter
    6. Format restriction to short-form (<=1080p, mp4 + m4a)
    7. Hard ceiling: max retries per video before giving up

Each download goes through a "candidate" chain: try direct -> try proxy A -> try
proxy B -> try with cookies -> give up. The LangGraph agent orchestrates this
by retrying with progressively more aggressive settings.
"""
from __future__ import annotations

import random
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config import SafetySettings, YouTubeSettings
from logging_setup import get_logger
from pipeline._runtime import yt_dlp_command
from pipeline.metadata import classify_error
from safety.proxy_health import ProxyPool, ProxyState

log = get_logger(__name__)

# Max file size cap: 500MB - refuse anything bigger to protect disk
MAX_FILE_BYTES = 500 * 1024 * 1024

# Player clients in fallback order
PLAYER_CLIENTS = ["android,web,ios,tv_embedded", "web_safari,ios", "android", "web", "ios"]


@dataclass
class DownloadResult:
    success: bool
    video_id: str
    file_path: Optional[Path] = None
    attempts: int = 0
    last_error: str = ""
    file_size_bytes: int = 0
    proxy_used: str = "direct"
    elapsed_sec: float = 0.0
    client_used: str = ""
    attempt_log: list[dict] = field(default_factory=list)


def _build_yt_dlp_args(
    url: str,
    output_path: Path,
    yt_settings: YouTubeSettings,
    proxy: Optional[ProxyState],
    player_clients: str,
    extra_retries: int = 3,
) -> list[str]:
    args = yt_dlp_command() + [
        "--no-warnings",
        "--no-playlist",
        "--no-part",               # Don't write .part files
        "--no-mtime",              # Don't set mtime
        "--retries", str(extra_retries),
        "--fragment-retries", str(extra_retries),
        # Format: best short-form video + audio, capped at 1080p
        "-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/b[ext=mp4]/b",
        "--merge-output-format", "mp4",
        # Player client rotation
        "--extractor-args", f"youtube:player_client={player_clients}",
        # User agent
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        # Output template
        "-o", str(output_path),
    ]
    if yt_settings.cookies_file and yt_settings.cookies_file.exists():
        args += ["--cookies", str(yt_settings.cookies_file)]
    elif yt_settings.cookies_from_browser:
        args += ["--cookies-from-browser", yt_settings.cookies_from_browser]
    if proxy:
        args += ["--proxy", proxy.url]
    args.append(url)
    return args


def _attempt_download(
    url: str,
    output_path: Path,
    yt_settings: YouTubeSettings,
    proxy: Optional[ProxyState],
    player_clients: str,
    timeout_sec: int,
) -> tuple[bool, str, int]:
    """Make one download attempt. Returns (success, error_or_empty, file_size)."""
    args = _build_yt_dlp_args(url, output_path, yt_settings, proxy, player_clients)
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout_sec}s", 0

    if result.returncode != 0:
        return False, result.stderr.strip()[:400] or f"exit {result.returncode}", 0

    if not output_path.exists():
        return False, "yt-dlp reported success but file not found", 0

    size = output_path.stat().st_size
    if size > MAX_FILE_BYTES:
        output_path.unlink(missing_ok=True)
        return False, f"file too large: {size} bytes", 0

    if size < 1024:
        # Less than 1KB - probably a fake / error page
        output_path.unlink(missing_ok=True)
        return False, f"file too small: {size} bytes (likely error page)", 0

    return True, "", size


def download_video(
    url: str,
    video_id: str,
    output_dir: Path,
    yt_settings: YouTubeSettings,
    safety_settings: SafetySettings,
    proxy_pool: Optional[ProxyPool] = None,
    # 6 covers 2 proxies + all 5 player clients, trimmed by the slice below.
    max_attempts: int = 6,
    timeout_sec: int = 240,
    min_interval_sec: Optional[float] = None,
    max_interval_sec: Optional[float] = None,
) -> DownloadResult:
    """
    Download a single video with defense-in-depth retry.

    Tries multiple player clients and proxies in sequence. Gives up after
    `max_attempts` total attempts.
    """
    min_int = min_interval_sec if min_interval_sec is not None else safety_settings.download_min_interval_sec
    max_int = max_interval_sec if max_interval_sec is not None else safety_settings.download_max_interval_sec

    output_path = output_dir / f"{video_id}.mp4"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        # Reuse
        size = output_path.stat().st_size
        if size > 1024:
            log.info("download_reused", video_id=video_id, size=size)
            return DownloadResult(
                success=True, video_id=video_id, file_path=output_path,
                file_size_bytes=size, proxy_used="(reused)", elapsed_sec=0.0,
                client_used="(reused)", attempts=0,
            )

    started = time.time()
    last_error = "no attempts"
    attempts = 0
    proxy_used = "direct"
    client_used = ""
    # Per-attempt trail, including failures - see the note in pipeline.metadata.
    attempt_log: list[dict] = []

    # Build the candidate ladder: each entry is (proxy, player_client).
    # Ordered cheapest-to-most-desperate, and deliberately identical in shape
    # to the one in pipeline.metadata so both stages degrade the same way.
    #
    # Previously this only ever offered two player clients, so a run with no
    # proxies exhausted its ladder after trying 2 of the 5 - while the
    # metadata stage tried all 5. Client rotation is the cheapest lever
    # against a block, so there is no reason to leave three of them unused.
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
    # A direct attempt is always present, so a fully dead proxy pool degrades
    # to a working run rather than failing one.
    candidates.append((None, PLAYER_CLIENTS[0]))
    for client in PLAYER_CLIENTS[1:]:
        candidates.append((None, client))

    candidates = candidates[:max_attempts]

    for proxy, client in candidates:
        attempts += 1
        # Throttle between attempts
        if attempts > 1:
            sleep_for = random.uniform(min_int, max_int)
            log.info("download_throttle", video_id=video_id, sleep_sec=round(sleep_for, 2))
            time.sleep(sleep_for)

        log.info(
            "download_attempt",
            video_id=video_id,
            attempt=attempts,
            max_attempts=len(candidates),
            proxy=proxy.url if proxy else "direct",
            client=client,
        )

        ok, err, size = _attempt_download(
            url, output_path, yt_settings, proxy, client, timeout_sec
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
            proxy_used = proxy.url if proxy else "direct"
            client_used = client
            log.info(
                "download_ok",
                video_id=video_id,
                attempts=attempts,
                size_bytes=size,
                proxy=proxy_used,
                client=client,
                elapsed=round(elapsed, 2),
            )
            if proxy:
                proxy_pool.report_success(proxy)
            return DownloadResult(
                success=True, video_id=video_id, file_path=output_path,
                attempts=attempts, file_size_bytes=size,
                proxy_used=proxy_used, elapsed_sec=elapsed, client_used=client,
                attempt_log=attempt_log,
            )

        last_error = err
        if proxy:
            proxy_pool.report_failure(proxy)

    # All attempts failed
    elapsed = time.time() - started
    log.error(
        "download_failed",
        video_id=video_id,
        attempts=attempts,
        elapsed=round(elapsed, 2),
        last_error=last_error,
    )
    return DownloadResult(
        success=False, video_id=video_id, file_path=None,
        attempts=attempts, last_error=last_error, proxy_used="(none)",
        elapsed_sec=elapsed, client_used="(none)",
    )
