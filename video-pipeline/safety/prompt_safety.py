"""
LLM prompt injection sanitization.

When sending user-derived text (YouTube titles, descriptions, tags) to an LLM,
we must treat them as untrusted. The strings may contain:
    - Instructions to override the system prompt
    - Delimiters that confuse chat templates
    - Very long payloads that bloat the cost guard

This module strips / neutralizes the obvious patterns before they reach the LLM.
"""
from __future__ import annotations

import re
from typing import Optional

from logging_setup import get_logger

log = get_logger(__name__)

# Patterns that try to break out of a "user content" section into a new instruction
INJECTION_PATTERNS = [
    re.compile(r"ignore (all )?(previous|prior|above) instructions", re.IGNORECASE),
    re.compile(r"disregard (all )?(previous|prior|above)", re.IGNORECASE),
    re.compile(r"you are now", re.IGNORECASE),
    re.compile(r"system\s*:\s*", re.IGNORECASE),
    re.compile(r"assistant\s*:\s*", re.IGNORECASE),
    re.compile(r"<\s*\|?\s*system\s*\|?\s*>", re.IGNORECASE),
    re.compile(r"<\s*\|?\s*assistant\s*\|?\s*>", re.IGNORECASE),
    re.compile(r"\[\s*INST\s*\]", re.IGNORECASE),
    re.compile(r"<<\s*SYS\s*>>", re.IGNORECASE),
]

# Replace these delimiters that could confuse chat templates
DELIMITER_CHARS = ["<|im_start|>", "<|im_end|>", "```system", "<system>", "</system>"]


def sanitize_text(text: str, max_len: int = 4000) -> str:
    """
    Sanitize user-derived text before sending to LLM.

    - Caps length to bound cost and protect against context-stuffing
    - Replaces obvious prompt-injection phrases
    - Escapes chat-template delimiters
    """
    if not text:
        return ""

    # Length cap
    if len(text) > max_len:
        text = text[:max_len] + "..."

    # Replace injection patterns
    for pat in INJECTION_PATTERNS:
        text = pat.sub("[REDACTED]", text)

    # Escape template delimiters
    for delim in DELIMITER_CHARS:
        text = text.replace(delim, "[REDACTED]")

    return text.strip()


def sanitize_metadata(
    title: str = "",
    description: str = "",
    tags: Optional[list[str]] = None,
) -> dict:
    """Sanitize a metadata dict, returning clean fields ready for LLM context."""
    tags = tags or []
    return {
        "title": sanitize_text(title, max_len=200),
        "description": sanitize_text(description, max_len=1500),
        "tags": [sanitize_text(t, max_len=50) for t in tags[:20]],
    }
