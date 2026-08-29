#!/usr/bin/env bash
# Setup for macOS and Linux. Windows: run setup.ps1 instead.
set -euo pipefail
cd "$(dirname "$0")"

command -v ffmpeg >/dev/null || {
  echo "ERROR: ffmpeg not found."
  echo "  macOS : brew install ffmpeg"
  echo "  Ubuntu: sudo apt install ffmpeg"
  exit 1
}

PY=$(command -v python3.11 || command -v python3)
"$PY" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt

[ -f .env ] || cp .env.example .env

cat <<'MSG'

Setup complete.

  1. Check it works (no credentials needed - 184 tests, no network):
       .venv/bin/python -m pytest

  2. Configure it (asks brand vs general use, and where to publish):
       .venv/bin/python setup_wizard.py

  3. Run it:
       .venv/bin/python run.py --max 5 --publisher local --brand generic

The pipeline runs with NO credentials at all - it falls back to rule-based
checks and still produces real videos. Everything below is an upgrade:

  LLM_API_KEY in .env      AI summaries, titles and brand-relevance scoring.
                           Without it the AI stages are skipped entirely -
                           the run still succeeds, but the metadata is a
                           copy of YouTube's own.

  Google credentials       Upload to Google Drive, and drive the whole
                           pipeline from a Google Sheet. See docs/INSTALL.md.

MSG
