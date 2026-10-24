"""Paths and rulebook loading. One place for every setting."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
BRONZE_DIR = DATA_DIR / "bronze"
FIXTURES_DIR = ROOT / "fixtures"
MEASURE_DIR = ROOT / "measurements"
REPORTS_DIR = ROOT / "reports"

DEFAULT_DB = Path(os.environ.get("FC_DB", DATA_DIR / "firstcall.duckdb"))
KAFKA_BOOTSTRAP = os.environ.get("FC_KAFKA", "localhost:19092")
MODEL = "claude-sonnet-5-5"

# Source cadence. This caps freshness for both paths.
GDELT_UPDATE_MINUTES = 15          # GDELT DOC index refreshes every 15 minutes
GDELT_MIN_INTERVAL_S = 5.0         # GDELT rule: one request every 5 seconds
RSS_POLL_MINUTES = 15              # our poll interval for Google News RSS


@dataclass(frozen=True)
class Rulebook:
    version: int
    prepare_threshold: float
    half_life_days: float
    window_days: int
    story_window_days: int
    weights: dict[str, float]
    descriptions: dict[str, str]


VALID_SIGNALS = (
    "sale_or_advisor",
    "allocation_above_target",
    "liquidity_need",
    "policy_review",
    "leadership_change",
)


def load_rulebook(path: Path | None = None) -> Rulebook:
    raw = yaml.safe_load((path or CONFIG_DIR / "rulebook.yaml").read_text())
    fc = raw["first_call"]
    weights = {k: float(v) for k, v in fc["signal_weights"].items()}
    unknown = set(weights) - set(VALID_SIGNALS)
    if unknown:
        raise ValueError(f"Unknown signal types in rulebook: {sorted(unknown)}. Valid: {VALID_SIGNALS}")
    return Rulebook(
        version=int(raw["version"]),
        prepare_threshold=float(fc["prepare_threshold"]),
        half_life_days=float(fc["half_life_days"]),
        window_days=int(fc["window_days"]),
        story_window_days=int(fc["story_window_days"]),
        weights=weights,
        descriptions=fc.get("descriptions", {}),
    )


@lru_cache(maxsize=1)
def rulebook() -> Rulebook:
    return load_rulebook()


def load_queries() -> dict[str, list[str]]:
    return yaml.safe_load((CONFIG_DIR / "queries.yaml").read_text())
