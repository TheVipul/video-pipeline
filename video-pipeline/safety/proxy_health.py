"""
Per-proxy health + rate limit tracking.

Each proxy gets:
    - A counter of requests in the last 60 seconds
    - A counter of consecutive failures
    - A "cooldown" timer that blocks new requests for a short period after a failure

When a proxy's failure rate is too high, it's marked unhealthy and the pool skips it.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config import SafetySettings
from logging_setup import get_logger

log = get_logger(__name__)


@dataclass
class ProxyState:
    url: str
    requests_in_window: deque = field(default_factory=deque)  # timestamps
    consecutive_failures: int = 0
    total_failures: int = 0
    total_requests: int = 0
    cooldown_until: float = 0.0
    is_healthy: bool = True

    def is_available(self, now: float, rpm_limit: int) -> bool:
        if not self.is_healthy:
            return False
        if now < self.cooldown_until:
            return False
        # Drop old timestamps
        cutoff = now - 60.0
        while self.requests_in_window and self.requests_in_window[0] < cutoff:
            self.requests_in_window.popleft()
        return len(self.requests_in_window) < rpm_limit


class ProxyPool:
    """
    Thread-safe proxy pool with rate limit and health tracking.

    Reads proxies from a file (one per line, # comments) and provides a
    round-robin `acquire()` that respects per-proxy rate limits.
    """

    def __init__(self, proxy_file: Optional[Path], settings: SafetySettings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._proxies: list[ProxyState] = []
        self._index = 0
        if proxy_file and proxy_file.exists():
            self._load(proxy_file)
        log.info("proxy_pool_initialized", count=len(self._proxies))

    def _load(self, path: Path) -> None:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                self._proxies.append(ProxyState(url=line))

    @property
    def size(self) -> int:
        return len(self._proxies)

    def acquire(self) -> Optional[ProxyState]:
        """Round-robin acquire, skipping unavailable proxies. Returns None if pool empty."""
        with self._lock:
            if not self._proxies:
                return None
            now = time.time()
            for _ in range(len(self._proxies)):
                p = self._proxies[self._index]
                self._index = (self._index + 1) % len(self._proxies)
                if p.is_available(now, self.settings.proxy_requests_per_minute):
                    p.requests_in_window.append(now)
                    p.total_requests += 1
                    return p
            log.warning("proxy_pool_exhausted", pool_size=len(self._proxies))
            return None

    def report_success(self, proxy: ProxyState) -> None:
        with self._lock:
            proxy.consecutive_failures = 0

    def report_failure(self, proxy: ProxyState, cooldown_sec: float = 30.0) -> None:
        with self._lock:
            proxy.consecutive_failures += 1
            proxy.total_failures += 1
            proxy.cooldown_until = time.time() + cooldown_sec
            if proxy.consecutive_failures >= 3:
                proxy.is_healthy = False
                log.warning("proxy_marked_unhealthy", proxy=proxy.url)

    def stats(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "url": p.url,
                    "total_requests": p.total_requests,
                    "total_failures": p.total_failures,
                    "consecutive_failures": p.consecutive_failures,
                    "is_healthy": p.is_healthy,
                    "in_window": len(p.requests_in_window),
                }
                for p in self._proxies
            ]
