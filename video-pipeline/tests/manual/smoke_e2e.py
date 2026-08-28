"""End-to-end test of one video through all stages."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import get_settings
from pipeline.metadata import fetch_metadata
from pipeline.downloader import download_video
from pipeline.transformer import transform_video
from pipeline.publishers.local import LocalPublisher
from pipeline.ai_analyzer import AIEnrichment
from safety.content_safety import SafetyVerdict
from safety.proxy_health import ProxyPool
from safety.input_validator import validate_url

settings = get_settings()
print(f"Settings: {settings.summary()}")
print()

# Test with a SHORT video - "Me at the zoo" is only 19s
url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
v = validate_url(url)
print(f"Video: {v.video_id} ({v.video_type})")
print()

# 1. Metadata
print("=== Stage 1: Metadata ===")
md = fetch_metadata(
    url=v.url,
    video_id=v.video_id,
    yt_settings=settings.youtube,
    proxy_pool=None,
    timeout_sec=30,
)
print(f"  Title: {md.title}")
print(f"  Duration: {md.duration_sec}s")
print(f"  Channel: {md.channel}")
print()

# 2. Download
print("=== Stage 3: Download ===")
output_dir = settings.pipeline.output_dir
downloads_dir = output_dir / "raw"
result = download_video(
    url=v.url,
    video_id=v.video_id,
    output_dir=downloads_dir,
    yt_settings=settings.youtube,
    safety_settings=settings.safety,
    proxy_pool=None,
    max_attempts=2,
    timeout_sec=120,
)
print(f"  Success: {result.success}")
print(f"  File: {result.file_path}")
print(f"  Size: {result.file_size_bytes} bytes")
print(f"  Attempts: {result.attempts}")
print(f"  Proxy: {result.proxy_used}")
print(f"  Client: {result.client_used}")
print(f"  Error: {result.last_error}")
print()

if not result.success:
    print("Download failed - cannot continue")
    sys.exit(1)

# 3. Transform
print("=== Stage 4: Transform ===")
transformed_dir = output_dir / "transformed"
transform_result = transform_video(
    input_path=result.file_path,
    output_path=transformed_dir / f"{v.video_id}.mp4",
    watermark_text="csc.video",
)
print(f"  Success: {transform_result.success}")
print(f"  Output: {transform_result.output_path}")
print(f"  Size: {transform_result.file_size_bytes} bytes")
print(f"  Duration: {transform_result.duration_sec}s")
print(f"  Error: {transform_result.error}")
print()

if not transform_result.success:
    print("Transform failed - cannot continue")
    sys.exit(1)

# 4. Publish
print("=== Stage 5: Publish ===")
publisher = LocalPublisher(output_dir=output_dir / "published")
enrichment = AIEnrichment(
    video_id=v.video_id,
    ai_title=md.title,
    ai_description=md.description[:500],
    ai_tags=md.tags[:5],
    ai_category="general",
    safety=SafetyVerdict(verdict="safe", confidence=0.7, concerns=[], source="rules"),
)
pub_result = publisher.publish(v.video_id, transform_result.output_path, enrichment)
print(f"  Success: {pub_result.success}")
print(f"  Destination: {pub_result.destination}")
print(f"  Remote path: {pub_result.remote_path}")
print(f"  Bytes: {pub_result.bytes_written}")
print()
print("=== END-TO-END TEST PASSED ===" if pub_result.success else "=== FAILED ===")
