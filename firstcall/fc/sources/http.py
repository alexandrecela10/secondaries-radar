"""Shared HTTP client with a polite user agent and a per-host minimum interval."""
from __future__ import annotations

import time

import httpx

UA = "FirstCall-research-prototype/0.1 (headline monitoring; contact: repo owner)"


class Throttle:
    """Keeps at least `min_interval` seconds between calls. Process-local."""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._last = 0.0

    def wait(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last = time.monotonic()


def client(timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(timeout=timeout, headers={"User-Agent": UA}, follow_redirects=True)
