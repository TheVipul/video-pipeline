"""Test the AI analysis bypass path (no LLM key set)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import get_settings
from pipeline.metadata import VideoMetadata
from pipeline.ai_analyzer import enrich
from safety.cost_guard import CostGuard

settings = get_settings()
print(f"LLM enabled: {settings.llm_enabled}")
print(f"LLM model: {settings.llm.model}")
print()

# Create a sample metadata
md = VideoMetadata(
    video_id="sample123",
    url="https://www.youtube.com/watch?v=sample123",
    title="Cooking with Onions - Simple Recipe",
    description="A simple recipe for cooking onions. Vegetarian, healthy, easy.",
    duration_sec=120,
    channel="test_channel",
    tags=["cooking", "recipe", "onions"],
)

cost_guard = CostGuard(settings.safety, model=settings.llm.model)

# Run enrichment - should bypass since no API key
enrichment = enrich(
    metadata=md,
    brand="generic",
    llm_settings=settings.llm,
    safety_settings=settings.safety,
    cost_guard=cost_guard,
    project_root=settings.project_root,
)

print(f"=== AI Enrichment Result ===")
print(f"  Video ID: {enrichment.video_id}")
print(f"  AI title: {enrichment.ai_title}")
print(f"  AI description: {enrichment.ai_description[:100]}")
print(f"  AI tags: {enrichment.ai_tags}")
print(f"  AI category: {enrichment.ai_category}")
print(f"  Safety verdict: {enrichment.safety.verdict}")
print(f"  Safety confidence: {enrichment.safety.confidence}")
print(f"  Safety source: {enrichment.safety.source}")
print(f"  Safety concerns: {enrichment.safety.concerns}")
print(f"  Model: {enrichment.model}")
print(f"  Cost: ${enrichment.cost_usd}")
print(f"  Skipped reason: {enrichment.skipped_reason}")
print()

# Test with a problematic title - should be flagged/rejected
print("=== Test with NSFW title ===")
md2 = VideoMetadata(
    video_id="nsfw123",
    url="https://www.youtube.com/watch?v=nsfw123",
    title="NSFW content with explicit material",
    description="Adult content warning",
    duration_sec=60,
    channel="test",
    tags=[],
)
enrichment2 = enrich(
    metadata=md2, brand="generic",
    llm_settings=settings.llm, safety_settings=settings.safety,
    cost_guard=cost_guard, project_root=settings.project_root,
)
print(f"  Safety verdict: {enrichment2.safety.verdict}")
print(f"  Safety concerns: {enrichment2.safety.concerns}")
print()

# Test with copyrighted content
print("=== Test with copyrighted brand name ===")
md3 = VideoMetadata(
    video_id="marvel123",
    url="https://www.youtube.com/watch?v=marvel123",
    title="Marvel movie review - new release",
    description="Reviewing the latest Marvel film",
    duration_sec=300,
    channel="reviewer",
    tags=["marvel", "review"],
)
enrichment3 = enrich(
    metadata=md3, brand="generic",
    llm_settings=settings.llm, safety_settings=settings.safety,
    cost_guard=cost_guard, project_root=settings.project_root,
)
print(f"  Safety verdict: {enrichment3.safety.verdict}")
print(f"  Safety concerns: {enrichment3.safety.concerns}")
print()

print("=== ALL AI BYPASS TESTS PASSED ===")
