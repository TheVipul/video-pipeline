"""
Google Drive publisher - resumable upload of finished videos.

This is the default publishing target. Drive is a deliberate choice over
YouTube for the working pipeline: uploading a video to a Drive folder is an
internal file operation with no copyright, monetisation or platform-policy
implications, whereas re-publishing third-party video to a public YouTube
channel has all three. The YouTube publisher remains available for content a
brand owns outright.

Uses the `drive.file` scope, which grants access only to files this
application creates. It cannot see or modify anything else in the user's
Drive. For a tool that uploads on someone else's behalf that is the only
defensible default.
"""
from __future__ import annotations

import json
import random
import re
import time
import unicodedata
from datetime import date
from pathlib import Path
from typing import Optional

from logging_setup import get_logger
from pipeline.ai_analyzer import AIEnrichment
from pipeline.google_auth import DRIVE_SCOPE, GoogleAuthError, build_service
from pipeline.publishers.base import PublishResult, Publisher

log = get_logger(__name__)

FOLDER_MIME = "application/vnd.google-apps.folder"

# Windows' path limit is the binding constraint for anyone who later syncs the
# Drive folder locally, so keep names comfortably short.
MAX_TITLE_CHARS = 80

# Characters Drive tolerates but that break Windows/macOS sync clients.
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE = re.compile(r"\s+")


def safe_filename(title: str, video_id: str, extension: str) -> str:
    """Build a human-readable filename that is still exactly deduplicable.

    The video id is kept as a bracketed suffix on purpose. Dedupe matches on
    filename, and the AI-generated title is not stable between runs - a
    slightly reworded title would otherwise upload a second copy instead of
    updating the first. The id makes the name unique and stable while the
    title makes it readable, which is the whole point of the change.
    """
    title = unicodedata.normalize("NFKC", title or "").strip()
    title = _UNSAFE.sub("", title)
    title = _WHITESPACE.sub(" ", title).strip(" .")
    if len(title) > MAX_TITLE_CHARS:
        title = title[:MAX_TITLE_CHARS].rstrip(" .")
    if not title:
        title = "untitled"
    return f"{title} [{video_id}].{extension}"
RETRIABLE_STATUS = (429, 500, 502, 503, 504)
MAX_RETRIES = 5


