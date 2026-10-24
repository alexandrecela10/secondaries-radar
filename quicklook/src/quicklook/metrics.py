"""Governed metrics. Pure functions over plain inputs, each with a version.

Nothing here reads a database or calls a model, so every function is tested on
fixtures with hand-computed answers (tests/test_metrics.py).
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from .models import MetricValue, Tier

# name -> (version, plain description). The rulebook may only use these.
REGISTRY: dict[str, tuple[str, str]] = {
    "total_value_usd": ("v1", "Total value of the offer in USD millions"),
    "count_lines": ("v1", "Number of fund lines"),
    "count_same_fund": ("v1", "Lines matched to a fund we own"),
    "count_unresolved": ("v1", "Lines we could not settle"),
    "share_value_same_fund": ("v1", "Share of value in funds we own"),
    "share_value_same_manager": ("v1", "Share of value with a manager we know, in a fund we do not own"),
    "share_value_unknown": ("v1", "Share of value we do not know"),
    "share_value_known_manager": ("v1", "Share of value with managers we already know"),
    "share_value_unresolved": ("v1", "Share of value in lines we could not settle"),
    "data_matched": ("v1", "Share of value matched at 0.8 or more, or settled as one manager or as unknown"),
    "share_value_venture": ("v1", "Share of value in venture funds"),
    "top5_concentration": ("v1", "Share of value in the 5 largest lines"),
    "fit_score": ("v1", "The fit of the strategy, region and vintage mix to our plan (0 to 1)"),
    "price_range_low": ("v1", "Low end of our price range for funds we own, as % of value"),
    "price_range_high": ("v1", "High end of our price range for funds we own, as % of value"),
    "asking_price_mid": ("v1", "Midpoint of the broker's pricing guidance, as % of value"),
    "guidance_minus_range_top": ("v1", "Asking price minus the top of our range"),
}
LINE_PRICE_VERSION = "v1"
EXPOSURE_VERSION = "v1"


@dataclass
class LineFact:
    """What the metrics need about one matched line. Values in USD millions."""

    line_id: str
    value_usd: float
    strategy: str | None
    region: str | None
    vintage: int | None
    tier: Tier
    resolved: bool
    match_confidence: float | None = None
    price_low: float | None = None
    price_high: float | None = None


def mv(name: str, value: float | None, note: str = "") -> MetricValue:
    return MetricValue(name=name, version=REGISTRY[name][0], value=value, note=note)


def _total(lines: list[LineFact]) -> float:
    return sum(l.value_usd for l in lines)


def share(lines: list[LineFact], pred) -> float | None:
    t = _total(lines)
    if t <= 0:
        return None
    return sum(l.value_usd for l in lines if pred(l)) / t


def top5_concentration(lines: list[LineFact]) -> float | None:
    t = _total(lines)
    if t <= 0:
        return None
    return sum(sorted((l.value_usd for l in lines), reverse=True)[:5]) / t


def vintage_band(v: int | None) -> str | None:
    if v is None:
        return None
    if v <= 2012:
        return "2008-2012"
    if v <= 2017:
        return "2013-2017"
    return "2018-2024"


def _mix(lines: list[LineFact], attr) -> dict[str, float] | None:
    known = [l for l in lines if attr(l) is not None]
    t = _total(known)
    if t <= 0:
        return None
    out: dict[str, float] = {}
    for l in known:
        out[attr(l)] = out.get(attr(l), 0.0) + l.value_usd / t
    return out


def one_minus_tvd(mix: dict[str, float], target: dict[str, float]) -> float:
    """1 - total variation distance between two mixes. 1 means identical."""
    keys = sorted(set(mix) | set(target))  # fixed order: float sums must not depend on hash seed
    return 1 - 0.5 * sum(abs(mix.get(k, 0.0) - target.get(k, 0.0)) for k in keys)


def fit_score(lines: list[LineFact], plan: dict) -> float | None:
    """Weighted fit to the plan: strategy, region and vintage band mixes by value."""
    parts = {"strategy": _mix(lines, lambda l: l.strategy), "region": _mix(lines, lambda l: l.region),
             "vintage_band": _mix(lines, lambda l: vintage_band(l.vintage))}
    w = plan["weights"]
    num = den = 0.0
    for k, mix in parts.items():
        if mix is None:
            continue
        num += w[k] * one_minus_tvd(mix, plan["targets"][k])
        den += w[k]
    return num / den if den else None


# Price bands as % of value, by strategy and fund age at the reference date. A heuristic:
# a real desk calibrates these on its own bids and on market reports.
PRICE_BANDS = {
    "buyout": [(5, 0.90, 0.97), (10, 0.84, 0.90), (99, 0.72, 0.84)],
    "growth": [(5, 0.85, 0.93), (10, 0.80, 0.86), (99, 0.65, 0.78)],
    "venture": [(5, 0.70, 0.85), (10, 0.65, 0.80), (99, 0.50, 0.70)],
    "credit": [(5, 0.94, 0.99), (10, 0.90, 0.96), (99, 0.80, 0.90)],
    "infra": [(5, 0.90, 0.97), (10, 0.86, 0.93), (99, 0.75, 0.86)],
}


def fund_age(vintage: int, on: dt.date) -> float:
    return (on - dt.date(vintage, 1, 1)).days / 365.25


def line_price_range(strategy: str, vintage: int, reference_date: dt.date, stated_value: float,
                     implied_value: float) -> tuple[float, float, str]:
    """Price range for a line in a fund we own, as % of the broker's stated value.

    implied_value = line commitment x (fund value / fund size) from our latest report.
    If our report says the line is worth more than the broker states, our range rises.
    """
    age = fund_age(vintage, reference_date)
    lo, hi = next((lo, hi) for top, lo, hi in PRICE_BANDS[strategy] if age < top)
    adj = implied_value / stated_value if stated_value else 1.0
    method = (f"{strategy}, age {age:.1f} years: band {lo:.0%} to {hi:.0%} of value, "
              f"times our value / broker value = {adj:.3f}")
    return lo * adj, hi * adj, method


def priced_range(lines: list[LineFact]) -> tuple[float | None, float | None]:
    priced = [l for l in lines if l.tier == Tier.SAME_FUND and l.price_low is not None]
    t = _total(priced)
    if t <= 0:
        return None, None
    return (sum(l.price_low * l.value_usd for l in priced) / t,
            sum(l.price_high * l.value_usd for l in priced) / t)


WORD_RANGE = {"low": (1, 3), "mid": (4, 6), "high": (7, 9)}


def asking_price_range(text: str | None) -> tuple[float, float] | None:
    """'high 80s' -> (0.87, 0.89); '92%' -> (0.92, 0.92); 'par' -> (1, 1); '85-88%' -> (0.85, 0.88)."""
    if not text:
        return None
    t = text.lower().strip()
    if re.search(r"\bpar\b", t) and not re.search(r"\d", t):
        return 1.0, 1.0
    m = re.search(r"\b(low|mid|high)[- ]?(\d0)s\b", t)
    if m:
        a, b = WORD_RANGE[m[1]]
        base = int(m[2])
        return (base + a) / 100, (base + b) / 100
    m = re.search(r"\b(\d0)s\b", t)
    if m:
        return int(m[1]) / 100, (int(m[1]) + 9) / 100
    m = re.search(r"(\d{2,3}(?:\.\d+)?)\s*(?:-|to|–)\s*(\d{2,3}(?:\.\d+)?)\s*%?", t)
    if m:
        return float(m[1]) / 100, float(m[2]) / 100
    m = re.search(r"(\d{2,3}(?:\.\d+)?)\s*%", t)
    if m:
        return float(m[1]) / 100, float(m[1]) / 100
    return None


def offer_metrics(lines: list[LineFact], asking_text: str | None, plan: dict) -> dict[str, MetricValue]:
    known = {Tier.SAME_FUND, Tier.SAME_MANAGER}
    lo, hi = priced_range(lines)
    ask = asking_price_range(asking_text)
    ask_mid = (ask[0] + ask[1]) / 2 if ask else None
    out = [
        mv("total_value_usd", _total(lines)),
        mv("count_lines", float(len(lines))),
        mv("count_same_fund", float(sum(1 for l in lines if l.tier == Tier.SAME_FUND))),
        mv("count_unresolved", float(sum(1 for l in lines if not l.resolved))),
        mv("share_value_same_fund", share(lines, lambda l: l.tier == Tier.SAME_FUND)),
        mv("share_value_same_manager", share(lines, lambda l: l.tier == Tier.SAME_MANAGER)),
        mv("share_value_unknown", share(lines, lambda l: l.tier == Tier.UNKNOWN)),
        mv("share_value_known_manager", share(lines, lambda l: l.tier in known)),
        mv("share_value_unresolved", share(lines, lambda l: not l.resolved)),
        mv("data_matched", share(lines, lambda l: l.resolved)),
        mv("share_value_venture", share(lines, lambda l: l.strategy == "venture")),
        mv("top5_concentration", top5_concentration(lines)),
        mv("fit_score", fit_score(lines, plan)),
        mv("price_range_low", lo, "no fund we own in this offer" if lo is None else ""),
        mv("price_range_high", hi, "no fund we own in this offer" if hi is None else ""),
        mv("asking_price_mid", ask_mid, "" if ask else "no pricing guidance read"),
        mv("guidance_minus_range_top", None if (ask_mid is None or hi is None) else ask_mid - hi,
           "no price: needs a fund we own and a pricing guidance" if (ask_mid is None or hi is None) else ""),
    ]
    return {m.name: m for m in out}
