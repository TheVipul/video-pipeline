"""
Runtime helpers for finding yt-dlp in different environments.

The pipeline invokes yt-dlp as a subprocess. On Windows, the binary lives in
.venv\\Scripts\\yt-dlp.exe (or .venv/bin/yt-dlp on Unix). We resolve it once at
import time and use that explicit path everywhere.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

_YT_DLP_CACHE: list[str | None] = [None]


def yt_dlp_command() -> list[str]:
    """
    Return the command prefix to invoke yt-dlp.

    Tries:
        1. shutil.which("yt-dlp")
        2. <sys.executable-dir>/yt-dlp(.exe)
        3. python -m yt_dlp (always works, slowest)
    """
    if _YT_DLP_CACHE[0] is not None:
        return _YT_DLP_CACHE[0].split() if not _YT_DLP_CACHE[0].startswith("python") else [sys.executable, "-m", "yt_dlp"]

    # 1. PATH
    found = shutil.which("yt-dlp")
    if found:
        _YT_DLP_CACHE[0] = found
        return [found]

    # 2. Next to current Python executable
    exe_dir = Path(sys.executable).parent
    for name in ("yt-dlp.exe", "yt-dlp", "yt_dlp.exe", "yt_dlp"):
        candidate = exe_dir / name
        if candidate.exists():
            _YT_DLP_CACHE[0] = str(candidate)
            return [str(candidate)]

    # 3. Python module form
    _YT_DLP_CACHE[0] = f"{sys.executable} -m yt_dlp"
    return [sys.executable, "-m", "yt_dlp"]
