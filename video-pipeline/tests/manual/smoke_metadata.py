"""Test single video metadata extraction (no download)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import get_settings
from pipeline.metadata import fetch_metadata
from safety.input_validator import validate_url

settings = get_settings()
print(f"Settings: {settings.summary()}")
print()

url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # "Me at the zoo" - first YouTube video, 19s
print(f"Testing: {url}")
v = validate_url(url)
print(f"Validated: video_id={v.video_id}, type={v.video_type}")
print()

try:
    md = fetch_metadata(
        url=v.url,
        video_id=v.video_id,
        yt_settings=settings.youtube,
        proxy_pool=None,
        timeout_sec=30,
    )
    print(f"SUCCESS")
    print(f"  Title: {md.title}")
    print(f"  Channel: {md.channel}")
    print(f"  Duration: {md.duration_sec}s")
    print(f"  Tags: {md.tags[:5]}")
    print(f"  Proxy used: {md.fetched_with}")
    print(f"  Elapsed: {md.fetch_duration_sec:.2f}s")
except Exception as exc:
    print(f"FAILED: {exc}")
    import traceback
    traceback.print_exc()
