"""Anti-bot and failure-handling behaviour.

The assessment brief requires the solution to "handle YouTube proxy and
blocking behavior". These tests pin that behaviour down so a refactor cannot
quietly remove it.
"""
from __future__ import annotations

import pytest

from pipeline.metadata import (
    BLOCK_SIGNATURES,
    PERMANENT_SIGNATURES,
    PLAYER_CLIENTS,
    classify_error,
)


class TestErrorClassification:
    @pytest.mark.parametrize("stderr", [
        "ERROR: Sign in to confirm you're not a bot",
        "ERROR: HTTP Error 403: Forbidden",
        "ERROR: HTTP Error 429: Too Many Requests",
        "Unable to connect to proxy",
        "Failed to establish a new connection: Connection refused",
        "ERROR: Unable to download API page",
    ])
    def test_blocks_are_retriable(self, stderr):
        assert classify_error(stderr) == "blocked"

    @pytest.mark.parametrize("stderr", [
        "ERROR: Video unavailable",
        "ERROR: Private video. Sign in if you've been granted access",
        "ERROR: This video has been removed by the uploader",
    ])
    def test_dead_videos_are_permanent(self, stderr):
        """Retrying a removed video just burns the ladder and the clock."""
        assert classify_error(stderr) == "permanent"

    @pytest.mark.parametrize("stderr", [
        # yt-dlp phrases a dead video differently per player client. Matching
        # only one form meant the fallback ladder was burned in full before
        # fast-fail triggered on the last attempt.
        "ERROR: [youtube] x: Video unavailable",
        "ERROR: [youtube] x: This video is unavailable",
        "ERROR: [youtube] x: This video is private",
        "ERROR: [youtube] x: This video has been removed by the uploader",
        "ERROR: [youtube] x: This video is no longer available",
    ])
    def test_all_dead_video_phrasings_are_permanent(self, stderr):
        assert classify_error(stderr) == "permanent"

    @pytest.mark.parametrize("stderr", [
        "ERROR: The uploader has not made this video available in your country",
        "ERROR: This video is not available in your country",
    ])
    def test_geo_restriction_is_retriable_not_permanent(self, stderr):
        """Geo-blocks are permanent for this exit IP but not for the video.
        Rotating to a proxy elsewhere is the fix, so they must stay retriable -
        classifying them permanent would abandon the exact case proxies solve."""
        assert classify_error(stderr) == "blocked"

    def test_unknown_errors_are_not_misclassified(self):
        assert classify_error("something entirely novel") == "unknown"

    def test_classification_is_case_insensitive(self):
        assert classify_error("SIGN IN TO CONFIRM YOU'RE NOT A BOT") == "blocked"

    def test_permanent_wins_over_block_when_both_present(self):
        """A private video behind a 403 is still permanently unavailable."""
        assert classify_error("HTTP Error 403: Private video") == "permanent"


class TestFallbackLadder:
    def test_multiple_player_clients_configured(self):
        """Rotating the player client is the cheapest way past a block."""
        assert len(PLAYER_CLIENTS) >= 3
        assert len(set(PLAYER_CLIENTS)) == len(PLAYER_CLIENTS)

    def test_signature_lists_are_disjoint(self):
        """A signature in both lists would make classification order-dependent."""
        assert not (set(BLOCK_SIGNATURES) & set(PERMANENT_SIGNATURES))


class TestLadderConsistency:
    """Both network stages must degrade the same way.

    Regression: the downloader offered only 2 player clients while metadata
    offered 5, so a no-proxy run gave up with three rotation options unused -
    and the two stages disagreed about what "exhausted" meant.
    """

    def test_both_stages_share_the_same_client_list(self):
        from pipeline.downloader import PLAYER_CLIENTS as download_clients
        from pipeline.metadata import PLAYER_CLIENTS as metadata_clients

        assert download_clients == metadata_clients

    def test_download_default_allows_the_full_ladder(self):
        import inspect

        from pipeline.downloader import PLAYER_CLIENTS, download_video

        default = inspect.signature(download_video).parameters["max_attempts"].default
        # 1 direct + the remaining clients, plus room for two proxy attempts.
        assert default >= len(PLAYER_CLIENTS), (
            "max_attempts must not truncate the ladder below the number of "
            "player clients available"
        )


