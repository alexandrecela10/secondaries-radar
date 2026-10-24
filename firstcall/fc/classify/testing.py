"""Classifier wrappers for experiments: a slow one (backpressure) and a flaky one (dead-letter topic)."""
from __future__ import annotations

import time
import zlib

from ..models import Classification
from .base import ClassifierError
from .keyword import KeywordClassifier


class SlowClassifier(KeywordClassifier):
    """Simulates a model call that takes `delay` seconds."""

    name = "slow-keyword"

    def __init__(self, delay: float = 1.0):
        self.delay = delay

    def classify(self, headline: str, pension_name: str | None = None) -> Classification:
        time.sleep(self.delay)
        return super().classify(headline, pension_name)


class FlakyClassifier(KeywordClassifier):
    """Fails on a deterministic share of headlines, like a timeout or a schema-check failure."""

    name = "keyword"     # same name as the good adapter, so redriven rows match the reference

    def __init__(self, fail_every: int = 7):
        self.fail_every = fail_every

    def classify(self, headline: str, pension_name: str | None = None) -> Classification:
        if zlib.crc32(headline.encode()) % self.fail_every == 0:
            raise ClassifierError("simulated timeout")
        return super().classify(headline, pension_name)
