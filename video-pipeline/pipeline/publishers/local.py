"""
Local publisher - writes transformed videos + JSON metadata sidecars to disk.

This is the default and always-works destination. The agent uses it for demos
and as a fallback if S3/YouTube publishers fail.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from logging_setup import get_logger
from pipeline.ai_analyzer import AIEnrichment
from pipeline.publishers.base import PublishResult, Publisher

log = get_logger(__name__)


class LocalPublisher(Publisher):
    name = "local"

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.videos_dir = self.output_dir / "videos"
        self.manifests_dir = self.output_dir / "manifests"
        self.videos_dir.mkdir(parents=True, exist_ok=True)
        self.manifests_dir.mkdir(parents=True, exist_ok=True)

    def publish(
        self,
        video_id: str,
        video_path: Path,
        metadata: AIEnrichment,
    ) -> PublishResult:
        try:
            dest_video = self.videos_dir / f"{video_id}.mp4"
            dest_manifest = self.manifests_dir / f"{video_id}.json"
            shutil.copy2(video_path, dest_video)
            dest_manifest.write_text(
                json.dumps(metadata.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            size = dest_video.stat().st_size
            log.info(
                "publish_local_ok",
                video_id=video_id,
                video=str(dest_video),
                manifest=str(dest_manifest),
                size=size,
            )
            return PublishResult(
                success=True, video_id=video_id,
                destination="local",
                remote_path=str(dest_video),
                bytes_written=size,
            )
        except Exception as exc:
            log.error("publish_local_failed", video_id=video_id, error=str(exc))
            return PublishResult(
                success=False, video_id=video_id,
                destination="local", error=str(exc),
            )
