"""Shared Google OAuth for Drive, Sheets and YouTube.

All three integrations authenticate the same way against the same Google
Cloud project, so the consent dance lives here once rather than being copied
into each publisher.

Scopes are requested together and cached in a single token file. That matters
for usability: the operator clicks through one consent screen, not three. It
also means adding a scope later invalidates the cached token and re-prompts,
which is the correct behaviour - a token must never silently carry more
authority than the user agreed to.

Scope choices are deliberately narrow:
  drive.file      - only files this app creates. It cannot read or touch
                    anything else in the user's Drive, which is the right
                    default for a tool that uploads on someone's behalf.
  spreadsheets    - read/write sheets (needed to write results back).
  youtube.upload  - upload only; cannot read or modify the channel.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from logging_setup import get_logger

log = get_logger(__name__)

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"

DEFAULT_CREDENTIALS_FILE = Path("inputs/client_secret.json")
DEFAULT_TOKEN_FILE = Path("inputs/google_token.json")


class GoogleAuthError(RuntimeError):
    """Raised with actionable setup instructions rather than a stack trace."""


SETUP_HELP = """
Google API credentials are not set up yet. One-time setup, ~5 minutes:

  1. https://console.cloud.google.com  ->  create a project
  2. APIs & Services -> Library -> enable the APIs you need:
       Google Drive API      (to upload finished videos)
       Google Sheets API     (to read inputs / write results)
       YouTube Data API v3   (only if publishing to YouTube)
  3. APIs & Services -> OAuth consent screen -> External
       -> add your own Google account under "Test users"
  4. Credentials -> Create Credentials -> OAuth client ID -> Desktop app
       -> Download JSON
  5. Save it to: {path}

The first run opens a browser for consent and caches a refresh token, so this
only happens once.
""".strip()


def load_credentials(
    scopes: Iterable[str],
    credentials_file: Optional[Path] = None,
    token_file: Optional[Path] = None,
    allow_interactive: bool = True,
):
    """Return authorised Google credentials, running consent if needed.

    `allow_interactive=False` is for unattended processes such as the sheet
    watcher. Consent opens a browser and blocks until somebody completes it -
    on a headless or background process that is a hang nobody sees, so it is
    better to fail with an instruction than to wait forever.
    """
    credentials_file = Path(credentials_file or DEFAULT_CREDENTIALS_FILE)
    token_file = Path(token_file or DEFAULT_TOKEN_FILE)
    scopes = list(scopes)

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise GoogleAuthError(
            "Google client libraries are missing. Install with:\n"
            "  pip install google-api-python-client google-auth-oauthlib"
        ) from exc

    creds = None
    if token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_file), scopes)
        except ValueError:
            log.info("google_token_unreadable_reconsenting", token=str(token_file))
            creds = None

        # from_authorized_user_file does NOT validate scopes - it simply
        # attaches the ones we asked for. If the cached token was granted a
        # narrower set, nothing complains until the refresh, which then fails
        # with an opaque "invalid_scope: Bad Request". Compare explicitly.
        #
        # This is exactly what happens when a token is created by a Drive-only
        # command and then reused by something that also needs Sheets.
        if creds is not None:
            granted = set(creds.scopes or [])
            missing = set(scopes) - granted
            if missing:
                log.info(
                    "google_token_missing_scopes_reconsenting",
                    token=str(token_file),
                    missing=sorted(missing),
                    granted=sorted(granted),
                )
                creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save(creds, token_file)
            return creds
        except Exception as exc:  # noqa: BLE001 - fall through to full consent
            log.warning("google_token_refresh_failed", error=str(exc))

    if not allow_interactive:
        raise GoogleAuthError(
            "Google authorisation is needed and cannot be completed "
            "automatically.\n\n"
            "Run this on the machine itself, complete the browser consent, "
            "then start the watcher again:\n"
            "    python run.py --max 1 --publisher gdrive\n\n"
            f"(token file: {token_file})"
        )

    if not credentials_file.exists():
        raise GoogleAuthError(SETUP_HELP.format(path=credentials_file))

    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), scopes)
    creds = flow.run_local_server(port=0)
    _save(creds, token_file)
    return creds


def _save(creds, token_file: Path) -> None:
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json())
    # The token grants API access on the user's behalf - keep it owner-only.
    try:
        token_file.chmod(0o600)
    except OSError:
        pass
    log.info("google_token_cached", path=str(token_file))


def build_service(api: str, version: str, scopes: Iterable[str], **kwargs):
    """Build an authorised Google API client."""
    from googleapiclient.discovery import build  # noqa: PLC0415

    creds = load_credentials(scopes, **kwargs)
    return build(api, version, credentials=creds, cache_discovery=False)
