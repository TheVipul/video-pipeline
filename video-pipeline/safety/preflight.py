"""
Pre-flight checks before the pipeline starts.

Validates that the host has everything it needs:
    - Python version OK
    - FFmpeg installed
    - LLM API key (if expected)
    - Disk space sufficient
    - YouTube reachable
    - URLs file exists and has at least 1 valid entry
    - Output dir writable
"""
from __future__ import annotations

import shutil
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config import Settings
from logging_setup import get_logger

log = get_logger(__name__)


@dataclass
class PreflightResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.ok = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


def check_ffmpeg() -> tuple[bool, Optional[str]]:
    path = shutil.which("ffmpeg")
    if not path:
        return False, "ffmpeg not found on PATH"
    return True, path


def check_disk_space(path: Path, min_gb: float) -> tuple[bool, str]:
    import os
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(path).free
    free_gb = free / (1024 ** 3)
    return free_gb >= min_gb, f"{free_gb:.2f} GB free at {path}"


def check_youtube_reachable(timeout_sec: float = 5.0) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(
            "https://www.youtube.com/",
            headers={"User-Agent": "Mozilla/5.0 (CSC-VideoPipeline/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=timeout_sec) as r:
            return r.status == 200, f"YouTube status {r.status}"
    except (urllib.error.URLError, socket.timeout) as exc:
        return False, f"YouTube unreachable: {exc}"


def check_pypi_reachable(timeout_sec: float = 5.0) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen("https://pypi.org", timeout=timeout_sec) as r:
            return r.status == 200, f"PyPI status {r.status}"
    except (urllib.error.URLError, socket.timeout) as exc:
        return False, f"PyPI unreachable: {exc}"


def run_preflight(settings: Settings, urls_file: Path) -> PreflightResult:
    """Run all preflight checks. Return a result object - the pipeline should not start on errors."""
    result = PreflightResult(ok=True)

    # Python version
    import sys
    if sys.version_info < (3, 11):
        result.add_error(f"Python >=3.11 required, got {sys.version_info.major}.{sys.version_info.minor}")

    # FFmpeg
    ok, info = check_ffmpeg()
    if ok:
        log.info("preflight_ffmpeg_ok", path=info)
    else:
        result.add_error(info)

    # Disk
    output = settings.pipeline.output_dir
    ok, info = check_disk_space(output, settings.safety.disk_min_free_gb)
    if ok:
        log.info("preflight_disk_ok", info=info)
    else:
        result.add_error(f"Insufficient disk space: {info}, need >= {settings.safety.disk_min_free_gb} GB")

    # YouTube reachability
    ok, info = check_youtube_reachable()
    if ok:
        log.info("preflight_youtube_ok", info=info)
    else:
        result.add_error(info)

    # URLs file
    if not urls_file.exists():
        result.add_error(f"URLs file not found: {urls_file}")
    else:
        # Quick count
        with urls_file.open("r", encoding="utf-8") as f:
            non_empty = sum(
                1 for line in f
                if line.strip() and not line.strip().startswith("#")
            )
        if non_empty == 0:
            result.add_error(f"URLs file has no usable entries: {urls_file}")
        elif non_empty < settings.pipeline.max_videos:
            result.add_warning(
                f"URLs file has {non_empty} entries, max_videos={settings.pipeline.max_videos}. "
                f"Will stop early."
            )
        else:
            log.info("preflight_urls_ok", count=non_empty)

    # LLM
    if not settings.llm_enabled:
        result.add_warning("LLM_API_KEY not set - pipeline will run WITHOUT AI metadata enrichment.")

    # Cookies (optional)
    if settings.youtube.cookies_file and not settings.youtube.cookies_file.exists():
        result.add_warning(
            f"YT_COOKIES_FILE set but file not found: {settings.youtube.cookies_file}. "
            f"Continuing without cookies - higher bot-detection risk."
        )

    return result
