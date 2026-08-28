"""
Runtime helpers for finding yt-dlp in different environments.

The pipeline invokes yt-dlp as a subprocess. Where that binary lives - and
whether it is allowed to run at all - varies enough between machines that it
is worth resolving carefully, once, at first use.

Two lessons are baked in here:

Existing is not the same as runnable. Windows Application Control (and
Smart App Control on consumer builds) blocks unsigned executables, so
`.venv\\Scripts\\yt-dlp.exe` can be present and still fail with
`WinError 4551` the moment it is executed. The old code checked only that the
file existed, picked it, and every download then failed. Each candidate is
now actually run once before being trusted.

`python -m yt_dlp` is the reliable floor. It runs inside the interpreter that
is already executing - which the machine has clearly already permitted - so
it survives policies that block standalone binaries. It is marginally slower
to start, which is irrelevant next to a download.
"""
from __future__ import annotations

import subprocess
import shutil
import sys
from pathlib import Path

from logging_setup import get_logger

log = get_logger(__name__)

# Resolved command, cached as a list so paths containing spaces survive.
# The previous implementation cached a string and re-split it on whitespace,
# which would have broken on any path like C:\Program Files\...
_YT_DLP_CACHE: list[list[str] | None] = [None]

_PROBE_TIMEOUT_SEC = 20


def _runs(command: list[str]) -> bool:
    """Check a candidate actually executes, rather than merely existing."""
    try:
        result = subprocess.run(
            [*command, "--version"],
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT_SEC,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # OSError covers the blocked-by-policy case: Python raises it with
        # winerror 4551 when the OS refuses to start the process.
        log.info("yt_dlp_candidate_rejected", command=command[0], error=str(exc)[:160])
        return False
    if result.returncode != 0:
        log.info(
            "yt_dlp_candidate_rejected",
            command=command[0], returncode=result.returncode,
            stderr=result.stderr.strip()[:160],
        )
        return False
    return True


def yt_dlp_command() -> list[str]:
    """Return the command prefix used to invoke yt-dlp.

    Candidates are tried in order and each is verified by running it:
        1. yt-dlp on PATH
        2. yt-dlp next to the current interpreter (the virtualenv)
        3. python -m yt_dlp
    """
    if _YT_DLP_CACHE[0] is not None:
        return list(_YT_DLP_CACHE[0])

    candidates: list[list[str]] = []

    found = shutil.which("yt-dlp")
    if found:
        candidates.append([found])

    exe_dir = Path(sys.executable).parent
    for name in ("yt-dlp.exe", "yt-dlp", "yt_dlp.exe", "yt_dlp"):
        candidate = exe_dir / name
        if candidate.exists():
            candidates.append([str(candidate)])

    module_form = [sys.executable, "-m", "yt_dlp"]
    candidates.append(module_form)

    for candidate in candidates:
        if _runs(candidate):
            if candidate is not candidates[0]:
                log.info("yt_dlp_resolved", command=candidate[0], fallback=True)
            _YT_DLP_CACHE[0] = candidate
            return list(candidate)

    # Nothing ran. Cache the module form anyway so the caller gets a real
    # yt-dlp error rather than a confusing empty command.
    log.error(
        "yt_dlp_unresolvable",
        tried=[c[0] for c in candidates],
        hint="yt-dlp could not be executed by any method. On Windows this is "
             "usually an Application Control policy blocking unsigned binaries.",
    )
    _YT_DLP_CACHE[0] = module_form
    return list(module_form)
