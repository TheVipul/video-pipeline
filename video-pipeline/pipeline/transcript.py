"""
Caption / transcript extraction.

Fetches a video's subtitles without downloading the video itself, so the
pipeline can understand what a video actually *says* before deciding whether
to spend bandwidth on it. That ordering matters: a transcript costs a few KB,
the video costs tens of megabytes.

Manual subtitles are preferred over auto-generated ones - they are punctuated
and correctly spelled, which produces a noticeably better summary.

Not every video has captions. Silent footage (drone shots, animation,
b-roll) usually has none at all, and the caller must handle that: this module
returns None rather than raising, and the LLM falls back to summarising the
description instead.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from config import YouTubeSettings
from logging_setup import get_logger
from pipeline._runtime import yt_dlp_command

log = get_logger(__name__)

# Cap what we feed the model. ~12k characters is roughly 3k tokens - plenty
# for a summary of a long video, and a hard bound on per-video LLM cost.
MAX_TRANSCRIPT_CHARS = 12_000

# Request exactly "en", never a glob. `en.*` pulls machine-translated variants
# (en-de, en-es, ...) which are redundant, slower, and trip YouTube's rate
# limiter - a 429 mid-run for content we would have discarded anyway.
SUB_LANGS = "en"


@dataclass
class Transcript:
    text: str
    source: str          # "manual" | "automatic"
    language: str
    char_count: int
    truncated: bool = False


def _parse_json3(path: Path) -> str:
    """Extract plain text from yt-dlp's json3 subtitle format."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("transcript_parse_failed", path=str(path), error=str(exc))
        return ""

    parts: list[str] = []
    for event in data.get("events") or []:
        for seg in event.get("segs") or []:
            text = (seg.get("utf8") or "").strip()
            if text:
                parts.append(text)

    # Caption cues break mid-sentence, so joining on a single space and
    # collapsing whitespace reads far better than preserving line structure.
    return " ".join(" ".join(parts).split())


def fetch_transcript(
    url: str,
    video_id: str,
    yt_settings: YouTubeSettings,
    timeout_sec: int = 60,
) -> Optional[Transcript]:
    """Fetch English captions for a video. Returns None when none exist."""
    with tempfile.TemporaryDirectory(prefix=f"subs_{video_id}_") as tmp:
        tmpdir = Path(tmp)
        args = yt_dlp_command() + [
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs", SUB_LANGS,
            "--sub-format", "json3",
            "--no-warnings",
            "--no-playlist",
            "--extractor-args", "youtube:player_client=android,web,ios,tv_embedded",
            "-o", str(tmpdir / "%(id)s.%(ext)s"),
        ]
        if yt_settings.cookies_file and yt_settings.cookies_file.exists():
            args += ["--cookies", str(yt_settings.cookies_file)]
        args.append(url)

        try:
            subprocess.run(args, capture_output=True, text=True, timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            log.warning("transcript_timeout", video_id=video_id)
            return None

        files = sorted(tmpdir.glob("*.json3"))
        if not files:
            log.info("transcript_unavailable", video_id=video_id)
            return None

        # yt-dlp does not label manual vs automatic in the filename, but it
        # writes plain "<id>.en.json3" for manual tracks and a longer variant
        # for generated ones. Prefer the shortest name.
        chosen = min(files, key=lambda f: len(f.name))
        text = _parse_json3(chosen)
        if not text:
            return None

        truncated = len(text) > MAX_TRANSCRIPT_CHARS
        if truncated:
            text = text[:MAX_TRANSCRIPT_CHARS]

        log.info(
            "transcript_ok",
            video_id=video_id,
            chars=len(text),
            truncated=truncated,
            file=chosen.name,
        )
        return Transcript(
            text=text,
            source="manual" if chosen.name.count(".") <= 2 else "automatic",
            language="en",
            char_count=len(text),
            truncated=truncated,
        )