class GoogleDrivePublisher(Publisher):
    name = "gdrive"

    def __init__(
        self,
        folder_name: str = "VideoPipeline",
        subfolder: Optional[str] = None,
        credentials_file: Optional[Path] = None,
        token_file: Optional[Path] = None,
        chunk_size: int = 4 * 1024 * 1024,
        make_shareable: bool = False,
        date_folders: bool = True,
    ) -> None:
        self.folder_name = folder_name
        # Usually the brand/profile name, so one Drive can serve several
        # brands without their outputs mixing.
        self.subfolder = subfolder
        self.credentials_file = Path(credentials_file) if credentials_file else None
        self.token_file = Path(token_file) if token_file else None
        self.chunk_size = chunk_size
        # Off by default: a link that works for anyone with it is a data
        # decision, not a convenience, so it must be opted into explicitly.
        self.make_shareable = make_shareable
        # Group output by run date, so a folder that accumulates for months
        # stays navigable: VideoPipeline/<brand>/2026-08-27/...
        self.date_folders = date_folders
        self._service = None
        self._folder_cache: dict[str, str] = {}

    # --- plumbing -------------------------------------------------------------

    def _get_service(self):
        if self._service is None:
            self._service = build_service(
                "drive", "v3", [DRIVE_SCOPE],
                credentials_file=self.credentials_file,
                token_file=self.token_file,
            )
        return self._service

    def _ensure_folder(self, name: str, parent_id: Optional[str] = None) -> str:
        """Find or create a folder, returning its id. Cached per run."""
        cache_key = f"{parent_id or 'root'}/{name}"
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]

        service = self._get_service()
        # Escaping matters: a folder named  My "Brand"  would otherwise break
        # the query syntax.
        safe = name.replace("\\", "\\\\").replace("'", "\\'")
        query = (
            f"name = '{safe}' and mimeType = '{FOLDER_MIME}' and trashed = false"
        )
        if parent_id:
            query += f" and '{parent_id}' in parents"

        found = service.files().list(
            q=query, spaces="drive", fields="files(id, name)", pageSize=1
        ).execute()
        files = found.get("files", [])

        if files:
            folder_id = files[0]["id"]
        else:
            metadata = {"name": name, "mimeType": FOLDER_MIME}
            if parent_id:
                metadata["parents"] = [parent_id]
            folder_id = service.files().create(
                body=metadata, fields="id"
            ).execute()["id"]
            log.info("gdrive_folder_created", name=name, folder_id=folder_id)

        self._folder_cache[cache_key] = folder_id
        return folder_id

    def _upload(
        self, path: Path, name: str, mime: str, parent_id: str,
        video_id: str, extension: str,
    ) -> dict:
        """Resumable upload with backoff. Overwrites same-named files."""
        from googleapiclient.errors import HttpError  # noqa: PLC0415
        from googleapiclient.http import MediaFileUpload  # noqa: PLC0415

        service = self._get_service()

        # Idempotency: re-publishing a video must update the existing file,
        # not scatter "video (1).mp4" copies around.
        #
        # The search is by video id and spans every folder, not just the
        # current one. With date folders enabled, a video re-published the
        # next day would otherwise land in a new dated folder as a second
        # copy - technically a different location, practically a duplicate.
        existing = self._find_by_video_id(video_id, extension)

        media = MediaFileUpload(
            str(path), mimetype=mime, chunksize=self.chunk_size, resumable=True
        )
        if existing:
            request = service.files().update(
                fileId=existing[0]["id"], media_body=media,
                fields="id, webViewLink, size",
            )
        else:
            request = service.files().create(
                body={"name": name, "parents": [parent_id]},
                media_body=media, fields="id, webViewLink, size",
            )

        response = None
        retries = 0
        while response is None:
            try:
                status, response = request.next_chunk()
                if status:
                    log.info(
                        "gdrive_upload_progress",
                        file=name, percent=int(status.progress() * 100),
                    )
            except HttpError as exc:
                if exc.resp.status in RETRIABLE_STATUS and retries < MAX_RETRIES:
                    retries += 1
                    sleep_for = min(2 ** retries, 60) * (0.5 + random.random())
                    log.warning(
                        "gdrive_upload_retry", file=name,
                        status=exc.resp.status, retry=retries,
                        sleep_sec=round(sleep_for, 1),
                    )
                    time.sleep(sleep_for)
                    continue
                raise
        return response

    def _find_by_video_id(self, video_id: str, extension: str) -> list[dict]:
        """Find a previously published file for this video, in any folder.

        Matches on the bracketed id suffix that safe_filename() guarantees,
        so a reworded title still resolves to the same file.
        """
        service = self._get_service()
        needle = f"[{video_id}].{extension}"
        try:
            return service.files().list(
                q=f"name contains '{needle}' and trashed = false",
                spaces="drive", fields="files(id,name)", pageSize=5,
            ).execute().get("files", [])
        except Exception as exc:  # noqa: BLE001 - a failed lookup must not
            # block publishing; worst case we create a second copy.
            log.warning("gdrive_dedupe_lookup_failed", video_id=video_id, error=str(exc))
            return []

    def _target_folder(self) -> str:
        """Resolve (creating as needed) the folder this run publishes into."""
        folder_id = self._ensure_folder(self.folder_name)
        if self.subfolder:
            folder_id = self._ensure_folder(self.subfolder, folder_id)
        if self.date_folders:
            folder_id = self._ensure_folder(date.today().isoformat(), folder_id)
        return folder_id

    # --- publisher interface --------------------------------------------------

    def publish(
        self,
        video_id: str,
        video_path: Path,
        metadata: AIEnrichment,
    ) -> PublishResult:
        if not video_path.exists():
            return PublishResult(
                success=False, video_id=video_id, destination="gdrive",
                error=f"video file not found: {video_path}",
            )

        try:
            parent_id = self._target_folder()
        except GoogleAuthError as exc:
            log.error("gdrive_auth_failed", video_id=video_id)
            return PublishResult(
                success=False, video_id=video_id, destination="gdrive",
                error=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            log.error("gdrive_folder_failed", video_id=video_id, error=str(exc))
            return PublishResult(
                success=False, video_id=video_id, destination="gdrive",
                error=f"could not prepare Drive folder: {exc}",
            )

        size = video_path.stat().st_size
        log.info(
            "gdrive_upload_start", video_id=video_id, size_bytes=size,
            folder=self.subfolder or self.folder_name,
            dated=self.date_folders,
        )

        # Human-readable name, with the id kept for exact dedupe.
        title = (metadata.ai_title if metadata else "") or video_id
        video_name = safe_filename(title, video_id, "mp4")
        manifest_name = safe_filename(title, video_id, "json")

        try:
            uploaded = self._upload(
                video_path, video_name, "video/mp4", parent_id, video_id, "mp4"
            )

            # The manifest travels with the video so the folder is
            # self-describing - someone opening Drive can see why a video was
            # published and what the AI said about it.
            manifest_path = video_path.parent / f"{video_id}.json"
            manifest_path.write_text(
                json.dumps(metadata.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._upload(
                manifest_path, manifest_name, "application/json",
                parent_id, video_id, "json"
            )

            link = uploaded.get("webViewLink", "")
            if self.make_shareable:
                link = self._make_shareable(uploaded["id"]) or link

        except Exception as exc:  # noqa: BLE001
            log.error("gdrive_upload_failed", video_id=video_id, error=str(exc))
            return PublishResult(
                success=False, video_id=video_id, destination="gdrive",
                error=str(exc),
            )

        log.info("gdrive_upload_ok", video_id=video_id, link=link)
        return PublishResult(
            success=True, video_id=video_id, destination="gdrive",
            remote_path=link, bytes_written=size,
        )

    def _make_shareable(self, file_id: str) -> Optional[str]:
        """Grant anyone-with-link read access. Opt-in only."""
        try:
            service = self._get_service()
            service.permissions().create(
                fileId=file_id, body={"role": "reader", "type": "anyone"},
            ).execute()
            return service.files().get(
                fileId=file_id, fields="webViewLink"
            ).execute().get("webViewLink")
        except Exception as exc:  # noqa: BLE001 - sharing is a bonus, not the job
            log.warning("gdrive_share_failed", file_id=file_id, error=str(exc))
            return None
