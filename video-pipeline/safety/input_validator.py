"""
URL input validation.

Hard rules:
    - Only YouTube domains allowed (youtube.com, youtu.be, youtube-nocookie.com)
    - Watch/shorts/embed paths only (no channel/user/playlist)
    - Per-video duration cap
    - Path-traversal safe filenames
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from config import PipelineSettings
from logging_setup import get_logger

log = get_logger(__name__)

ALLOWED_DOMAINS = frozenset({
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
})

# Path rules by domain family
YOUTUBE_PATH = re.compile(r"^/(watch|shorts|embed)(/|$|\?|#)", re.IGNORECASE)
YOUTUBE_NOCOOKIE_PATH = re.compile(r"^/embed(/|$|\?|#)", re.IGNORECASE)
YOUTU_BE_PATH = re.compile(r"^/[A-Za-z0-9_-]{11}(/|$|\?|#)", re.IGNORECASE)
YOUTUBE_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def ALLOWED_PATHS_BY_DOMAIN(domain: str, path: str) -> bool:
    """Return True if the path is allowed for the given domain family."""
    if domain == "youtu.be":
        return bool(YOUTU_BE_PATH.match(path))
    if domain == "youtube-nocookie.com" or domain == "www.youtube-nocookie.com":
        return bool(YOUTUBE_NOCOOKIE_PATH.match(path))
    # youtube.com / www.youtube.com / m.youtube.com
    return bool(YOUTUBE_PATH.match(path))


@dataclass
class ValidatedURL:
    url: str
    video_id: str
    video_type: str  # "watch" | "shorts" | "embed"
    original: str

    def to_youtube_url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


class InputValidationError(ValueError):
    pass


def validate_url(url: str) -> ValidatedURL:
    """
    Validate a single YouTube URL.

    Raises InputValidationError if the URL is not allowed.
    Returns a normalized ValidatedURL.
    """
    if not url or not isinstance(url, str):
        raise InputValidationError(f"URL must be a non-empty string, got {type(url).__name__}")

    raw = url.strip()
    if not raw:
        raise InputValidationError("URL is empty after stripping")

    try:
        parsed = urlparse(raw)
    except Exception as exc:
        raise InputValidationError(f"Could not parse URL: {raw!r} ({exc})")

    if parsed.scheme not in ("http", "https"):
        raise InputValidationError(f"URL scheme must be http(s), got {parsed.scheme!r}")

    if parsed.netloc.lower() not in ALLOWED_DOMAINS:
        raise InputValidationError(
            f"Domain {parsed.netloc!r} not in allowlist. Allowed: {sorted(ALLOWED_DOMAINS)}"
        )

    if not ALLOWED_PATHS_BY_DOMAIN(parsed.netloc.lower(), parsed.path):
        raise InputValidationError(
            f"Path {parsed.path!r} not allowed for domain {parsed.netloc!r}. "
            f"Must be a valid YouTube video path."
        )

    video_id: Optional[str] = None
    if parsed.netloc.lower() == "youtu.be":
        # youtu.be/<id>
        candidate = parsed.path.lstrip("/").split("/")[0]
        if YOUTUBE_VIDEO_ID.match(candidate):
            video_id = candidate
    else:
        # youtube.com/watch?v=<id>
        qs = parse_qs(parsed.query)
        if "v" in qs and YOUTUBE_VIDEO_ID.match(qs["v"][0]):
            video_id = qs["v"][0]
        else:
            # /shorts/<id> or /embed/<id>
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) >= 2 and YOUTUBE_VIDEO_ID.match(parts[-1]):
                video_id = parts[-1]

    if not video_id:
        raise InputValidationError(f"Could not extract 11-char YouTube video ID from {raw!r}")

    if parsed.path.startswith("/shorts") or parsed.path.startswith("/shorts/"):
        vtype = "shorts"
    elif parsed.path.startswith("/embed"):
        vtype = "embed"
    else:
        vtype = "watch"

    return ValidatedURL(
        url=f"https://www.youtube.com/watch?v={video_id}",
        video_id=video_id,
        video_type=vtype,
        original=raw,
    )


def validate_url_file(path: Path) -> list[ValidatedURL]:
    """Read a URL file (one URL per line, # comments) and return validated URLs."""
    if not path.exists():
        raise InputValidationError(f"URL file does not exist: {path}")

    valid: list[ValidatedURL] = []
    errors: list[str] = []

    with path.open("r", encoding="utf-8") as f:
        for lineno, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                valid.append(validate_url(line))
            except InputValidationError as exc:
                errors.append(f"line {lineno}: {exc}")
                log.warning("input_validation_failed", line=lineno, url=line, error=str(exc))

    if errors:
        log.warning("input_validation_summary", valid_count=len(valid), error_count=len(errors))

    return valid


def validate_duration(duration_sec: Optional[int], settings: PipelineSettings) -> None:
    """Reject if duration exceeds the cap."""
    if duration_sec is None:
        return  # unknown - yt-dlp will fetch; we re-check later
    if duration_sec > settings.max_duration_sec:
        raise InputValidationError(
            f"Video duration {duration_sec}s exceeds cap {settings.max_duration_sec}s"
        )


def sanitize_filename(name: str, max_len: int = 120) -> str:
    """
    Convert a title into a safe filename.
    - Strips path components
    - Removes/replaces illegal chars
    - Caps length
    """
    # Strip path components
    name = Path(name).name
    # Replace illegal chars
    illegal = '<>:"/\\|?*\x00'
    for ch in illegal:
        name = name.replace(ch, "_")
    # Collapse any surviving dot-runs. Traversal is already impossible once
    # separators are gone, so this is belt-and-braces - but a filename like
    # ".._.._windows_system32" is alarming in a log and trivially avoidable,
    # and a leading ".." can still confuse tools that re-join paths naively.
    name = re.sub(r"\.{2,}", "_", name)
    # Collapse whitespace
    name = re.sub(r"\s+", " ", name).strip()
    # Cap length
    if len(name) > max_len:
        name = name[:max_len].rstrip()
    return name or "untitled"
