"""
Configuration loader for the Video Pipeline.

All settings are read from environment variables (via .env) using pydantic-settings.
The pipeline will run in a degraded mode (no AI enrichment) if LLM_API_KEY is unset.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LLM_", env_file=str(PROJECT_ROOT / ".env"), extra="ignore")
    base_url: str = "https://api.MiniMax.chat/v1"
    api_key: Optional[str] = None
    model: str = "MiniMax-Text-01"
    timeout_sec: float = 30.0
    max_retries: int = 2


class YouTubeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="YT_", env_file=str(PROJECT_ROOT / ".env"), extra="ignore")
    cookies_file: Optional[Path] = None
    cookies_from_browser: Optional[str] = None  # chrome | firefox | edge | safari


class ProxySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=str(PROJECT_ROOT / ".env"), extra="ignore")
    proxy_file: Optional[Path] = None


class PipelineSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PIPELINE_", env_file=str(PROJECT_ROOT / ".env"), extra="ignore")
    brand: str = "generic"
    output_dir: Path = PROJECT_ROOT / "outputs"
    max_videos: int = 5
    max_duration_sec: int = 600
    dry_run: bool = False

    # "general" | "brand".
    #
    # Watermarking and brand-relevance filtering only make sense when
    # publishing on behalf of a specific brand. In general mode they produce
    # confusing output - a watermark for a brand that does not exist, and
    # videos held for review for not matching a brand nobody configured - so
    # the setup wizard switches them off together.
    #
    # They remain separately overridable for anyone who wants, say, a
    # watermark without brand filtering.
    mode: str = "general"
    enable_watermark: bool = False
    enable_relevance_gate: bool = False

    @property
    def is_brand_mode(self) -> bool:
        return self.mode.lower() == "brand"


class SafetySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SAFETY_", env_file=str(PROJECT_ROOT / ".env"), extra="ignore")
    max_llm_spend_usd: float = 1.50
    disk_min_free_gb: float = 2.0
    max_consecutive_failures: int = 3
    circuit_breaker_cooldown_sec: int = 60
    download_min_interval_sec: float = 2.0
    download_max_interval_sec: float = 8.0
    proxy_requests_per_minute: int = 10


class Settings:
    """Container for all settings groups."""

    def __init__(self) -> None:
        self.llm = LLMSettings()
        self.youtube = YouTubeSettings()
        self.proxy = ProxySettings()
        self.pipeline = PipelineSettings()
        self.safety = SafetySettings()
        self.project_root = PROJECT_ROOT

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm.api_key) and self.llm.api_key != "your_minimax_api_key_here"

    def summary(self) -> dict:
        return {
            "llm_enabled": self.llm_enabled,
            "llm_model": self.llm.model if self.llm_enabled else "(disabled - no key)",
            "brand": self.pipeline.brand,
            "max_videos": self.pipeline.max_videos,
            "max_duration_sec": self.pipeline.max_duration_sec,
            "dry_run": self.pipeline.dry_run,
            "cookies_configured": bool(self.youtube.cookies_file or self.youtube.cookies_from_browser),
            "proxy_file": str(self.proxy.proxy_file) if self.proxy.proxy_file else "(none)",
            "output_dir": str(self.pipeline.output_dir),
        }


def get_settings() -> Settings:
    return Settings()
