"""Synthetic data: invented managers, funds, series, holdings, offers."""
from __future__ import annotations

from pathlib import Path

from .offers import generate_offers
from .universe import generate, write_all


def run(data_dir: Path, seed: int = 42) -> dict:
    data = generate(seed=seed)
    write_all(data, data_dir)
    labels = generate_offers(data, data_dir / "offers", data_dir / "fixtures" / "labels.json", seed=seed)
    return dict(funds=len(data["funds"]), series_rows=len(data["fund_series"]),
                holdings=len(data["holdings"]), offers=len(labels))
