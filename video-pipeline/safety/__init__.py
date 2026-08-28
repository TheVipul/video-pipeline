"""
Safety railguards for the video pipeline.

Modules in this package enforce hard limits on:
    - input URLs (allowlist, duration cap)
    - cost (LLM spend circuit breaker)
    - content (brand safety, copyright)
    - network behavior (per-proxy rate limit)
    - audit (every action logged as JSONL)
    - environment (preflight checks)
    - LLM inputs (prompt injection sanitization)
"""
