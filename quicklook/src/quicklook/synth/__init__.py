"""Synthetic data: invented managers, funds, series, holdings, offers."""
from __future__ import annotations

from pathlib import Path

from .events import generate_events
from .offers import generate_offers
from .universe import generate, write_all, write_parquet


def run(data_dir: Path, seed: int = 42) -> dict:
    data = generate(seed=seed)
    write_all(data, data_dir)
    labels = generate_offers(data, data_dir / "offers", data_dir / "fixtures" / "labels.json", seed=seed)
    ev = generate_events(data, labels, seed=seed)
    write_parquet(ev["fund_events"], data_dir / "reference" / "fund_events.parquet")
    write_parquet(ev["fund_next"], data_dir / "market" / "fund_next.parquet")
    return dict(funds=len(data["funds"]), series_rows=len(data["fund_series"]),
                holdings=len(data["holdings"]), offers=len(labels), events=len(ev["fund_events"]))
