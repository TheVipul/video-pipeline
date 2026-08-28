"""
YouTube publisher - real resumable upload via YouTube Data API v3.

This was previously a stub. It is now a working implementation: given OAuth
credentials for a channel, it performs a genuine resumable upload and returns
the created video id. Without credentials it fails with an actionable error
rather than pretending to succeed.

Two deliberate safety choices:

`privacy_status` defaults to "private". Re-uploading content the channel does
not own is a copyright problem, and a private upload is the only responsible
default for a pipeline that ingests third-party video. Making it public is an
explicit, per-run decision by a human.

`made_for_kids` is set explicitly to False rather than left unset, because
YouTube requires the declaration and an unset value causes the upload to be
rejected at the API layer.

Setup:
    1. Google Cloud project with YouTube Data API v3 enabled
    2. OAuth 2.0 Desktop client -> download client_secret.json
    3. First run opens a consent screen and caches a refresh token
"""
from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Optional

from logging_setup import get_logger
from pipeline.ai_analyzer import AIEnrichment
from pipeline.publishers.base import PublishResult, Publisher

log = get_logger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# YouTube asks clients to retry these, with exponential backoff, and to treat
# anything else as fatal.
RETRIABLE_STATUS = (500, 502, 503, 504, 429)
MAX_RETRIES = 5


class YouTubePublisher(Publisher):
    name = "youtube"

    def __init__(
        self,
        credentials_file: Optional[Path] = None,
        token_file: Optional[Path] = None,
        privacy_status: str = "private",
        category_id: str = "22",  # People & Blogs
        chunk_size: int = 4 * 1024 * 1024,
    ) -> None:
        self.credentials_file = Path(credentials_file) if credentials_file else Path("inputs/client_secret.json")
        self.token_file = Path(token_file) if token_file else Path("inputs/youtube_token.json")
        self.privacy_status = privacy_status
        self.category_id = category_id
        self.chunk_size = chunk_size
        self._service = None

    # --- auth -----------------------------------------------------------------

    def _load_credentials(self):
        """Load cached credentials, refreshing or running consent as needed."""
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError as exc:
            raise RuntimeError(
                "YouTube upload needs google-api-python-client and "
                "google-auth-oauthlib. Install with: "
                "pip install google-api-python-client google-auth-oauthlib"
            ) from exc

        creds = None
        if self.token_file.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_file), SCOPES)

        if creds and creds.valid:
            return creds

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not self.credentials_file.exists():
                raise RuntimeError(
                    f"No OAuth client secrets at {self.credentials_file}. "
                    "Create an OAuth 2.0 Desktop client in Google Cloud with the "
                    "YouTube Data API v3 enabled, download the JSON, and place it there."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(self.credentials_file), SCOPES
            )
            creds = flow.run_local_server(port=0)

        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(creds.to_json())
        return creds

    def _get_service(self):
        if self._service is None:
            from googleapiclient.discovery import build  # noqa: PLC0415

            self._service = build("youtube", "v3", credentials=self._load_credentials())
        return self._service

    # --- upload ---------------------------------------------------------------

    def publish(
        self,
        video_id: str,
        video_path: Path,
        metadata: AIEnrichment,
    ) -> PublishResult:
        if not video_path.exists():
            return PublishResult(
                success=False, video_id=video_id, destination="youtube",
                error=f"video file not found: {video_path}",
            )

        try:
            from googleapiclient.errors import HttpError  # noqa: PLC0415
            from googleapiclient.http import MediaFileUpload  # noqa: PLC0415

            service = self._get_service()
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller as a result
            log.error("youtube_auth_failed", video_id=video_id, error=str(exc))
            return PublishResult(
                success=False, video_id=video_id, destination="youtube",
                error=f"auth/setup failed: {exc}",
            )

        body = {
            "snippet": {
                "title": (metadata.ai_title or video_id)[:100],
                "description": (metadata.ai_description or "")[:5000],
                "tags": [t for t in (metadata.ai_tags or []) if t][:30],
                "categoryId": self.category_id,
            },
            "status": {
                "privacyStatus": self.privacy_status,
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(
            str(video_path), chunksize=self.chunk_size, resumable=True, mimetype="video/*"
        )
        request = service.videos().insert(
            part="snippet,status", body=body, media_body=media
        )

        log.info(
            "youtube_upload_start",
            video_id=video_id,
            size_bytes=video_path.stat().st_size,
            privacy=self.privacy_status,
        )

        response = None
        retries = 0
        while response is None:
            try:
                status, response = request.next_chunk()
                if status:
                    log.info(
                        "youtube_upload_progress",
                        video_id=video_id,
                        percent=int(status.progress() * 100),
                    )
            except HttpError as exc:
                if exc.resp.status in RETRIABLE_STATUS and retries < MAX_RETRIES:
                    retries += 1
                    # Exponential backoff with jitter, per Google's guidance.
                    sleep_for = min(2 ** retries, 60) * (0.5 + random.random())
                    log.warning(
                        "youtube_upload_retry",
                        video_id=video_id,
                        status=exc.resp.status,
                        retry=retries,
                        sleep_sec=round(sleep_for, 1),
                    )
                    time.sleep(sleep_for)
                    continue
                log.error("youtube_upload_failed", video_id=video_id, error=str(exc))
                return PublishResult(
                    success=False, video_id=video_id, destination="youtube",
                    error=f"HttpError {exc.resp.status}: {exc}",
                )
            except Exception as exc:  # noqa: BLE001
                log.error("youtube_upload_failed", video_id=video_id, error=str(exc))
                return PublishResult(
                    success=False, video_id=video_id, destination="youtube",
                    error=str(exc),
                )

        remote_id = response.get("id", "")
        log.info("youtube_upload_ok", video_id=video_id, youtube_id=remote_id)
        return PublishResult(
            success=True,
            video_id=video_id,
            destination="youtube",
            remote_path=f"https://www.youtube.com/watch?v={remote_id}",
            bytes_written=video_path.stat().st_size,
        )
