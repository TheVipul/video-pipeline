"""
Base Publisher interface.

Every publisher receives a video file and a metadata sidecar, and returns a
PublishResult. Implementations must be idempotent (re-publishing the same
video_id should overwrite, not duplicate).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pipeline.ai_analyzer import AIEnrichment


@dataclass
class PublishResult:
    success: bool
    video_id: str
    destination: str
    remote_path: str = ""
    bytes_written: int = 0
    error: str = ""


class Publisher(ABC):
    """Abstract base for re-publish destinations."""

    name: str = "base"

    @abstractmethod
    def publish(
        self,
        video_id: str,
        video_path: Path,
        metadata: AIEnrichment,
    ) -> PublishResult:
        """Publish one video + its metadata sidecar."""
        raise NotImplementedError
