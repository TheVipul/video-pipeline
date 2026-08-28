"""
S3-compatible publisher.

Works with AWS S3, Cloudflare R2, Backblaze B2, or local MinIO. Uses boto3
if available; gracefully no-ops if boto3 is not installed and the publisher
is invoked (caller can choose to ignore the failure or fall back to Local).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from logging_setup import get_logger
from pipeline.ai_analyzer import AIEnrichment
from pipeline.publishers.base import PublishResult, Publisher

log = get_logger(__name__)


class S3Publisher(Publisher):
    name = "s3"

    def __init__(
        self,
        bucket: str,
        prefix: str = "republished/",
        endpoint_url: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        region: str = "us-east-1",
    ) -> None:
        self.bucket = bucket
        self.prefix = prefix.rstrip("/") + "/"
        self.endpoint_url = endpoint_url
        self.region = region

        # Allow env override
        self.access_key = access_key or os.environ.get("S3_ACCESS_KEY") or os.environ.get("AWS_ACCESS_KEY_ID")
        self.secret_key = secret_key or os.environ.get("S3_SECRET_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY")

        self._client = None

    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        try:
            import boto3  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "boto3 is not installed. Run: pip install boto3"
            ) from exc

        kwargs = {
            "region_name": self.region,
        }
        if self.endpoint_url:
            kwargs["endpoint_url"] = self.endpoint_url
        if self.access_key and self.secret_key:
            kwargs["aws_access_key_id"] = self.access_key
            kwargs["aws_secret_access_key"] = self.secret_key
        self._client = boto3.client("s3", **kwargs)
        log.info("s3_client_ready", bucket=self.bucket, endpoint=self.endpoint_url)

    def publish(
        self,
        video_id: str,
        video_path: Path,
        metadata: AIEnrichment,
    ) -> PublishResult:
        try:
            self._ensure_client()
        except Exception as exc:
            log.error("s3_client_init_failed", error=str(exc))
            return PublishResult(
                success=False, video_id=video_id, destination="s3", error=str(exc)
            )

        key = f"{self.prefix}{video_id}.mp4"
        manifest_key = f"{self.prefix}{video_id}.json"
        try:
            self._client.upload_file(
                str(video_path), self.bucket, key,
                ExtraArgs={"ContentType": "video/mp4"},
            )
            body = json.dumps(metadata.to_dict(), ensure_ascii=False, indent=2).encode("utf-8")
            self._client.put_object(
                Bucket=self.bucket, Key=manifest_key, Body=body,
                ContentType="application/json",
            )
            size = video_path.stat().st_size
            log.info("s3_publish_ok", video_id=video_id, key=key, size=size)
            return PublishResult(
                success=True, video_id=video_id, destination="s3",
                remote_path=f"s3://{self.bucket}/{key}", bytes_written=size,
            )
        except Exception as exc:
            log.error("s3_publish_failed", video_id=video_id, error=str(exc))
            return PublishResult(
                success=False, video_id=video_id, destination="s3", error=str(exc)
            )