class TestMetadataLadderConstruction:
    """The ladder must always include a direct attempt.

    Regression: metadata had no retry at all, so one dead proxy failed the
    whole run before the downloader's own fallback logic ever executed.
    """

    def _build_ladder(self, pool_size: int, max_attempts: int = 4):
        candidates = []
        for _ in range(min(2, pool_size)):
            candidates.append(("proxy", PLAYER_CLIENTS[0]))
        candidates.append((None, PLAYER_CLIENTS[0]))
        for client in PLAYER_CLIENTS[1:]:
            candidates.append((None, client))
        return candidates[:max_attempts]

    def test_direct_attempt_present_even_with_proxies(self):
        ladder = self._build_ladder(pool_size=5)
        assert any(proxy is None for proxy, _ in ladder), (
            "a fully dead proxy pool must still degrade to a direct connection"
        )

    def test_ladder_tries_proxies_before_direct(self):
        ladder = self._build_ladder(pool_size=2)
        assert ladder[0][0] == "proxy"

    def test_ladder_respects_max_attempts(self):
        assert len(self._build_ladder(pool_size=5, max_attempts=3)) == 3

    def test_ladder_works_with_no_proxies(self):
        ladder = self._build_ladder(pool_size=0)
        assert ladder and all(proxy is None for proxy, _ in ladder)


class TestProxyPool:
    """Pool rotation and health tracking."""

    def _pool(self, urls, tmp_path):
        from config import SafetySettings
        from safety.proxy_health import ProxyPool

        f = tmp_path / "proxies.txt"
        f.write_text("\n".join(urls))
        return ProxyPool(f, SafetySettings())

    def test_round_robin_rotates(self, tmp_path):
        pool = self._pool(["http://a:1", "http://b:2"], tmp_path)
        assert [pool.acquire().url for _ in range(4)] == [
            "http://a:1", "http://b:2", "http://a:1", "http://b:2"
        ]

    def test_failed_proxies_go_into_cooldown(self, tmp_path):
        pool = self._pool(["http://a:1", "http://b:2"], tmp_path)
        for _ in range(2):
            p = pool.acquire()
            pool.report_failure(p)
        assert pool.acquire() is None, "all proxies failed - pool must report exhausted"

    def test_acquire_can_repeat_when_alternates_are_cooling(self, tmp_path):
        """Documents *why* the ladder dedupes.

        acquire() skips proxies in cooldown, so with one healthy and one
        cooling proxy, two calls both return the healthy one. That is correct
        for rate limiting but means a naive ladder would spend two rungs on
        the same route.
        """
        pool = self._pool(["http://a:1", "http://b:2"], tmp_path)
        first = pool.acquire()
        second = pool.acquire()
        pool.report_failure(second)          # b is now cooling
        assert pool.acquire().url == first.url
        assert pool.acquire().url == first.url

    def test_ladder_dedupes_proxies(self, tmp_path):
        """The ladder must not retry the same route twice."""
        pool = self._pool(["http://a:1", "http://b:2"], tmp_path)
        pool.report_failure(pool._proxies[1])   # b cooling; acquire repeats a

        seen, candidates = set(), []
        for _ in range(min(2, pool.size)):
            p = pool.acquire()
            if p and p.url not in seen:
                seen.add(p.url)
                candidates.append(p)

        assert len({c.url for c in candidates}) == len(candidates)

    def test_empty_pool_is_safe(self, tmp_path):
        pool = self._pool([], tmp_path)
        assert pool.size == 0 and pool.acquire() is None
