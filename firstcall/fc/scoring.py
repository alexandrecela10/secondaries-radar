"""Pension score and verdict (PRD F-3, F-4). Pure functions, tested in tests/test_scoring.py.

score = 1 - exp(-S), S = sum(weight x confidence x 0.5 ** (age_days / half_life)).
Point in time (D-5): a score at T uses only signals published on or before T.
A sale that already closed is evidence only when the rulebook says so (v2): it adds nothing to S.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from .config import Rulebook, rulebook


@dataclass(frozen=True)
class SignalInput:
    signal_id: str
    signal_type: str
    confidence: float
    published_at: datetime
    completed: bool = False


@dataclass(frozen=True)
class Contribution:
    signal_id: str
    signal_type: str
    weight: float
    confidence: float
    age_days: float
    decay: float
    value: float


@dataclass(frozen=True)
class Score:
    as_of: datetime
    S: float
    score: float
    verdict: str
    contributions: tuple[Contribution, ...]


def inputs(signals: list[dict]) -> list[SignalInput]:
    """Gold signal rows to scoring inputs."""
    return [SignalInput(s["signal_id"], s["signal_type"], s["confidence"], s["published_at"],
                        bool(s.get("completed") or False)) for s in signals]


def decay(age_days: float, half_life_days: float) -> float:
    return 0.5 ** (age_days / half_life_days)


def contribution(sig: SignalInput, as_of: datetime, rb: Rulebook) -> Contribution | None:
    age = (as_of - sig.published_at).total_seconds() / 86400
    if age < 0 or age > rb.window_days:      # future (not yet known) or out of window
        return None
    if sig.completed and not rb.completed_sales_count:   # a closed sale: evidence, not a fresh signal
        return None
    w = rb.weights.get(sig.signal_type, 0.0)
    d = decay(age, rb.half_life_days)
    return Contribution(sig.signal_id, sig.signal_type, w, sig.confidence, age, d, w * sig.confidence * d)


def score(signals: list[SignalInput], as_of: datetime, rb: Rulebook | None = None) -> Score:
    rb = rb or rulebook()
    parts = [c for s in signals if (c := contribution(s, as_of, rb)) is not None]
    parts.sort(key=lambda c: (-c.value, c.signal_id))
    S = sum(c.value for c in parts)
    value = 1 - math.exp(-S)
    return Score(as_of, S, value, verdict(value, rb), tuple(parts))


def verdict(value: float, rb: Rulebook | None = None) -> str:
    rb = rb or rulebook()
    return "PREPARE" if value >= rb.prepare_threshold - 1e-12 else "WATCH"
